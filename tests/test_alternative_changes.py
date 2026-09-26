import importlib.util
import json
import re
import sqlite3
import subprocess
import os
import sys
import tempfile
from contextlib import closing
import unittest
from unittest.mock import patch
from pathlib import Path

from database import crear_esquema
from alternative_change_workflow import create_proposal, review_proposal
from alternative_morphology import create_or_replace_alternative_morphology
from work_assignments import concept_diagnostics

ROOT = Path(__file__).resolve().parents[1]


class AlternativeChangeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        crear_esquema(self.db)
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Ana')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('Concepto')")
        self.db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(1,?)', [('1a',), ('2b',)])
        self.db.commit()
        self.addCleanup(self.db.close)

    def propose(self, kind='MORPHOLOGY', values=None):
        return create_proposal(self.db, 2, kind, values or {'component_count': 1}, collaborator_id=1, access_role='analyst')

    def review(self, sid, decision='accepted', role='reviewer', **kwargs):
        return review_proposal(self.db, sid, decision, collaborator_id=1, access_role=role, note='Revisado', **kwargs)

    def canonical(self):
        return {t: [tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in
                ('alternative','alternative_morphology','alternative_component','alternative_relation')}

    def test_morphology_partial_history_and_diagnostic(self):
        old, _ = create_or_replace_alternative_morphology(self.db, 2, component_count=1)
        before = self.canonical()
        sid = self.propose(values={'component_count': 4, 'free_permutation': 'NO',
            'components': [{'position': 2, 'component_alternative_id': 1}]})
        self.assertEqual(before, self.canonical())
        mid = self.review(sid)
        row = self.db.execute('SELECT * FROM alternative_morphology WHERE alternative_morphology_id=?', (mid,)).fetchone()
        self.assertEqual((row['component_count'], row['supersedes_alternative_morphology_id'],row['created_from_submission_id']), (4,old,sid))
        self.assertEqual(self.db.execute('SELECT is_current FROM alternative_morphology WHERE alternative_morphology_id=?',(old,)).fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM alternative_component').fetchone()[0], 1)
        self.assertNotIn(2, [r['alternative_id'] for r in concept_diagnostics(self.db,[1])[1]['morphology']])

    def test_simple_and_rejection_and_roles(self):
        sid=self.propose(); before=self.canonical()
        with self.assertRaises(ValueError): self.review(sid, role='analyst')
        self.assertEqual(before,self.canonical())
        self.review(sid,'rejected');self.assertEqual(before,self.canonical())
        sid=self.propose();self.review(sid,role='master')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM alternative_component').fetchone()[0],0)
        with self.assertRaises(ValueError): self.review(sid)

    def test_relations_symmetry_duplicates_and_diagnostic(self):
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,is_current) VALUES(1,2,'CM_1',0)")
        self.db.commit()
        self.assertEqual(len(concept_diagnostics(self.db,[1])[1]['relations']),1)
        before=self.canonical();sid=self.propose('RELATION',{'target_id':1,'parameter':'CM_1'})
        self.assertEqual(before,self.canonical());self.review(sid, relations_resolution='ACCEPTED')
        rows=self.db.execute('SELECT * FROM alternative_relation WHERE is_current=1').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual((rows[0]['alternative_low_id'],rows[0]['alternative_high_id']),(1,2))
        self.assertEqual(concept_diagnostics(self.db,[1])[1]['relations'],[])
        for target in (1,2):
            with self.assertRaises(ValueError):self.propose('RELATION',{'target_id':target,'parameter':'CM_1'})

    def test_stale_and_invalid_target(self):
        sid=self.propose()
        create_or_replace_alternative_morphology(self.db,2,component_count_not_applicable=True)
        before=self.canonical()
        with self.assertRaises(ValueError):self.review(sid)
        self.assertEqual(before,self.canonical())
        self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1")
        self.db.commit()
        with self.assertRaises(ValueError):self.propose('RELATION',{'target_id':1,'parameter':'CM_1'})

    def test_pending_morphology_blocks_duplicate_and_rejection_reopens(self):
        sid=self.propose()
        before=list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError,'revisión'):self.propose(values={'component_count':2,'free_permutation':'NO'})
        self.assertEqual(before,list(self.db.iterdump()))
        item=next(r for r in concept_diagnostics(self.db,[1])[1]['morphology'] if r['alternative_id']==2)
        self.assertEqual([r['submission_id'] for r in item['pending_changes']['MORPHOLOGY']],[sid])
        self.review(sid,'rejected')
        sid=self.propose();self.review(sid)
        self.assertNotIn(2,[r['alternative_id'] for r in concept_diagnostics(self.db,[1])[1]['morphology']])

    def test_pending_relation_duplicate_is_symmetric(self):
        sid=self.propose('RELATION',{'target_id':1,'parameter':'CM_1'})
        with self.assertRaisesRegex(ValueError,'revisión'):
            create_proposal(self.db,1,'RELATION',{'target_id':2,'parameter':'CM_1'},collaborator_id=1,access_role='analyst')
        with self.assertRaisesRegex(ValueError,'revisión'):self.propose('RELATION',{'target_id':1,'parameter':'CM_1'})
        from alternative_change_workflow import pending_changes
        pending=pending_changes(self.db,[1,2])
        self.assertEqual(pending[1]['RELATION'][0]['submission_id'],sid)
        self.assertEqual(pending[2]['RELATION'][0]['submission_id'],sid)
        other=self.propose('RELATION',{'target_id':1,'parameter':'OR_M1'})
        self.assertNotEqual(sid,other)
        self.review(sid, relations_resolution='ACCEPTED');self.review(other,'rejected')

    def test_relation_batch_is_atomic_and_independently_reviewed(self):
        from alternative_change_workflow import create_relation_proposals, baseline
        actor=dict(collaborator_id=1,access_role='analyst')
        values=[{'target_id':1,'parameter':'CM_1'},{'target_id':1,'parameter':'OR_M1'}]
        before=list(self.db.iterdump())
        for invalid in ([values[0],values[0]], [values[0],{'target_id':2,'parameter':'CM_1'}], []):
            with self.assertRaises(ValueError):create_relation_proposals(self.db,2,invalid,**actor)
            self.assertEqual(before,list(self.db.iterdump()))
        ids=create_relation_proposals(self.db,2,values,**actor)
        self.assertEqual(len(set(ids)),2)
        stale=baseline(self.db,2,'RELATION')
        self.review(ids[0], relations_resolution='ACCEPTED')
        before=list(self.db.iterdump())
        with self.assertRaises(ValueError):
            review_proposal(self.db,ids[1],'accepted',collaborator_id=1,access_role='reviewer',expected_baseline=stale,relations_resolution='ACCEPTED')
        self.assertEqual(before,list(self.db.iterdump()))
        review_proposal(self.db,ids[1],'accepted',collaborator_id=1,access_role='reviewer',expected_baseline=baseline(self.db,2,'RELATION'),relations_resolution='ACCEPTED')
        rows=self.db.execute('SELECT alternative_low_id,alternative_high_id FROM alternative_relation WHERE is_current=1').fetchall()
        self.assertEqual([tuple(r) for r in rows],[(1,2),(1,2)])

    def test_two_connections_cannot_create_duplicate_morphology(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'concurrent.db'
            with closing(sqlite3.connect(path)) as copy:self.db.backup(copy)
            def propose():
                with closing(sqlite3.connect(path)) as db:
                    db.row_factory=sqlite3.Row
                    try:return create_proposal(db,2,'MORPHOLOGY',{'component_count':1},collaborator_id=1,access_role='analyst')
                    except ValueError:return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:propose(),range(2)))
            self.assertEqual(sum(sid is not None for sid in results),1)

    def test_negative_payload_pending_diagnostic_and_exclusive_contract(self):
        from alternative_change_workflow import get_proposal, create_relation_proposals
        before = self.canonical()
        sid = self.propose('RELATION', {'relation_answer': 'NO'})
        self.assertEqual(self.canonical(), before)
        self.assertEqual(json.loads(get_proposal(self.db, sid)['payload']), {'relation_answer': 'NO'})
        task = concept_diagnostics(self.db, [1])[1]['relations'][0]
        self.assertEqual(task['pending_changes']['RELATION'][0]['submission_id'], sid)
        for values in ({'relation_answer': 'NO'}, {'relation_answer': 'NO', 'target_id': 1},
                       {'relation_answer': 'NO', 'parameter': 'CM_1'}, {},
                       {'target_id': 1, 'parameter': 'CM_1', 'uncertain': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                create_proposal(self.db, 2, 'RELATION', values, collaborator_id=1, access_role='analyst')
        with self.assertRaises(ValueError):
            create_relation_proposals(self.db, 2, [], collaborator_id=1, access_role='analyst')
        with self.assertRaises(ValueError):
            create_relation_proposals(self.db, 1, [{'relation_answer': 'NO'}], collaborator_id=1, access_role='analyst')

    def test_relation_previews_use_the_full_current_concept_and_optional_edge(self):
        from alternative_change_workflow import relation_review_preview
        positive = dict(target_id=1, parameter='CM_1')
        accepted = relation_review_preview(self.db, 2, positive, 'ACCEPTED')
        rejected = relation_review_preview(self.db, 2, positive, 'REJECTED')
        negative = relation_review_preview(self.db, 2, {'relation_answer': 'NO'}, 'NO_CONFIRMED')
        self.assertEqual(accepted['suggestions'], {1: '1a', 2: '1b'})
        self.assertEqual(rejected['suggestions'], {1: '1a', 2: '2a'})
        self.assertEqual(negative['suggestions'], rejected['suggestions'])
        self.assertEqual({r['alternative_id'] for r in negative['rows']}, {1, 2})

    def test_accepted_relation_requires_explicit_compatible_resolution(self):
        for values, valid, incompatible in [
                ({'target_id': 1, 'parameter': 'CM_1'}, 'ACCEPTED', 'NO_CONFIRMED'),
                ({'relation_answer': 'NO'}, 'NO_CONFIRMED', 'ACCEPTED')]:
            with self.subTest(values=values):
                self.db.execute('SAVEPOINT scenario')
                sid = self.propose('RELATION', values)
                before = list(self.db.iterdump())
                for resolution in (None, '', 'INVALID', incompatible):
                    kwargs = {} if resolution is None else {'relations_resolution': resolution}
                    with self.subTest(resolution=resolution), self.assertRaises(ValueError):
                        self.review(sid, **kwargs)
                    self.assertEqual(before, list(self.db.iterdump()))
                self.review(sid, relations_resolution=valid)
                self.assertEqual(self.db.execute('SELECT resolution FROM submission WHERE submission_id=?',
                                                (sid,)).fetchone()[0], 'accepted')
                self.db.execute('ROLLBACK TO scenario'); self.db.execute('RELEASE scenario')

    def test_linguistic_resolutions_normalize_without_edges_and_preserve_payload(self):
        from alternative_change_workflow import get_proposal, relation_review_history
        for values, resolution in [({'relation_answer': 'NO'}, 'NO_CONFIRMED'),
                                   ({'target_id': 1, 'parameter': 'CM_1'}, 'REJECTED')]:
            with self.subTest(resolution=resolution):
                self.db.execute('SAVEPOINT scenario')
                sid = self.propose('RELATION', values)
                payload = get_proposal(self.db, sid)['payload']
                result = review_proposal(self.db, sid, 'accepted', collaborator_id=1,
                    access_role='reviewer', relations_resolution=resolution, note='Nota humana')
                self.assertIsNone(result)
                proposal = get_proposal(self.db, sid)
                self.assertEqual((proposal['status'], proposal['resolution'], proposal['review_note']),
                                 ('resolved', 'accepted', 'Nota humana'))
                self.assertEqual(proposal['payload'], payload)
                self.assertEqual(self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0], 0)
                self.assertEqual(self.db.execute('SELECT working_label FROM alternative WHERE alternative_id=2').fetchone()[0], '2a')
                self.assertEqual(concept_diagnostics(self.db, [1])[1]['relations'], [])
                history = relation_review_history(self.db, sid)
                self.assertEqual(history['relations_resolution'], resolution)
                self.assertIsNotNone(history['renumber_event_id'])
                self.db.execute('ROLLBACK TO scenario'); self.db.execute('RELEASE scenario')

    def test_negative_confirmation_keeps_historical_relations(self):
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,is_current) VALUES(1,2,'CM_2',0)")
        self.db.commit()
        edges = [tuple(r) for r in self.db.execute('SELECT * FROM alternative_relation')]
        self.review(self.propose('RELATION', {'relation_answer': 'NO'}), relations_resolution='NO_CONFIRMED')
        self.assertEqual(edges, [tuple(r) for r in self.db.execute('SELECT * FROM alternative_relation')])

    def test_current_relation_blocks_negative_creation_at_either_endpoint(self):
        from alternative_relations import create_current_relation
        create_current_relation(self.db, 1, 2, 'CM_1')
        before = list(self.db.iterdump())
        for aid in (1, 2):
            with self.subTest(aid=aid), self.assertRaisesRegex(ValueError, 'relaciones fonológicas vigentes'):
                create_proposal(self.db, aid, 'RELATION', {'relation_answer': 'NO'},
                                collaborator_id=1, access_role='analyst')
            self.assertEqual(before, list(self.db.iterdump()))

    def test_new_current_relation_blocks_negative_confirmation_even_after_reload(self):
        from alternative_change_workflow import baseline, relation_review_preview
        from alternative_relations import create_current_relation
        sid = self.propose('RELATION', {'relation_answer': 'NO'})
        create_current_relation(self.db, 1, 2, 'CM_1')
        before = list(self.db.iterdump())
        with patch('alternative_change_workflow.apply_nomenclature') as apply:
            with self.assertRaisesRegex(ValueError, 'relaciones fonológicas vigentes'):
                review_proposal(self.db, sid, 'accepted', collaborator_id=1, access_role='reviewer',
                                expected_baseline=baseline(self.db, 2, 'RELATION'), relations_resolution='NO_CONFIRMED')
            apply.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'relaciones fonológicas vigentes'):
            relation_review_preview(self.db, 2, {'relation_answer': 'NO'}, 'NO_CONFIRMED')
        self.assertEqual(before, list(self.db.iterdump()))

    def test_pending_positive_blocks_negative_at_either_endpoint(self):
        self.propose('RELATION', {'target_id': 1, 'parameter': 'CM_1'})
        before = list(self.db.iterdump())
        for aid in (1, 2):
            with self.subTest(aid=aid), self.assertRaisesRegex(ValueError, 'contradice'):
                create_proposal(self.db, aid, 'RELATION', {'relation_answer': 'NO'},
                                collaborator_id=1, access_role='analyst')
            self.assertEqual(before, list(self.db.iterdump()))

    def test_pending_negative_blocks_positive_origin_and_destination_only(self):
        self.propose('RELATION', {'relation_answer': 'NO'})
        before = list(self.db.iterdump())
        for origin, target in ((2, 1), (1, 2)):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, 'contradice'):
                create_proposal(self.db, origin, 'RELATION', {'target_id': target, 'parameter': 'CM_1'},
                                collaborator_id=1, access_role='analyst')
            self.assertEqual(before, list(self.db.iterdump()))
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'3a')")
        self.db.commit()
        from alternative_change_workflow import create_relation_proposals
        ids = create_relation_proposals(self.db, 1,
            [{'target_id': 3, 'parameter': 'CM_1'}, {'target_id': 3, 'parameter': 'OR_M1'}],
            collaborator_id=1, access_role='analyst')
        self.assertEqual(len(set(ids)), 2)

    def test_relation_pending_is_noop_and_full_rejection_never_changes_canonical(self):
        from alternative_change_workflow import get_proposal, relation_review_history
        for values in ({'relation_answer': 'NO'}, {'target_id': 1, 'parameter': 'CM_1'}):
            sid = self.propose('RELATION', values)
            before = list(self.db.iterdump())
            self.review(sid, 'pending')
            self.assertEqual(before, list(self.db.iterdump()))
            with self.assertRaises(ValueError):
                review_proposal(self.db, sid, 'rejected', collaborator_id=1, access_role='reviewer')
            canonical = self.canonical()
            self.review(sid, 'rejected')
            self.assertEqual(canonical, self.canonical())
            self.assertEqual(get_proposal(self.db, sid)['resolution'], 'rejected')
            self.assertEqual(relation_review_history(self.db, sid)['relations_resolution'], 'PROPOSAL_REJECTED')

    def test_no_edge_resolution_checks_review_baseline(self):
        from alternative_change_workflow import baseline
        from alternative_relations import create_current_relation
        for values, resolution in [({'relation_answer': 'NO'}, 'NO_CONFIRMED'),
                                   ({'target_id': 1, 'parameter': 'CM_1'}, 'REJECTED')]:
            with self.subTest(resolution=resolution):
                self.db.execute('SAVEPOINT scenario')
                sid = self.propose('RELATION', values)
                token = baseline(self.db, 2, 'RELATION')
                create_current_relation(self.db, 1, 2, 'CM_2')
                before = list(self.db.iterdump())
                with self.assertRaisesRegex(ValueError, 'abrir'):
                    review_proposal(self.db, sid, 'accepted', collaborator_id=1, access_role='reviewer',
                                    expected_baseline=token, relations_resolution=resolution)
                self.assertEqual(before, list(self.db.iterdump()))
                self.db.execute('ROLLBACK TO scenario'); self.db.execute('RELEASE scenario')

    def test_relation_resolution_rollback_for_nomenclature_activity_and_invalid_preview(self):
        for values, resolution in [({'relation_answer': 'NO'}, 'NO_CONFIRMED'),
                                   ({'target_id': 1, 'parameter': 'CM_1'}, 'ACCEPTED')]:
            for function in ('apply_nomenclature', 'record_activity', 'validate_final_labels'):
                with self.subTest(resolution=resolution, function=function):
                    self.db.execute('SAVEPOINT scenario')
                    sid = self.propose('RELATION', values)
                    before = list(self.db.iterdump())
                    with patch('alternative_change_workflow.' + function, side_effect=ValueError('injected')):
                        with self.assertRaises(ValueError):
                            review_proposal(self.db, sid, 'accepted', collaborator_id=1,
                                            access_role='reviewer', relations_resolution=resolution)
                    self.assertEqual(before, list(self.db.iterdump()))
                    self.db.execute('ROLLBACK TO scenario'); self.db.execute('RELEASE scenario')

    def test_inconclusive_graph_cannot_be_applied(self):
        from alternative_change_workflow import relation_review_preview
        self.db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(1,?)',
                            [(str(i) + 'a',) for i in range(3, 29)])
        self.db.executemany("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,'CM_1')",
                            [(1, 3)] + [(i, i + 1) for i in range(3, 28)])
        self.db.commit()
        sid = self.propose('RELATION', {'relation_answer': 'NO'})
        preview = relation_review_preview(self.db, 2, {'relation_answer': 'NO'}, 'NO_CONFIRMED')
        self.assertFalse(preview['conclusive'])
        self.assertEqual(len(preview['rows']), 28)
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, 'nomenclatura'):
            self.review(sid, relations_resolution='NO_CONFIRMED')
        self.assertEqual(before, list(self.db.iterdump()))

    def test_failures_roll_back_everything(self):
        for kind, table, event in [('MORPHOLOGY','alternative_morphology','INSERT'),
                                  ('MORPHOLOGY','alternative_morphology','UPDATE'),
                                  ('MORPHOLOGY','alternative_component','INSERT'),
                                  ('MORPHOLOGY','submission','UPDATE'),
                                  ('RELATION','alternative_relation','INSERT'),
                                  ('RELATION','submission','UPDATE')]:
            with self.subTest(kind=kind,table=table):
                self.db.execute('SAVEPOINT fixture')
                if kind == 'MORPHOLOGY':
                    create_or_replace_alternative_morphology(self.db,2,component_count=1)
                values = {'component_count':2,'free_permutation':'NO','components':[{'position':1,'component_alternative_id':1}]} if kind=='MORPHOLOGY' else {'target_id':1,'parameter':'CM_1'}
                sid=self.propose(kind,values)
                self.db.execute(f"CREATE TEMP TRIGGER fail_write BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
                before=list(self.db.iterdump())
                with self.assertRaises(sqlite3.IntegrityError):
                    self.review(sid, **({'relations_resolution': 'ACCEPTED'} if kind == 'RELATION' else {}))
                self.assertEqual(before,list(self.db.iterdump()))
                self.db.execute('ROLLBACK TO fixture');self.db.execute('RELEASE fixture')

    def test_real_role_routes_proposal_queue_and_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'changes.db'
            with closing(sqlite3.connect(path)) as copy:self.db.backup(copy)
            env={k:v for k,v in os.environ.items() if not k.startswith(('LESICO_','RAILWAY_'))}
            env.update(LESICO_ENV='development',LESICO_DATABASE_PATH=str(path),LESICO_SECRET_KEY='test-only',
                       LESICO_ANALYST_ROUTE='a',LESICO_REVIEWER_ROUTE='r',LESICO_MASTER_ROUTE='m')
            script='''
from app import app
from database import conectar
from contextlib import closing
from html.parser import HTMLParser
class Forms(HTMLParser):
    def __init__(self,text):
        super().__init__();self.forms=[];self.current=None;self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form':self.current={};self.forms.append(self.current)
        if tag=='input' and self.current is not None and a.get('name'):self.current[a['name']]=a.get('value','')
client=app.test_client()
page=client.get('/a/alternativas/2/proponer?mode=morphology');assert page.status_code==200,page.text
assert 'name="kind" value="RELATION"' not in page.text
assert 'Evidencias asociadas' in page.text
forms=Forms(page.text).forms
data=next(f for f in forms if f.get('kind')=='MORPHOLOGY')
data.update(component_count='1',collaborator_id='1')
assert client.post('/a/alternativas/2/proponer',data={}).status_code==400
response=client.post('/a/alternativas/2/proponer?mode=morphology',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_morphology').fetchone()[0]==0
    sid=db.execute('SELECT max(submission_id) FROM submission').fetchone()[0]
page=client.get('/a/alternativas/2/proponer?mode=morphology')
assert 'En revisión' in page.text and 'name="component_count"' not in page.text
assert client.post('/a/alternativas/2/proponer?mode=morphology',data=data).status_code==400
assert '/aportes/alternativas/'+str(sid) in client.get('/r/aportes/pendientes').text
import re
link=re.search('<a[^>]*href="/r/aportes/alternativas/'+str(sid)+'"[^>]*>',client.get('/r/aportes/pendientes').text)
assert link and 'target=' not in link.group()
detail='/aportes/alternativas/'+str(sid)
assert client.post('/a'+detail+'/decidir',data={}).status_code==404
page=client.get('/r'+detail);assert page.status_code==200,page.text
data=Forms(page.text).forms[0];data.update(decision='accepted',collaborator_id='1',relations_resolution='ACCEPTED')
response=client.post('/r'+detail+'/decidir',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_morphology WHERE is_current=1').fetchone()[0]==1
page=client.get('/a/alternativas/2/proponer?mode=relation')
assert 'name="component_count"' not in page.text
assert 'value="2"' not in re.search('<select name="target_id".*?</select>',page.text,re.S).group()
data=next(f for f in Forms(page.text).forms if f.get('kind')=='RELATION')
data.update(target_id='1',parameter='CM_1',collaborator_id='1',relation_answer='YES')
response=client.post('/a/alternativas/2/proponer?mode=relation',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_relation').fetchone()[0]==0
    sid=db.execute('SELECT max(submission_id) FROM submission').fetchone()[0]
detail='/aportes/alternativas/'+str(sid)
page=client.get('/m'+detail);assert page.status_code==200,page.text
data=Forms(page.text).forms[0];data.update(decision='accepted',collaborator_id='1',relations_resolution='ACCEPTED')
response=client.post('/m'+detail+'/decidir',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0]==1
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
assert client.get('/a/alternativas/2/gestionar').status_code==404
assert client.get('/r/alternativas/2/gestionar').status_code==200
assert client.get('/a/aportes').status_code==200
from playwright.sync_api import sync_playwright, expect
from urllib.parse import urlsplit
def serve(route):
    req=route.request;url=urlsplit(req.url)
    response=client.open(url.path+('?' + url.query if url.query else ''),method=req.method,
        data=req.post_data,content_type=req.headers.get('content-type'),follow_redirects=True)
    try:route.fulfill(status=response.status_code,body=response.data,content_type=response.content_type)
    finally:response.close()
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    try:
        page=browser.new_page();page.route('**/*',serve);errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://local.test/a/alternativas/1/proponer?mode=morphology')
        expect(page.locator('select[name=free_permutation]')).to_have_value('SIN INFORMACIÓN')
        expect(page.locator('#identified-component-controls')).to_be_hidden()
        page.locator('[name=component_count]').fill('3')
        page.locator('[name=ui_identified][value=yes]').check()
        expect(page.locator('#morphology-components [data-component-row]')).to_have_count(1)
        first=page.locator('#morphology-components [data-component-row]').first
        first.locator('[value=existing]').check()
        first.locator('select').select_option('2')
        first.locator('[name$=_label]').fill('Etiqueta conservada')
        first.locator('[name$=_note]').fill('Nota conservada')
        first.locator('[name$=_position]').fill('5')
        page.locator('#add-component').click()
        expect(page.locator('#morphology-components [data-component-row]')).to_have_count(2)
        page.locator('#remove-last-component').click()
        expect(page.locator('#morphology-components [data-component-row]')).to_have_count(1)
        first.locator('[value=unapproved]').check()
        expect(first.locator('select')).to_be_disabled()
        expect(first.locator('[name$=_label]')).to_have_value('Etiqueta conservada')
        first.locator('[value=existing]').check()
        expect(first.locator('select')).to_have_value('2')
        page.locator('[name=component_count]').fill('1')
        expect(page.locator('#identified-component-controls')).to_be_hidden()
        expect(first.locator('[name$=_position]')).to_be_disabled()
        page.locator('[name=component_count]').fill('N/A')
        expect(page.locator('#identified-component-controls')).to_be_visible()
        expect(page.locator('[name=free_permutation]')).to_be_disabled()
        page.locator('[name=component_count]').fill('0')
        page.locator('[name=morphology_note]').fill('Observación enviada')
        page.locator('#lesico-collaborator').select_option('1')
        page.get_by_role('button',name='Enviar propuesta de morfología').click()
        expect(page.locator('[role=alert]')).to_contain_text('al menos 1')
        first=page.locator('#morphology-components [data-component-row]').first
        expect(first.locator('select')).to_have_value('2')
        expect(first.locator('[name$=_label]')).to_have_value('Etiqueta conservada')
        expect(first.locator('[name$=_note]')).to_have_value('Nota conservada')
        expect(first.locator('[name$=_position]')).to_have_value('5')
        expect(page.locator('[name=morphology_note]')).to_have_value('Observación enviada')
        page.locator('[name=component_count]').fill('12')
        page.locator('[name=ui_identified][value=no]').check()
        expect(first.locator('[name$=_position]')).to_be_disabled()
        page.locator('[name=ui_identified][value=yes]').check()
        expect(first.locator('[name$=_label]')).to_have_value('Etiqueta conservada')
        expect(page.locator('#morphology-proposal [name=collaborator_id]')).to_have_value('1')
        page.get_by_role('button',name='Enviar propuesta de morfología').click()
        expect(page.locator('h1')).to_contain_text('Aporte #')
        with closing(conectar()) as db:
            import json
            from alternative_change_workflow import review_proposal
            proposal=db.execute("SELECT submission_id,payload FROM alternative_change_submission WHERE change_kind='MORPHOLOGY' ORDER BY submission_id DESC").fetchone()
            values=json.loads(proposal['payload'])
            assert values['component_count']==12 and len(values['components'])==1,values
            assert values['components'][0]==dict(position=5,component_alternative_id=2,component_label='Etiqueta conservada',note='Nota conservada'),values
            assert db.execute('SELECT COUNT(*) FROM alternative_morphology WHERE is_current=1').fetchone()[0]==1
            review_proposal(db,proposal['submission_id'],'rejected',collaborator_id=1,access_role='reviewer',note='Prueba de interfaz')
        page.goto('http://local.test/a/alternativas/2/proponer?mode=relation')
        page.locator('#lesico-collaborator').select_option('1')
        rows=page.locator('#relation-rows [data-relation-row]')
        expect(rows).to_have_count(1)
        expect(page.locator('#relation-context')).not_to_have_attribute('open','')
        expect(page.locator('#send-relations')).to_be_disabled()
        expect(page.locator('#positive-relation-controls')).to_be_hidden()
        page.locator('#relation-answer').select_option('YES')
        rows.first.locator('[data-remove-relation]').click()
        expect(rows).to_have_count(0)
        expect(page.locator('#send-relations')).to_be_disabled()
        page.locator('#add-relation').click()
        rows.first.locator('[name=target_id]').select_option('1')
        expect(rows.first.locator('[name=parameter] option').filter(has_text='CM_1')).to_have_attribute('disabled','')
        rows.first.locator('[name=parameter]').select_option('CM_2')
        expect(page.locator('#send-relations')).to_be_enabled()
        page.locator('#add-relation').click()
        rows.last.locator('[name=target_id]').select_option('1')
        expect(rows.last.locator('[name=parameter] option').filter(has_text='CM_2')).to_have_attribute('disabled','')
        rows.last.locator('[name=parameter]').select_option('OR_M1')
        expect(page.locator('#send-relations')).to_be_enabled()
        page.locator('#add-relation').click()
        expect(page.locator('#send-relations')).to_be_disabled()
        rows.last.locator('[data-remove-relation]').click()
        expect(rows).to_have_count(2)
        page.locator('#relation-answer').select_option('NO')
        expect(page.locator('#positive-relation-controls')).to_be_hidden()
        expect(rows.first.locator('[name=target_id]')).to_be_disabled()
        assert page.locator('#relation-proposals').evaluate("form => !new FormData(form).has('target_id') && !new FormData(form).has('parameter')")
        expect(page.locator('#send-relations')).to_be_enabled()
        page.locator('#relation-answer').select_option('YES')
        expect(rows.first.locator('[name=parameter]')).to_have_value('CM_2')
        expect(rows.last.locator('[name=parameter]')).to_have_value('OR_M1')
        page.locator('#relation-proposals [name=state_token]').evaluate("input => input.value='invalid'")
        page.locator('#send-relations').click()
        expect(page.locator('[role=alert]')).to_be_visible()
        expect(rows).to_have_count(2)
        expect(rows.first.locator('[name=target_id]')).to_have_value('1')
        expect(rows.first.locator('[name=parameter]')).to_have_value('CM_2')
        expect(rows.last.locator('[name=parameter]')).to_have_value('OR_M1')
        expect(page.locator('#relation-proposals [name=collaborator_id]')).to_have_value('1')
        page.locator('#send-relations').click()
        with closing(conectar()) as db:
            pending=[r[0] for r in db.execute("SELECT submission_id FROM submission WHERE status='pending'")]
            assert len(pending)==2
            assert db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0]==1
            payloads=[json.loads(r[0]) for r in db.execute("SELECT payload FROM alternative_change_submission JOIN submission USING(submission_id) WHERE status='pending' ORDER BY submission_id")]
            assert payloads==[dict(target_id=1,parameter='CM_2'),dict(target_id=1,parameter='OR_M1')],payloads
        page.locator('#relation-answer').select_option('YES')
        expect(rows.first.locator('[name=target_id]')).to_have_value('')
        rows.first.locator('[name=target_id]').select_option('1')
        expect(rows.first.locator('[name=parameter] option').filter(has_text='CM_2')).to_have_attribute('disabled','')
        expect(rows.first.locator('[name=parameter] option').filter(has_text='OR_M1')).to_have_attribute('disabled','')
        for sid,decision in zip(pending,('ACCEPTED','REJECTED')):
            page.goto('http://local.test/r/aportes/alternativas/'+str(sid))
            page.locator('textarea[name=review_note]').fill('Revision individual')
            expect(page.locator('[data-relation-preview]:visible')).to_have_count(0)
            page.locator('[name=relations_resolution][value='+decision+']').check()
            expect(page.locator('[data-relation-preview]:visible .preview-table tr')).to_have_count(3)
            page.locator('#apply-relation-review').click()
        with closing(conectar()) as db:
            assert db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0]==2
            db.execute("INSERT INTO concept(preferred_label) VALUES('Concepto aislado')")
            db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1b')")
            db.commit()
        page.goto('http://local.test/a/alternativas/3/proponer?mode=relation')
        page.locator('#relation-answer').select_option('NO')
        expect(page.locator('#send-relations')).to_be_enabled()
        page.locator('#relation-proposals [name=state_token]').evaluate("input => input.value='invalid'")
        page.locator('#send-relations').click()
        expect(page.locator('#relation-answer')).to_have_value('NO')
        expect(page.locator('#positive-relation-controls')).to_be_hidden()
        expect(page.locator('#relation-proposals [name=collaborator_id]')).to_have_value('1')
        page.locator('#send-relations').click()
        expect(page.locator('h1')).to_contain_text('Aporte #')
        with closing(conectar()) as db:
            sid,payload=db.execute('SELECT submission_id,payload FROM alternative_change_submission ORDER BY submission_id DESC').fetchone()
            assert json.loads(payload)=={'relation_answer':'NO'}
        page.goto('http://local.test/r/aportes/alternativas/'+str(sid))
        page.locator('[name=decision][value=rejected]').check()
        expect(page.locator('textarea[name=review_note]')).to_have_attribute('required','')
        expect(page.locator('[data-relation-preview]:visible')).to_have_count(0)
        page.locator('[name=decision][value=accepted]').check()
        expect(page.locator('textarea[name=review_note]')).not_to_have_attribute('required','')
        expect(page.locator('[data-relation-preview]:visible .preview-table tr')).to_have_count(2)
        page.locator('#apply-relation-review').click()
        expect(page.locator('body')).to_contain_text('Propuesta de ninguna relación faltante confirmada')
        with closing(conectar()) as db:
            assert db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0]==2
        assert not errors,errors
    finally:browser.close()
with closing(conectar()) as db:
    db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1");db.commit()
page=client.get('/a/alternativas/2/proponer?mode=relation')
assert 'No hay otras alternativas activas' in page.text
assert 'id="relation-proposals"' in page.text
assert 'value="YES" disabled' in page.text
'''
            result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


class MorphologyProposalRouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask, g
        from routes.alternative_changes import alternative_changes_bp
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'morphology.db'
        with closing(self.connect()) as db:
            crear_esquema(db)
            db.execute("INSERT INTO collaborator(display_name) VALUES('Ana')")
            db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('SOL',), ('LUNA',)])
            db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)',
                           [(1, '1a'), (1, '2a'), (2, '1a'), (2, '2a')])
            db.execute('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=4')
            db.commit()
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        app.register_blueprint(alternative_changes_bp)
        @app.before_request
        def analyst():
            g.current_access_role = 'analyst'
        self.client = app.test_client()
        connection_patch = patch('routes.alternative_changes.conectar', self.connect)
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        self.url = '/alternativas/1/proponer?mode=morphology'

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def form(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        from html import unescape
        values = {name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
                  for name in ('csrf_token', 'state_token', 'kind')}
        values.update(collaborator_id='1', component_count='3', free_permutation='NO',
                      morphology_note='Observación', component_7_position='5',
                      component_7_alternative_id='3', component_7_label='Etiqueta', component_7_note='Nota')
        return values

    def canonical(self):
        with closing(self.connect()) as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)] for table in
                    ('alternative', 'alternative_morphology', 'alternative_component', 'alternative_relation')}

    def test_selector_is_global_active_and_excludes_self(self):
        page = self.client.get(self.url).text
        select = re.search(r'<select name="component_1_alternative_id">(.*?)</select>', page, re.S)[1]
        self.assertIn('value="2">SOL-2a', select)
        self.assertIn('value="3">LUNA-1a', select)
        self.assertNotIn('value="1"', select)
        self.assertNotIn('value="4"', select)
        self.assertNotIn('<input name="component_1_alternative_id"', page)

    def test_existing_component_preserves_exact_payload_and_canonical_state(self):
        data = self.form()  # Already-open forms do not send the new UI controls.
        before = self.canonical()
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(before, self.canonical())
        with closing(self.connect()) as db:
            payload = json.loads(db.execute('SELECT payload FROM alternative_change_submission').fetchone()[0])
            self.assertEqual(payload, dict(component_count=3, component_count_not_applicable=0,
                free_permutation='NO', note='Observación', components=[dict(position=5,
                component_alternative_id=3, component_label='Etiqueta', note='Nota')]))
            self.assertEqual(tuple(db.execute('SELECT submission_type,status FROM submission').fetchone()),
                             ('ALTERNATIVE_CHANGE', 'pending'))

    def test_uncertain_component_keeps_label_and_note(self):
        data = self.form()
        data.update(ui_identified='yes', ui_component_type_7='unapproved', component_7_alternative_id='')
        before = self.canonical()
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(before, self.canonical())
        with closing(self.connect()) as db:
            payload = json.loads(db.execute('SELECT payload FROM alternative_change_submission').fetchone()[0])
        self.assertEqual(payload['components'], [dict(position=5, component_alternative_id=None,
                                                     component_label='Etiqueta', note='Nota')])
        self.assertEqual(set(payload), {'component_count', 'component_count_not_applicable',
                                       'free_permutation', 'note', 'components'})

    def test_count_one_na_and_large_counts_keep_existing_rules(self):
        for count, expected_count, expected_components in [('1', 1, 0), ('N/A', None, 1), ('12', 12, 1)]:
            with self.subTest(count=count):
                data = self.form()
                data.update(component_count=count, ui_identified='yes', ui_component_type_7='existing')
                response = self.client.post(self.url, data=data)
                self.assertEqual(response.status_code, 302, response.text)
                with closing(self.connect()) as db:
                    row = db.execute('SELECT submission_id,payload FROM alternative_change_submission ORDER BY submission_id DESC').fetchone()
                    payload = json.loads(row['payload'])
                    self.assertEqual(payload['component_count'], expected_count)
                    self.assertEqual(payload['free_permutation'], 'NO' if count == '12' else 'N/A')
                    self.assertEqual(len(payload['components']), expected_components)
                    review_proposal(db, row['submission_id'], 'rejected', collaborator_id=1,
                                    access_role='reviewer', note='Siguiente caso')

    def test_validation_error_preserves_sent_values_and_rejects_invalid_targets(self):
        for target in ('1', '4', '999'):
            with self.subTest(target=target):
                data = self.form()
                data.update(component_7_alternative_id=target, ui_identified='yes', ui_component_type_7='existing')
                before = self.canonical()
                response = self.client.post(self.url, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(before, self.canonical())
                self.assertIn('name="component_7_label" value="Etiqueta"', response.text)
                self.assertIn('name="component_7_note">Nota</textarea>', response.text)
                self.assertIn('name="morphology_note">Observación</textarea>', response.text)
                self.assertIn('value="' + target + '" data-unavailable-reference', response.text)
                with closing(self.connect()) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM submission').fetchone()[0], 0)

    def test_canonical_preload_keeps_all_component_fields(self):
        with closing(self.connect()) as db:
            create_or_replace_alternative_morphology(db, 1, component_count=3, free_permutation='SÍ',
                note='Observación previa', components=[dict(position=5, component_alternative_id=3,
                component_label='Etiqueta previa', note='Nota previa')])
        page = self.client.get(self.url).text
        self.assertIn('value="3" selected>LUNA-1a', page)
        self.assertIn('value="Etiqueta previa"', page)
        self.assertIn('>Nota previa</textarea>', page)
        self.assertIn('>Observación previa</textarea>', page)
        self.assertIn('value="yes" checked', page)

    def test_invalid_permutation_is_preserved_for_correction(self):
        data = self.form()
        data['free_permutation'] = 'Valor reenviado'
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('<option selected value="Valor reenviado">Valor reenviado</option>', response.text)
        data['free_permutation'] = 'SÍ'
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 302, response.text)


