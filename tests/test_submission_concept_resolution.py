import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
import database
from alternative_workflow import create_alternative_submission, review_as_existing, review_as_new, reject_alternative_submission
from submission_concept_resolution import save_resolution, current_resolution, resolution_history, ConceptResolutionError
from routes.submissions import submissions_bp
from concept_labels import alternative_display_label
from tests.form_client import hidden


class LocalConceptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',2000,2000,'known')")
        self.db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('X',),('Y',)])
        self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('X','pending')")
        for oid in (1,2):
            self.db.execute("INSERT INTO occurrence(occurrence_id,source_id,original_gloss,occurrence_year) VALUES(?,1,'G',2000)", (oid,))
            self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(?,1)', (oid,))
        self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(?,'1a')", [(1,),(2,)])
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        self.db.commit()
        self.a = self.create(1)
        self.b = self.create(2)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def create(self, oid):
        return create_alternative_submission(self.db, oid, 'NEW', phonological_relation_answer='NO', morphology={'component_count_not_applicable':True})

    def save(self, sid=None, action='USE_EXISTING', **kwargs):
        return save_resolution(self.db, sid or self.a, action, access_role='reviewer', **kwargs)

    def snapshot(self):
        return '\n'.join(self.db.iterdump())

    def test_shared_proposal_independent_and_assignment_only_on_acceptance(self):
        proposal = tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone())
        original = [tuple(r) for r in self.db.execute('SELECT * FROM alternative_submission')]
        assignment = [tuple(r) for r in self.db.execute('SELECT * FROM assignment')]
        self.save(concept_id=1)
        self.assertIsNone(current_resolution(self.db,self.b))
        self.assertEqual('pending',self.db.execute('SELECT status FROM submission WHERE submission_id=?',(self.b,)).fetchone()[0])
        self.save(self.b,concept_id=2,note='Otro significado')
        self.assertEqual([1,2],[current_resolution(self.db,s)['concept_id'] for s in (self.a,self.b)])
        self.assertEqual(proposal,tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone()))
        self.assertEqual(original,[tuple(r) for r in self.db.execute('SELECT * FROM alternative_submission')])
        self.assertEqual(assignment,[tuple(r) for r in self.db.execute('SELECT * FROM assignment')])
        review_as_existing(self.db,self.b,2)
        self.assertEqual(2,self.db.execute('SELECT alternative_id FROM assignment WHERE occurrence_id=2 AND is_current=1').fetchone()[0])
        self.assertEqual(1,self.db.execute('SELECT alternative_id FROM assignment WHERE occurrence_id=1 AND is_current=1').fetchone()[0])

    def test_versioning_and_required_note(self):
        first=self.save(concept_id=1)
        before=self.snapshot()
        with self.assertRaises(ConceptResolutionError): self.save(concept_id=2)
        self.assertEqual(before,self.snapshot())
        second=self.save(concept_id=2,note='Corrección')
        history=resolution_history(self.db,self.a)
        self.assertEqual([second,first],[r['submission_concept_resolution_id'] for r in history])
        self.assertEqual([1,0],[r['is_current'] for r in history])
        self.assertEqual(first,history[0]['supersedes_submission_concept_resolution_id'])
        refs=self.db.execute('SELECT * FROM occurrence_concept_reference WHERE occurrence_id=1 ORDER BY occurrence_concept_reference_id').fetchall()
        self.assertEqual([0,0,1],[r['is_current'] for r in refs])
        self.assertEqual(refs[1]['occurrence_concept_reference_id'],refs[2]['supersedes_occurrence_concept_reference_id'])
        self.assertEqual('pending',self.db.execute('SELECT status FROM submission WHERE submission_id=?',(self.a,)).fetchone()[0])
        self.assertEqual(1,self.db.execute('SELECT count(*) FROM assignment').fetchone()[0])

    def test_reject_rest_retains_concept_and_assignment(self):
        self.save(concept_id=2,note='Corrección')
        assignments=[tuple(r) for r in self.db.execute('SELECT * FROM assignment')]
        reject_alternative_submission(self.db,self.a)
        self.assertEqual(2,current_resolution(self.db,self.a)['concept_id'])
        self.assertEqual(assignments,[tuple(r) for r in self.db.execute('SELECT * FROM assignment')])
        self.assertEqual(('resolved','rejected'),tuple(self.db.execute('SELECT status,resolution FROM submission WHERE submission_id=?',(self.a,)).fetchone()))
        with self.assertRaises(ConceptResolutionError): self.save(concept_id=1)

    def test_no_closure_or_inline_resolution_without_local_concept(self):
        for operation in (lambda:review_as_existing(self.db,self.a,1),lambda:review_as_new(self.db,self.a),lambda:reject_alternative_submission(self.db,self.a),lambda:review_as_new(self.db,self.a,concept_resolution={'action':'new','label':'Z'})):
            before=self.snapshot()
            with self.assertRaises(ValueError): operation()
            self.assertEqual(before,self.snapshot())

    def test_create_new_and_rejected_global_proposal(self):
        self.db.execute("UPDATE concept_proposal SET status='rejected'")
        self.db.commit()
        before=tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone())
        self.save(action='CREATE_NEW',label='Z',note='Concepto distinto')
        cid=current_resolution(self.db,self.a)['concept_id']
        with self.assertRaises(ValueError): review_as_existing(self.db,self.a,1)
        aid=review_as_new(self.db,self.a)
        self.assertEqual(cid,self.db.execute('SELECT concept_id FROM alternative WHERE alternative_id=?',(aid,)).fetchone()[0])
        self.assertEqual(before,tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone()))

    def test_resolved_global_proposal_is_not_authority(self):
        self.db.execute("UPDATE concept_proposal SET status='resolved',resolved_concept_id=1")
        self.db.commit()
        self.save(concept_id=2,note='Otro significado en esta ocurrencia')
        review_as_existing(self.db,self.a,2)
        self.assertEqual(('resolved',1),tuple(self.db.execute('SELECT status,resolved_concept_id FROM concept_proposal').fetchone()))
        self.assertEqual(2,current_resolution(self.db,self.a)['concept_id'])
        self.assertIsNone(current_resolution(self.db,self.b))

    def test_collaborator_snapshot_and_direct_reference_correction(self):
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Revisor')")
        self.db.execute('UPDATE alternative_submission SET reference_concept_proposal_id=NULL,reference_concept_id=1 WHERE submission_id=?',(self.a,))
        self.db.commit()
        with self.assertRaises(ConceptResolutionError): self.save(concept_id=2)
        self.save(concept_id=2,note='Corrección de referencia',collaborator_id=1)
        resolution=current_resolution(self.db,self.a)
        self.assertEqual((1,'Revisor','reviewer'),tuple(resolution[key] for key in ('collaborator_id','collaborator_name_snapshot','access_role')))
        self.assertEqual(1,self.db.execute('SELECT reference_concept_id FROM alternative_submission WHERE submission_id=?',(self.a,)).fetchone()[0])
        self.assertEqual(1,self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='submission_concept_resolved'").fetchone()[0])

    def test_same_proposed_new_label_needs_no_note(self):
        self.db.execute("UPDATE concept_proposal SET proposed_label='NUEVO-CONCEPTO'")
        self.db.commit()
        self.save(action='CREATE_NEW',label='nuevo concepto')
        self.assertEqual('NUEVO-CONCEPTO',current_resolution(self.db,self.a)['preferred_label'])
        self.assertEqual('pending',self.db.execute('SELECT status FROM concept_proposal').fetchone()[0])

    def test_accept_proposal_creates_or_reuses_normalized_concept_locally(self):
        for label, action in [('nuevo concepto', 'CREATE_NEW'), (' Nuevo--Concepto ', 'USE_EXISTING')]:
            with self.subTest(action=action):
                self.db.execute('UPDATE concept_proposal SET proposed_label=?', (label,))
                self.db.commit()
                proposal = tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone())
                assignments = [tuple(r) for r in self.db.execute('SELECT * FROM assignment')]
                self.save(action='ACCEPT_PROPOSAL', concept_id=2, label='IGNORADO')
                resolution = current_resolution(self.db, self.a)
                self.assertEqual('CREATE_NEW', resolution['resolution_action'])
                self.assertEqual('NUEVO-CONCEPTO', resolution['preferred_label'])
                self.assertIsNone(resolution['resolution_note'])
                self.assertEqual(3, self.db.execute('SELECT count(*) FROM concept').fetchone()[0])
                self.assertEqual(proposal, tuple(self.db.execute('SELECT * FROM concept_proposal').fetchone()))
                self.assertEqual(assignments, [tuple(r) for r in self.db.execute('SELECT * FROM assignment')])
                self.assertIsNone(current_resolution(self.db, self.b))
                self.assertEqual('pending', self.db.execute('SELECT status FROM submission WHERE submission_id=?', (self.a,)).fetchone()[0])
        self.assertEqual([1], [r['is_current'] for r in resolution_history(self.db,self.a)])

    def test_identical_resolution_is_noop_with_normalized_note(self):
        first = self.save(concept_id=1)
        for note in (None, '', ' \t\n'):
            with self.subTest(note=note):
                before = self.snapshot()
                self.assertEqual(first, self.save(concept_id='1', note=note))
                self.assertEqual(before, self.snapshot())
                self.assertFalse(self.db.in_transaction)
        self.assertEqual(1, len(resolution_history(self.db, self.a)))

    def test_repeated_proposal_acceptance_has_no_side_effects(self):
        self.db.execute("UPDATE concept_proposal SET proposed_label='Nuevo concepto'")
        self.db.commit()
        first = self.save(action='ACCEPT_PROPOSAL')
        before = self.snapshot()
        references = self.db.execute('SELECT count(*) FROM occurrence_concept_reference').fetchone()[0]
        events = self.db.execute('SELECT count(*) FROM activity_event').fetchone()[0]
        self.assertEqual(first, self.save(action='ACCEPT_PROPOSAL', note=''))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(references, self.db.execute('SELECT count(*) FROM occurrence_concept_reference').fetchone()[0])
        self.assertEqual(events, self.db.execute('SELECT count(*) FROM activity_event').fetchone()[0])
        self.assertEqual('CREATE_NEW', current_resolution(self.db, self.a)['resolution_action'])
        self.assertEqual([first], [r['submission_concept_resolution_id'] for r in resolution_history(self.db,self.a)])

    def test_changed_note_versions_same_concept(self):
        first = self.save(concept_id=1)
        second = self.save(concept_id=1, note=' Nota adicional ')
        self.assertNotEqual(first, second)
        history = resolution_history(self.db, self.a)
        self.assertEqual([1,0], [r['is_current'] for r in history])
        self.assertEqual(first, history[0]['supersedes_submission_concept_resolution_id'])
        self.assertEqual('Nota adicional', history[0]['resolution_note'])
        before = self.snapshot()
        self.assertEqual(second, self.save(concept_id=1, note='  Nota adicional\n'))
        self.assertEqual(before, self.snapshot())
        third = self.save(concept_id=1, note='')
        self.assertNotEqual(second, third)
        self.assertIsNone(current_resolution(self.db,self.a)['resolution_note'])

    def test_noop_preserves_outer_transaction(self):
        first = self.save(concept_id=1)
        before = self.snapshot()
        self.db.execute('BEGIN')
        self.db.execute("UPDATE concept_proposal SET proposed_label='Cambio temporal'")
        self.assertEqual(first, self.save(concept_id=1))
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(before, self.snapshot())

    def test_accept_proposal_requires_proposal_and_preserves_change_note_rule(self):
        self.save(concept_id=2,note='Corrección')
        before=self.snapshot()
        with self.assertRaises(ConceptResolutionError): self.save(action='ACCEPT_PROPOSAL')
        self.assertEqual(before,self.snapshot())
        self.save(action='ACCEPT_PROPOSAL',note='Volver a la propuesta original')
        self.db.execute('UPDATE alternative_submission SET reference_concept_proposal_id=NULL,reference_concept_id=1 WHERE submission_id=?',(self.a,))
        self.db.commit()
        with self.assertRaises(ConceptResolutionError): self.save(action='ACCEPT_PROPOSAL')

    def test_direct_confirmation_and_future_submission_not_approved(self):
        self.save(concept_id=1)
        reject_alternative_submission(self.db,self.a)
        sid=self.create(1)
        self.assertIsNone(current_resolution(self.db,sid))
        original=tuple(self.db.execute('SELECT * FROM alternative_submission WHERE submission_id=?',(sid,)).fetchone())
        self.save(sid,action='CONFIRM_REFERENCE')
        self.assertEqual(1,current_resolution(self.db,sid)['concept_id'])
        self.assertEqual(original,tuple(self.db.execute('SELECT * FROM alternative_submission WHERE submission_id=?',(sid,)).fetchone()))

    def test_legacy_reference_not_mutated(self):
        self.db.execute('UPDATE alternative_submission SET is_legacy=1,reference_concept_proposal_id=NULL WHERE submission_id=?',(self.a,))
        self.db.commit()
        self.save(concept_id=2,note='Identificación explícita')
        review_as_existing(self.db,self.a,2)
        self.assertIsNone(self.db.execute('SELECT reference_concept_id FROM alternative_submission WHERE submission_id=?',(self.a,)).fetchone()[0])

    def test_atomic_rollback_outer_transaction_and_activity_failure(self):
        self.db.execute("CREATE TRIGGER fail_resolution_activity BEFORE INSERT ON activity_event BEGIN SELECT RAISE(ABORT,'failure'); END")
        self.db.commit()
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError): self.save(action='CREATE_NEW',label='Z',note='Cambio')
        self.assertEqual(before,self.snapshot())
        self.db.execute('DROP TRIGGER fail_resolution_activity')
        self.db.commit()
        before=self.snapshot()
        self.db.execute('BEGIN')
        self.save(concept_id=1)
        self.db.rollback()
        self.assertEqual(before,self.snapshot())

    def test_type_permissions_and_unique_current(self):
        with self.assertRaises(ConceptResolutionError): save_resolution(self.db,self.a,'USE_EXISTING',concept_id=1,access_role='analyst')
        sid=self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'GRAMMAR','pending')").lastrowid
        self.db.commit()
        with self.assertRaises(ConceptResolutionError): self.save(sid,concept_id=1)
        self.save(concept_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO submission_concept_resolution(submission_id,concept_id,resolution_action,access_role) VALUES(?,1,'USE_EXISTING','reviewer')",(self.a,))
        self.db.rollback()

    def test_migration_has_no_backfill_and_is_idempotent(self):
        self.db.execute('DROP TABLE submission_concept_resolution')
        self.db.commit()
        before=[tuple(r) for r in self.db.execute('SELECT * FROM submission')]
        migration=importlib.import_module('migrations.020_submission_concept_resolution')
        self.assertTrue(migration.migrate(self.path))
        self.assertFalse(migration.migrate(self.path))
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM submission_concept_resolution').fetchone()[0])
        self.assertEqual(before,[tuple(r) for r in self.db.execute('SELECT * FROM submission')])
        self.assertEqual([],self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_routes_permissions_stale_and_historical_display(self):
        app=Flask(__name__,template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        app.secret_key='test'
        app.register_blueprint(submissions_bp)
        app.jinja_env.filters['alternative_display_label']=alternative_display_label
        role=['reviewer']
        @app.before_request
        def actor(): g.current_access_role=role[0]
        client=app.test_client()
        with patch('routes.submissions.conectar',lambda: self.connect()):
            page=client.get(f'/aportes/{self.a}').get_data(as_text=True)
            self.assertIn('Aceptar el concepto propuesto por el analista',page)
            self.assertIn('value="ACCEPT_PROPOSAL" selected',page)
            self.assertIn('data-concept-action="USE_EXISTING" hidden',page)
            self.assertIn('data-concept-action="CREATE_NEW" hidden',page)
            self.assertIn('<fieldset disabled><legend>Resolución del resto del análisis',page)
            token=hidden(page,'concept_edit_token')
            form={'concept_edit_token':token,'concept_action':'ACCEPT_PROPOSAL'}
            role[0]='analyst'
            self.assertEqual(404,client.post(f'/aportes/{self.a}/concepto',data=form).status_code)
            role[0]='reviewer'
            self.assertEqual(302,client.post(f'/aportes/{self.a}/concepto',data=form).status_code)
            self.assertEqual(409,client.post(f'/aportes/{self.a}/concepto',data=form).status_code)
            page=client.get(f'/aportes/{self.a}').get_data(as_text=True)
            self.assertIn('Estado: Resuelto',page)
            self.assertIn('<details class="concept-resolution-editor"><summary>Modificar resolución</summary>',page)
            self.assertIn('value="USE_EXISTING" selected',page)
            self.assertIn('<option value="1" selected>X</option>',page)
            self.assertIn('Concepto resuelto. Ya es posible continuar con la decisión sobre la alternativa.',page)
            self.assertIn('<fieldset><legend>Resolución del resto del análisis',page)
            form={'concept_edit_token':hidden(page,'concept_edit_token'),'concept_action':'USE_EXISTING','concept_id':'2','concept_note':'Corrección'}
            self.assertEqual(302,client.post(f'/aportes/{self.a}/concepto',data=form).status_code)
            self.assertEqual([1,0],[r['is_current'] for r in resolution_history(self.db,self.a)])
            self.assertEqual(2,current_resolution(self.db,self.a)['concept_id'])
            self.assertEqual(302,client.post(f'/aportes/{self.a}/decidir',data={'decision':'rejected'}).status_code)
            page=client.get(f'/aportes/{self.a}').get_data(as_text=True)
            self.assertIn('Concepto resuelto para esta revisión',page)
            self.db.execute("UPDATE submission SET status='resolved',resolution='rejected' WHERE submission_id=?",(self.b,));self.db.commit()
            page=client.get(f'/aportes/{self.b}').get_data(as_text=True)
            self.assertIn('No existe resolución conceptual local registrada',page)

    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
