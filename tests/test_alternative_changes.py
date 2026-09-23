import importlib.util
import sqlite3
import subprocess
import os
import sys
import tempfile
from contextlib import closing
import unittest
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

    def review(self, sid, decision='accepted', role='reviewer'):
        return review_proposal(self.db, sid, decision, collaborator_id=1, access_role=role, note='Revisado')

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
        self.assertEqual(before,self.canonical());self.review(sid)
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
        self.review(sid);self.review(other,'rejected')

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
                with self.assertRaises(sqlite3.IntegrityError):self.review(sid)
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
data=Forms(page.text).forms[0];data.update(decision='accepted',collaborator_id='1')
response=client.post('/r'+detail+'/decidir',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_morphology WHERE is_current=1').fetchone()[0]==1
page=client.get('/a/alternativas/2/proponer?mode=relation')
assert 'name="component_count"' not in page.text
assert 'value="2"' not in re.search('<select name="target_id".*?</select>',page.text,re.S).group()
data=next(f for f in Forms(page.text).forms if f.get('kind')=='RELATION')
data.update(target_id='1',parameter='CM_1',collaborator_id='1')
response=client.post('/a/alternativas/2/proponer?mode=relation',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_relation').fetchone()[0]==0
    sid=db.execute('SELECT max(submission_id) FROM submission').fetchone()[0]
detail='/aportes/alternativas/'+str(sid)
page=client.get('/m'+detail);assert page.status_code==200,page.text
data=Forms(page.text).forms[0];data.update(decision='accepted',collaborator_id='1')
response=client.post('/m'+detail+'/decidir',data=data);assert response.status_code==302,response.text
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0]==1
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
assert client.get('/a/alternativas/2/gestionar').status_code==404
assert client.get('/r/alternativas/2/gestionar').status_code==200
assert client.get('/a/aportes').status_code==200
'''
            result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


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