class RelationProposalRouteTests(unittest.TestCase):
    connect = MorphologyProposalRouteTests.connect
    canonical = MorphologyProposalRouteTests.canonical

    def setUp(self):
        MorphologyProposalRouteTests.setUp(self)
        self.url = '/alternativas/1/proponer?mode=relation'
        from routes.submissions import preview_group_changed, preview_proposed_order
        self.client.application.jinja_env.filters.update(preview_group_changed=preview_group_changed, preview_proposed_order=preview_proposed_order)
        with closing(self.connect()) as db:
            db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'3a')")
            db.execute("INSERT INTO source(source_name,source_type) VALUES('Libro solar','MATERIAL_IMPRESO')")
            db.execute("INSERT INTO occurrence(source_id,original_gloss,source_detail_1,source_detail_1_status,source_detail_2,source_detail_2_status,source_locator) VALUES(1,'Glosa actual','Capítulo solar','VALUE','42','VALUE','LOCALIZADOR LEGACY')")
            db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,2)')
            db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'Glosa histórica')")
            db.execute('INSERT INTO assignment(occurrence_id,alternative_id,is_current) VALUES(2,2,0)')
            db.commit()

    def form(self, targets=('2',), parameters=('CM_1',)):
        from html import unescape
        from werkzeug.datastructures import MultiDict
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        values = MultiDict({name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
                            for name in ('csrf_token', 'state_token', 'kind')})
        values['collaborator_id'] = '1'
        values['relation_answer'] = 'YES'
        values.setlist('target_id', targets)
        values.setlist('parameter', parameters)
        return values

    def test_context_closed_current_evidence_and_readable_selector(self):
        page = self.client.get(self.url).text
        self.assertIn('<details id="relation-context">', page)
        context = page.split('<details id="relation-context">', 1)[1].split('</details>', 1)[0]
        for value in ('SOL-2a', 'SOL-3a', 'Glosa actual', 'Libro solar', 'Capítulo solar', 'Página: 42'):
            self.assertIn(value, context)
        for value in ('SOL-1a', 'LUNA', 'Glosa histórica', 'LOCALIZADOR LEGACY', '<input', '<select'):
            self.assertNotIn(value, context)
        rows = page.split('<div id="relation-rows">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(rows.count('data-relation-row'), 1)
        target = re.search(r'<select name="target_id" required>(.*?)</select>', rows, re.S)[1]
        self.assertIn('value="2">SOL-2a', target)
        self.assertIn('value="5">SOL-3a', target)
        self.assertNotIn('selected', target)
        self.assertNotIn('ID', target)
        for identifier in ('1', '3', '4'):
            self.assertNotIn('value="' + identifier + '"', target)

    def test_multiple_targets_and_parameters_preserve_exact_payload(self):
        before = self.canonical()
        response = self.client.post(self.url, data=self.form(('2', '2', '5'), ('CM_1', 'OR_M1', 'MOV_M1')))
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(before, self.canonical())
        with closing(self.connect()) as db:
            payloads = [json.loads(r[0]) for r in db.execute('SELECT payload FROM alternative_change_submission ORDER BY submission_id')]
            self.assertEqual(payloads, [dict(target_id=2, parameter='CM_1'), dict(target_id=2, parameter='OR_M1'),
                                        dict(target_id=5, parameter='MOV_M1')])
            self.assertEqual(db.execute("SELECT count(*) FROM submission WHERE status='pending'").fetchone()[0], 3)

    def test_duplicate_post_recovers_all_rows_and_collaborator(self):
        before = self.canonical()
        response = self.client.post(self.url, data=self.form(('2', '2', '5'), ('CM_1', 'CM_1', 'OR_M1')))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.canonical())
        rows = response.text.split('<div id="relation-rows">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(rows.count('data-relation-row'), 3)
        self.assertEqual(rows.count('value="2" selected'), 2)
        self.assertIn('value="5" selected', rows)
        self.assertEqual(rows.count('<option selected>CM_1</option>'), 2)
        self.assertIn('<option selected>OR_M1</option>', rows)
        self.assertIn('name="collaborator_id" value="1"', response.text)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM submission').fetchone()[0], 0)

    def test_misaligned_and_invalid_rows_are_not_dropped(self):
        for targets, parameters in [(('2', '5'), ('CM_1',)), (('3', '4'), ('CM_1', 'INVALID'))]:
            with self.subTest(targets=targets):
                response = self.client.post(self.url, data=self.form(targets, parameters))
                self.assertEqual(response.status_code, 400)
                rows = response.text.split('<div id="relation-rows">', 1)[1].split('</div>', 1)[0]
                self.assertEqual(rows.count('data-relation-row'), 2)
                for target in targets:
                    self.assertIn('value="' + target + '" selected', rows)
                for parameter in parameters:
                    self.assertIn(parameter, rows)

    def test_pending_inverse_blocks_only_matching_parameter(self):
        with closing(self.connect()) as db:
            create_proposal(db, 2, 'RELATION', dict(target_id=1, parameter='CM_1'),
                            collaborator_id=1, access_role='analyst')
        page = self.client.get(self.url).text
        unavailable = json.loads(re.search(r'id="unavailable-relations">(.*?)</script>', page, re.S)[1])
        self.assertIn([2, 'CM_1'], unavailable)
        self.assertNotIn([2, 'OR_M1'], unavailable)
        self.assertEqual(self.client.post(self.url, data=self.form()).status_code, 400)
        self.assertEqual(self.client.post(self.url, data=self.form(parameters=('OR_M1',))).status_code, 302)

    def test_notice_answer_selector_negative_submission_and_error_recovery(self):
        with closing(self.connect()) as db:
            db.execute("UPDATE alternative SET working_label='1b' WHERE alternative_id=1")
            db.commit()
        page = self.client.get(self.url).text
        self.assertIn('Revisión necesaria', page)
        self.assertIn('<option value="" selected>Seleccione</option>', page)
        self.assertIn('<option value="YES">Sí</option>', page)
        self.assertIn('<option value="NO">No</option>', page)
        data = self.form((), ())
        data['relation_answer'] = 'NO'
        token = data['state_token']
        data['state_token'] = 'invalid'
        before = self.canonical()
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('<option value="NO" selected>No</option>', response.text)
        self.assertIn('name="collaborator_id" value="1"', response.text)
        self.assertIn('id="positive-relation-controls" hidden disabled', response.text)
        data['state_token'] = token
        response = self.client.post(self.url, data=data)
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(before, self.canonical())
        with closing(self.connect()) as db:
            proposals = db.execute('SELECT change_kind,payload FROM alternative_change_submission').fetchall()
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0]['change_kind'], 'RELATION')
            self.assertEqual(json.loads(proposals[0]['payload']), {'relation_answer': 'NO'})
        page = self.client.get(self.url).text
        self.assertIn('En revisión', page)
        self.assertIn('No falta ninguna relación', page)
        self.assertNotIn('[null, null]', page)

    def test_empty_positive_and_mixed_negative_are_invalid(self):
        for answer, targets, parameters in [('YES', (), ()), ('', (), ()), ('NO', ('2',), ('CM_1',))]:
            with self.subTest(answer=answer):
                data = self.form(targets, parameters)
                data['relation_answer'] = answer
                response = self.client.post(self.url, data=data)
                self.assertEqual(response.status_code, 400)
                with closing(self.connect()) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM submission').fetchone()[0], 0)

    def test_analyst_requires_explicit_answer_even_with_valid_relation_rows(self):
        for answer in (None, '', 'INVALID', 'yes'):
            with self.subTest(answer=answer):
                data = self.form()
                if answer is None:
                    del data['relation_answer']
                else:
                    data['relation_answer'] = answer
                with closing(self.connect()) as db:
                    before = list(db.iterdump())
                response = self.client.post(self.url, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn('Seleccione Sí o No', response.text)
                self.assertNotIn('<option value="YES" selected', response.text)
                self.assertIn('id="positive-relation-controls" hidden disabled', response.text)
                with closing(self.connect()) as db:
                    self.assertEqual(before, list(db.iterdump()))

    def test_review_route_requires_explicit_relation_resolution(self):
        from flask import g
        from html import unescape
        self.client.application.before_request_funcs[None].append(lambda: setattr(g, 'current_access_role', 'reviewer'))
        for values in ({'relation_answer': 'NO'}, {'target_id': 2, 'parameter': 'CM_1'}):
            with self.subTest(values=values):
                with closing(self.connect()) as db:
                    sid = create_proposal(db, 1, 'RELATION', values, collaborator_id=1, access_role='analyst')
                url = '/aportes/alternativas/' + str(sid)
                page = self.client.get(url)
                data = {name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
                        for name in ('csrf_token', 'review_token')}
                data.update(collaborator_id='1', decision='accepted')
                with closing(self.connect()) as db:
                    before = list(db.iterdump())
                for resolution in (None, '', 'INVALID'):
                    if resolution is not None:
                        data['relations_resolution'] = resolution
                    response = self.client.post(url + '/decidir', data=data)
                    self.assertEqual(response.status_code, 400)
                    with closing(self.connect()) as db:
                        self.assertEqual(before, list(db.iterdump()))
                # Full rejection still needs only the human note, not a linguistic resolution.
                del data['relations_resolution']
                data.update(decision='rejected', review_note='Revisión completa')
                self.assertEqual(self.client.post(url + '/decidir', data=data).status_code, 302)

    def test_review_routes_preview_decisions_and_stale_token(self):
        from flask import g
        from html import unescape
        self.client.application.before_request_funcs[None].append(lambda: setattr(g, 'current_access_role', 'reviewer'))
        for values, resolution in [({'relation_answer': 'NO'}, 'NO_CONFIRMED'),
                                   ({'target_id': 2, 'parameter': 'CM_1'}, 'REJECTED')]:
            with self.subTest(values=values):
                with closing(self.connect()) as db:
                    sid = create_proposal(db, 1, 'RELATION', values, collaborator_id=1, access_role='analyst')
                url = '/aportes/alternativas/' + str(sid)
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertIn('VISTA PREVIA DE CAMBIOS', page.text)
                self.assertRegex(page.text, r'<input\s+type="hidden"\s+name="csrf_token"\s+value="[^"]+">')
                self.assertIn('3a', page.text)
                data = {name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', page.text)[1])
                        for name in ('csrf_token', 'review_token')}
                data.update(collaborator_id='1', decision='pending')
                if resolution != 'NO_CONFIRMED':
                    data.update(decision='accepted', relations_resolution='pending')
                with closing(self.connect()) as db:
                    before = list(db.iterdump())
                self.assertEqual(self.client.post(url + '/decidir', data=data).status_code, 302)
                with closing(self.connect()) as db:
                    self.assertEqual(before, list(db.iterdump()))
                    db.execute("UPDATE alternative SET working_label='9b' WHERE alternative_id=1")
                    db.commit()
                data['decision'] = 'accepted'
                if resolution:
                    data['relations_resolution'] = resolution
                self.assertEqual(self.client.post(url + '/decidir', data=data).status_code, 400)
                page = self.client.get(url)
                data['review_token'] = unescape(re.search(r'name="review_token" value="([^"]*)"', page.text)[1])
                response = self.client.post(url + '/decidir', data=data)
                self.assertEqual(response.status_code, 302, response.text)
                page = self.client.get(url)
                self.assertIn('Resolución:', page.text)
                with closing(self.connect()) as db:
                    self.assertEqual(db.execute('SELECT resolution FROM submission WHERE submission_id=?', (sid,)).fetchone()[0], 'accepted')
                    self.assertEqual(db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0], 0)


