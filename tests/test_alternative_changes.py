import importlib.util
import sqlite3
import subprocess
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
        db.execute("UPDATE submission SET status='resolved',resolution='accepted' WHERE submission_id=30")
        sid=db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'GRAMMAR','pending')").lastrowid
        self.assertGreater(sid,70);db.commit()
        db.execute('BEGIN IMMEDIATE');self.assertEqual(module.migration(db)['changes'],0);db.commit()


if __name__=='__main__':unittest.main()
