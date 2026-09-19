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
        assign(self.db, [2], [1], access_role='reviewer')
        self.assertEqual(my_work(self.db, 1)['diagnostics'][2]['alternative_count'], 0)
        before = list(self.db.iterdump())
        self.db.execute('PRAGMA query_only=ON')
        queries = []
        self.db.set_trace_callback(queries.append)
        result = concept_diagnostics(self.db, [1, 2, 3])
        self.db.set_trace_callback(None)
        self.assertEqual(len(queries), 3)
        self.assertEqual(result[2], dict(alternative_count=0, morphology=[], relations=[], grammar=[], assignment=[]))
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(list_concepts(self.db)['diagnostics'], result)

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
assert '/a/alternativas/1/proponer#morfologia' in html
assert '/a/alternativas/2/proponer#relaciones' in html
assert '/gestionar' not in html
assert 'aún no está disponible' not in html
assert client.get('/a/alternativas/1/proponer').status_code==200
assert client.get('/a/alternativas/2/proponer').status_code==200
for role in ('r','m'):
    response=client.get('/'+role+'/administracion/asignaciones')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert '/'+role+'/ocurrencias/2/clasificar' in html
    assert 'Morfología: 2 pendientes' in html and 'bloqueantes' in html
    for anchor in ('morfologia','relaciones'):
        link='/'+role+'/alternativas/1/gestionar#'+anchor
        assert link in html
        assert client.get(link).status_code==200
    assert client.get('/'+role+'/ocurrencias/1/gramatica').status_code==200
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
assert client.get('/r/aportes/pendientes').status_code==200
'''
        result = subprocess.run([sys.executable, '-c', code], env=env, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