class Migration027Tests(unittest.TestCase):
    def test_legacy_preservation_and_idempotence(self):
        # Build exactly the previous schema without changing a working-tree file.
        source=subprocess.check_output(['git','show','176ce25:database.py'],cwd=ROOT).decode('utf-8')
        namespace={'__file__':str(ROOT/'database.py')};exec(compile(source,'old_database','exec'),namespace)
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');namespace['crear_esquema'](db)
        db.execute("INSERT INTO source(source_name) VALUES('Fuente')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'Glosa')")
        db.execute("INSERT INTO submission(submission_id,occurrence_id,submission_type,status) VALUES(30,1,'GRAMMAR','pending')")
        db.execute("INSERT INTO grammar_submission(submission_id,note) VALUES(30,'Historia')")
        db.execute("INSERT INTO submission(submission_id,occurrence_id,submission_type,status,resolution) VALUES(70,1,'GRAMMAR','resolved','rejected')")
        db.execute('DELETE FROM submission WHERE submission_id=70');db.commit()
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'")]
        before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables}
        spec=importlib.util.spec_from_file_location('migration027',ROOT/'migrations/027_alternative_change_submissions.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        db.execute('BEGIN IMMEDIATE');self.assertEqual(module.migration(db)['changes'],1);db.commit()
        for table,rows in before.items():
            if table=='submission':
                columns='submission_id,occurrence_id,submission_type,status,resolution,submitted_at,resolved_at,submitted_by,reviewed_by,review_note,legacy_reviewed_at'
            else:columns='*'
            self.assertEqual(rows,[tuple(r) for r in db.execute(f'SELECT {columns} FROM {table}')],table)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        from grammar_workflow import resolve_grammar_submission
        resolve_grammar_submission(db,30,'rejected',review_note='Histórico compatible',access_role='reviewer')
        sid=db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'GRAMMAR','pending')").lastrowid
        self.assertGreater(sid,70);db.commit()
        db.execute('BEGIN IMMEDIATE');self.assertEqual(module.migration(db)['changes'],0);db.commit()

    def test_empty_preview_backup_apply_and_failure_rollback(self):
        from unittest.mock import patch
        source=subprocess.check_output(['git','show','176ce25:database.py'],cwd=ROOT).decode('utf-8')
        namespace={'__file__':str(ROOT/'database.py')};exec(compile(source,'old_database','exec'),namespace)
        spec=importlib.util.spec_from_file_location('migration027',ROOT/'migrations/027_alternative_change_submissions.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.db'
            with closing(sqlite3.connect(path)) as db:
                namespace['crear_esquema'](db);db.commit()
            before=path.read_bytes()
            self.assertEqual(module.migrate(path)['changes'],1)
            self.assertEqual(path.read_bytes(),before)
            with patch.object(module,'validate_schema',side_effect=ValueError('injected validation failure')):
                with self.assertRaises(ValueError):module.migrate(path,apply=True)
            self.assertEqual(path.read_bytes(),before)
            report=module.migrate(path,apply=True)
            self.assertEqual(report['changes'],1)
            self.assertTrue(Path(report['backup']).is_file())
            self.assertEqual(module.migrate(path,apply=True)['changes'],0)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')


if __name__=='__main__':unittest.main()
