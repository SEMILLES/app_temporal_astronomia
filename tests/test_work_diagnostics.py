import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import database
from work_assignments import assign, concept_diagnostics, list_concepts, my_work

ROOT = Path(__file__).resolve().parents[1]


class WorkDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'diagnostics.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        database.crear_esquema(self.db)
        self.db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('Uno',), ('Sin alternativas',), ('Otro',)])
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Ana')")
        self.db.execute("INSERT INTO source(source_name) VALUES('Fuente sintética')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def alternative(self, label, concept=1, retired=None):
        return self.db.execute('INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(?,?,?)',
                               (concept, label, retired)).lastrowid

    def occurrence(self, alternative=None, *, current=1, reference=1):
        identifier = self.db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'Evidencia')").lastrowid
        if alternative:
            self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id,is_current) VALUES(?,?,?)',
                            (identifier, alternative, current))
        if reference:
            self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,?)',
                            (identifier, reference))
        return identifier

    def test_phonological_suffixes_and_current_relations_only(self):
        identifiers = [self.alternative(label) for label in ('1a', '1b', '1c', '1d', '1z')]
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,is_current) VALUES(1,2,'CM_1',0)")
        result = concept_diagnostics(self.db, [1])[1]
        self.assertEqual([r['alternative_id'] for r in result['relations']], identifiers[1:])
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(2,3,'CM_1')")
        result = concept_diagnostics(self.db, [1])[1]
        self.assertEqual([r['alternative_id'] for r in result['relations']], identifiers[3:])
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(2,4,'UB')")
        result = concept_diagnostics(self.db, [1])[1]
        self.assertEqual([r['alternative_id'] for r in result['relations']], [identifiers[4]])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM alternative_relation WHERE is_current=1').fetchone()[0], 2)

    def test_morphology_current_and_partial_compound_is_not_a_blocker(self):
        self.alternative('1a')
        self.alternative('2a')
        self.alternative('3a')
        self.alternative('4b', retired='2026-01-01')
        self.db.execute('INSERT INTO alternative_morphology(alternative_id,is_current) VALUES(1,0)')
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation) VALUES(2,4,'NO')")
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count_not_applicable,free_permutation) VALUES(3,1,'N/A')")
        # Deliberately no materialized components and no canonical videos.
        result = concept_diagnostics(self.db, [1])[1]
        self.assertEqual(result['alternative_count'], 3)
        self.assertEqual([r['alternative_id'] for r in result['morphology']], [1])
        self.assertEqual(result['relations'], [])
        self.assertEqual(result['grammar'], [])

    def test_grammar_only_current_assignments_and_current_grammar(self):
        active = self.alternative('1a')
        retired = self.alternative('2a', retired='2026-01-01')
        missing = self.occurrence(active)
        historical = self.occurrence(active)
        present = self.occurrence(active)
        self.occurrence()  # Reference alone does not make a grammar task.
        self.occurrence(active, current=0)
        self.occurrence(retired)
        self.db.execute('INSERT INTO occurrence_grammar(occurrence_id,is_current) VALUES(?,0)', (historical,))
        self.db.execute('INSERT INTO occurrence_grammar(occurrence_id) VALUES(?)', (present,))
        result = concept_diagnostics(self.db, [1])[1]
        self.assertEqual([r['occurrence_id'] for r in result['grammar']], [missing, historical])

    def test_assignment_requires_current_reference_and_no_current_assignment(self):
        alternative = self.alternative('1b')
        missing = self.occurrence()
        historical = self.occurrence(alternative, current=0)
        assigned = self.occurrence(alternative)
        self.occurrence(reference=None)
        old_reference = self.occurrence()
        self.db.execute('UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=?', (old_reference,))
        other = self.occurrence(reference=3)
        result = concept_diagnostics(self.db, [1, 2, 3])
        self.assertEqual([r['occurrence_id'] for r in result[1]['assignment']], [missing, historical])
        self.assertEqual([r['occurrence_id'] for r in result[3]['assignment']], [other])
        self.assertEqual(result[2]['assignment'], [])
        self.assertEqual([r['occurrence_id'] for r in result[1]['grammar']], [assigned])
        self.assertNotIn('analysis_occurrence_id', result[1]['morphology'][0])

    def test_batched_read_only_query_count_and_empty_concept_assignment(self):
        for concept in (1, 3):
            self.alternative('1b', concept=concept)
        self.db.commit()
        with self.assertRaises(ValueError):
            assign(self.db, [2], [1], access_role='reviewer')
        self.assertEqual(my_work(self.db, 1)['concepts'], [])
        before = list(self.db.iterdump())
        self.db.execute('PRAGMA query_only=ON')
        queries = []
        self.db.set_trace_callback(queries.append)
        result = concept_diagnostics(self.db, [1, 2, 3])
        self.db.set_trace_callback(None)
        self.assertEqual(len(queries), 4)
        self.assertEqual(result[2], dict(alternative_count=0, morphology=[], relations=[], grammar=[], assignment=[]))
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(list_concepts(self.db)['diagnostics'], result)

    def pending(self, **filters):
        return list_concepts(self.db, pending_only=True, **filters)

    def test_pending_summary_counts_filters_order_and_pagination(self):
        first = self.alternative('1b')
        other = self.alternative('1a', concept=3)
        grammar = self.occurrence(first)
        assignment = self.occurrence(reference=1)
        self.occurrence(reference=None)
        self.occurrence(other, reference=3)
        data = self.pending(per_page=1)
        self.assertEqual([(r['concept_id'], r['total']) for r in data['summary']], [(1, 4), (3, 2)])
        self.assertEqual(data['summary'][0]['counts'], dict(morphology=1, relations=1, grammar=1, assignment=1))
        self.assertEqual(data['task_total'], 6)
        self.assertEqual(data['pages'], 2)
        self.assertEqual(self.pending(page=2, per_page=1)['concepts'][0]['concept_id'], 3)
        self.assertEqual([r['concept_id'] for r in self.pending(concept_id='3')['concepts']], [3])
        selected = self.pending(concept_id=1, work_type='morphology')
        self.assertEqual(selected['task_total'], 1)
        self.assertEqual(selected['summary'][0]['total'], 4)
        self.assertEqual(selected['diagnostics'][1]['relations'], [])
        self.assertEqual(selected['diagnostics'][1]['grammar'], [])
        self.assertEqual(selected['diagnostics'][1]['assignment'], [])
        self.assertEqual(selected['diagnostics'][1]['morphology'][0]['alternative_id'], first)
        self.assertEqual(self.pending(work_type='morphology')['task_total'], 2)
        self.assertEqual(self.pending(concept_id=3, work_type='assignment')['concepts'], [])
        self.assertEqual(self.pending(concept_id=999)['concepts'], [])
        self.assertEqual(data['diagnostics'][1]['grammar'][0]['occurrence_id'], grammar)
        self.assertEqual(data['diagnostics'][1]['assignment'][0]['occurrence_id'], assignment)
        # A zero-pending Concept is absent; ties use the label before the ID.
        self.alternative('2a', concept=3)
        self.occurrence(reference=3)
        self.assertEqual([r['concept_id'] for r in self.pending()['concept_options']], [3, 1])

    def test_current_state_and_history_do_not_duplicate_summary(self):
        a = self.alternative('1a')
        b = self.alternative('1b')
        o = self.occurrence(a)
        missing = self.occurrence(reference=3)
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id,is_current) VALUES(?,?,0)', (o, b))
        self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id,is_current) VALUES(?,1,0)', (missing,))
        self.db.execute('INSERT INTO alternative_morphology(alternative_id,is_current) VALUES(?,0)', (a,))
        self.db.execute('INSERT INTO occurrence_grammar(occurrence_id,is_current) VALUES(?,0)', (o,))
        self.db.commit()
        assign(self.db, [1], [1], access_role='reviewer')
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Otro analista')")
        self.db.commit()
        assign(self.db, [1], [2], access_role='reviewer')
        data = self.pending()
        self.assertEqual(data['summary'][0]['total'], 4)
        self.assertEqual(data['diagnostics'][3]['assignment'][0]['occurrence_id'], missing)
        self.db.execute('INSERT INTO alternative_morphology(alternative_id) VALUES(?)', (a,))
        self.db.execute('INSERT INTO alternative_morphology(alternative_id) VALUES(?)', (b,))
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,'CM_1')", (a, b))
        self.db.execute('INSERT INTO occurrence_grammar(occurrence_id) VALUES(?)', (o,))
        self.assertEqual([r['concept_id'] for r in self.pending()['concept_options']], [3])
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (missing, a))
        self.assertEqual(self.pending(work_type='assignment')['task_total'], 0)

    def test_pending_proposals_are_annotations_not_extra_tasks(self):
        from alternative_change_workflow import create_proposal, review_proposal
        a = self.alternative('1a')
        b = self.alternative('1b')
        self.db.commit()
        before = self.pending()['task_total']
        actor = dict(collaborator_id=1, access_role='analyst')
        sid = create_proposal(self.db, b, 'MORPHOLOGY', {'component_count': 1}, **actor)
        for parameter in ('CM_1', 'OR_M1'):
            create_proposal(self.db, b, 'RELATION', {'target_id': a, 'parameter': parameter}, **actor)
        self.assertEqual(self.pending()['task_total'], before)
        review_proposal(self.db, sid, 'accepted', collaborator_id=1, access_role='reviewer')
        self.assertEqual(self.pending()['task_total'], before - 1)

    def test_pending_grammar_submission_is_annotation_not_extra_task(self):
        from grammar_workflow import create_grammar_submission, resolve_grammar_submission
        alternative = self.alternative('1a')
        occurrence = self.occurrence(alternative)
        self.db.commit()

        before = self.pending()['task_total']

        sid = create_grammar_submission(
            self.db,
            occurrence,
            {'gender': 'FEM-A'},
            collaborator_id=1,
            access_role='analyst',
        )

        data = self.pending()
        self.assertEqual(data['task_total'], before)
        item = next(
            row for row in data['diagnostics'][1]['grammar']
            if row['occurrence_id'] == occurrence
        )
        self.assertEqual(
            [row['submission_id'] for row in item['pending_submissions']],
            [sid],
        )

        resolve_grammar_submission(
            self.db,
            sid,
            'rejected',
            review_note='Revisar analisis',
            collaborator_id=1,
            access_role='reviewer',
        )

        item = next(
            row for row in self.pending()['diagnostics'][1]['grammar']
            if row['occurrence_id'] == occurrence
        )
        self.assertEqual(item['pending_submissions'], [])

    def test_grammar_follows_current_assignment_and_assignment_current_reference(self):
        old = self.alternative('1a')
        current = self.alternative('1b', concept=3)
        occurrence = self.occurrence(old)
        unassigned = self.occurrence(reference=1)
        self.db.execute('UPDATE assignment SET is_current=0 WHERE occurrence_id=?', (occurrence,))
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (occurrence, current))
        for identifier in (occurrence, unassigned):
            self.db.execute('UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=?', (identifier,))
            self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,3)', (identifier,))
        result = self.pending()['diagnostics']
        self.assertEqual(result[1]['grammar'], [])
        self.assertEqual(result[1]['assignment'], [])
        self.assertEqual([r['occurrence_id'] for r in result[3]['grammar']], [occurrence])
        self.assertEqual([r['occurrence_id'] for r in result[3]['assignment']], [unassigned])
        self.assertEqual(result[3]['relations'][0]['alternative_id'], current)
        self.assertEqual(result[3]['morphology'][0]['alternative_id'], current)

    def test_invalid_filters_and_scoped_options(self):
        self.alternative('1a')
        self.alternative('1b', concept=3)
        self.db.commit()
        assign(self.db, [1], [1], access_role='reviewer')
        for filters in ({'concept_id': 'bad'}, {'concept_id': '-1'}, {'concept_id': '9' * 100},
                        {'work_type': 'MORPHOLOGY'}, {'page': 'bad'}, {'analyst_id': '9' * 100}):
            with self.assertRaises(ValueError):
                self.pending(**filters)
        self.assertEqual([r['concept_id'] for r in self.pending(analyst_id=1)['concept_options']], [1])
        self.assertEqual(self.pending(analyst_id=1, concept_id=3)['concepts'], [])
        self.assertEqual([r['concept_id'] for r in self.pending(status='unassigned')['summary']], [3])

    def test_views_links_permissions_and_existing_submission_workflow(self):
        alternative = self.alternative('1b')
        self.occurrence(alternative)
        self.alternative('2b')  # Alternative consultation does not depend on evidence.
        self.occurrence()
        self.db.execute("UPDATE source SET source_name='Fuente de prueba',legacy_source_code='25ID',source_type='MATERIAL_IMPRESO'")
        self.db.execute("UPDATE occurrence SET source_detail_1='Seccion A',source_detail_1_status='VALUE',source_detail_2='17',source_detail_2_status='VALUE'")
        self.db.commit()
        assign(self.db, [1], [1], access_role='reviewer')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LESICO_', 'RAILWAY_'))}
        env.update(LESICO_ENV='development', LESICO_DATABASE_PATH=str(self.path),
                   LESICO_SECRET_KEY='synthetic-only', LESICO_ANALYST_ROUTE='a',
                   LESICO_REVIEWER_ROUTE='r', LESICO_MASTER_ROUTE='m')
        code = '''from app import app
from database import conectar
from contextlib import closing
client=app.test_client()
with closing(conectar()) as db:
    before=list(db.iterdump())
analyst=client.get('/a/mi-trabajo?collaborator_id=1')
assert analyst.status_code==200
html=analyst.get_data(as_text=True)
assert 'Morfología: 2 pendientes' in html and 'Relaciones: 2 pendientes' in html
assert 'Gramática: 1 pendientes · no bloqueantes' in html
assert 'Asignación a alternativa: 1 pendientes' in html
assert html.count('(25ID) Fuente de prueba')==2
assert html.count('Página: 17')==2
assert '/a/ocurrencias/1/gramatica' in html and '/a/ocurrencias/2/clasificar' in html
assert '/a/ocurrencias/1/clasificar' not in html
assert '/a/alternativas/1/proponer?mode=morphology' in html
assert '/a/alternativas/2/proponer?mode=relation' in html
assert '/gestionar' not in html
assert 'aún no está disponible' not in html
assert client.get('/a/alternativas/1/proponer').status_code==200
assert client.get('/a/alternativas/2/proponer').status_code==200
for role in ('r','m'):
    response=client.get('/'+role+'/administracion/asignaciones')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert '/'+role+'/ocurrencias/2/clasificar' in html
    assert 'Trabajo encontrado: 6 tareas' in html and 'bloqueante' in html
    for anchor in ('morfologia','relaciones'):
        link='/'+role+'/alternativas/1/gestionar#'+anchor
        assert link in html
        assert client.get(link).status_code==200
    assert client.get('/'+role+'/ocurrencias/1/gramatica').status_code==200
    for kind, count in (('morphology', 2), ('relations', 2), ('grammar', 1), ('assignment', 1)):
        filtered=client.get('/'+role+'/administracion/asignaciones?concept_id=1&work_type='+kind)
        assert filtered.status_code==200
        assert 'Trabajo encontrado: '+str(count)+' tareas' in filtered.text
    assert 'Trabajo encontrado: 0 tareas' in client.get('/'+role+'/administracion/asignaciones?concept_id=999').text
for path in ('/ocurrencias/1/gramatica','/ocurrencias/2/clasificar','/conceptos/1/alternativas'):
    assert client.get('/a'+path).status_code==200
for path in ('/ocurrencias/1/gramatica','/ocurrencias/2/clasificar','/alternativas/1/proponer'):
    page=client.get('/a'+path).get_data(as_text=True)
    assert '(25ID) Fuente de prueba' in page and 'Página: 17' in page
assert client.get('/a/alternativas/1/gestionar').status_code==404
assert client.post('/a/alternativas/1/gestionar',data={'action':'morphology'}).status_code==404
assert client.post('/a/ocurrencias/1/gramatica/aceptacion-inmediata/confirmar').status_code==404
assert client.post('/a/aportes/1/decidir').status_code==404
with closing(conectar()) as db:
    assert list(db.iterdump())==before
    assert db.execute('SELECT COUNT(*) FROM concept_work_assignment WHERE analyst_id!=1').fetchone()[0]==0
response=client.post('/a/ocurrencias/1/gramatica',data={'gender':'FEM-A','note':'Propuesta local'})
assert response.status_code==302
response=client.post('/a/ocurrencias/2/clasificar',data={'proposal_kind':'EXISTING','proposed_existing_alternative_id':'1'})
assert response.status_code==302
with closing(conectar()) as db:
    assert db.execute('SELECT COUNT(*) FROM occurrence_grammar').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM alternative_morphology').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM alternative_relation').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM assignment').fetchone()[0]==1
    assert {tuple(r) for r in db.execute('SELECT submission_type,status FROM submission')}=={('GRAMMAR','pending'),('ALTERNATIVE','pending')}
    grammar_sid=db.execute(
        "SELECT submission_id FROM submission WHERE submission_type='GRAMMAR' AND status='pending'"
    ).fetchone()[0]
html=client.get('/a/mi-trabajo?collaborator_id=1').text
assert 'En revisión' in html
assert '/a/aportes/'+str(grammar_sid) in html
assert '/a/ocurrencias/1/gramatica' not in html
assert client.get('/r/aportes/pendientes').status_code==200
from alternative_change_workflow import create_proposal,review_proposal
with closing(conectar()) as db:
    sid=create_proposal(db,1,'MORPHOLOGY',{'component_count':1},collaborator_id=1,access_role='analyst')
html=client.get('/a/mi-trabajo?collaborator_id=1').text
assert 'Morfología: 2 pendientes' in html
assert 'En revisión' in html and '/a/aportes/alternativas/'+str(sid) in html
assert '/a/alternativas/1/proponer?mode=morphology' not in html
with closing(conectar()) as db:
    review_proposal(db,sid,'rejected',collaborator_id=1,access_role='reviewer',note='Revisar análisis')
html=client.get('/a/mi-trabajo?collaborator_id=1').text
assert '/a/alternativas/1/proponer?mode=morphology' in html
with closing(conectar()) as db:
    sid=create_proposal(db,1,'MORPHOLOGY',{'component_count':1},collaborator_id=1,access_role='analyst')
    review_proposal(db,sid,'accepted',collaborator_id=1,access_role='reviewer')
assert 'Morfología: 1 pendientes' in client.get('/a/mi-trabajo?collaborator_id=1').text
'''
        result = subprocess.run([sys.executable, '-c', code], env=env, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
