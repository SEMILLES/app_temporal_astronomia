"""Pre-staging corrections; every file database is newly created under temp."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from database import crear_esquema
from classification_schema import TABLES, SEMANTIC_FIELDS, KNOWLEDGE_AREAS
from concept_classification import apply_metadata, administer
from edit_concurrency import edit_token, edit_state, check_edit, unsign, StaleEdit
from submission_concept_resolution import save_resolution, ConceptResolutionError
from alternative_workflow import create_alternative_submission
from tests import test_concept_classification as fixtures


class ExplicitMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pre19a.db'
        self.backup = self.path.with_name('pre19a.pre_migration_022.db')
        self.migration = importlib.import_module('migrations.022_concept_classification')
        with sqlite3.connect(self.path) as db:
            with patch('classification_schema.install'):
                crear_esquema(db)
            db.execute("INSERT INTO concept(preferred_label,semantic_field_1,semantic_field_2,knowledge_area_1,knowledge_area_2) VALUES('LEGACY','B','A','Astronomía','Otro')")
            db.execute("INSERT INTO source(source_name) VALUES('FUENTE SINTÉTICA')")
            db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'GLOSA')")
            db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
            db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
            db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'ALTERNATIVE','pending')")
            db.execute("INSERT INTO submission_concept_resolution(submission_id,concept_id,resolution_action,access_role) VALUES(1,1,'CONFIRM_REFERENCE','reviewer')")
            for number in (1, 2):
                snapshot = json.dumps({'concepts': [], 'fixture_version': number})
                db.execute('''INSERT INTO catalog_publication(version_number,snapshot_json,snapshot_sha256,
                    change_summary_json,publication_comment,published_access_role,concept_count,alternative_count,occurrence_count,relation_count)
                    VALUES(?,?,?,'{}','Synthetic snapshot','master',0,0,0,0)''',
                    (number,snapshot,hashlib.sha256(snapshot.encode()).hexdigest()))
        db.close()

    def content(self, path=None):
        with sqlite3.connect(path or self.path) as db:
            result = '\n'.join(db.iterdump())
        db.close()
        return result

    def apply(self, **kwargs):
        return self.migration.migrate(self.path, apply=True, report=lambda _: None, **kwargs)

    def test_refuses_without_authorization_or_existing_path(self):
        before = self.content()
        with self.assertRaisesRegex(RuntimeError, '--apply'):
            self.migration.migrate(self.path)
        with self.assertRaisesRegex(RuntimeError, 'no existe'):
            self.migration.migrate(Path(self.temp.name)/'missing.db', apply=True)
        self.assertEqual(before, self.content())
        self.assertFalse(self.backup.exists())
        self.assertFalse((Path(self.temp.name)/'missing.db').exists())

    def test_success_backup_seeds_legacy_and_all_existing_rows(self):
        before = self.content()
        db = sqlite3.connect(self.path)
        schema = {r[0]:list(self.migration.columns(db,r[0])) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        original = self.migration.existing_rows(db,schema)
        db.close()
        messages = []
        self.assertTrue(self.migration.migrate(self.path, apply=True, report=messages.append))
        self.assertTrue(self.backup.is_file())
        self.assertEqual(before,self.content(self.backup))
        self.assertIn(str(self.backup), ' '.join(messages))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(original,self.migration.existing_rows(db,schema))
            self.assertIn('classification_decision_json',self.migration.columns(db,'submission_concept_resolution'))
            self.assertIsNone(db.execute('SELECT classification_decision_json FROM submission_concept_resolution').fetchone()[0])
            for code,expected in (('semantic-fields',SEMANTIC_FIELDS),('knowledge-areas',KNOWLEDGE_AREAS)):
                actual = tuple(r[0] for r in db.execute('SELECT c.name FROM classification_category c JOIN classification_system s USING(system_id) WHERE s.code=? ORDER BY c.display_order',(code,)))
                self.assertEqual(expected,actual)
            for table in TABLES[3:]:
                self.assertEqual(0,db.execute(f'SELECT count(*) FROM {table}').fetchone()[0])
            self.assertEqual([],db.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual([('ok',)],db.execute('PRAGMA integrity_check').fetchall())
        db.close()

    def test_idempotent_after_legitimate_catalog_rename(self):
        self.apply()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE classification_category SET name='Renombrado',name_key='renombrado',active=0 WHERE category_id=1")
        db.close()
        before, backup = self.content(), self.backup.read_bytes()
        self.assertFalse(self.apply())
        self.assertEqual(before,self.content())
        self.assertEqual(backup,self.backup.read_bytes())

    def test_incompatible_resolution_rejected_before_backup(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP INDEX one_current_submission_concept_resolution')
        db.close()
        before = self.content()
        with self.assertRaisesRegex(RuntimeError,'Índice pre-19A'):
            self.apply()
        self.assertEqual(before,self.content())
        self.assertFalse(self.backup.exists())

    def test_missing_resolution_column_rejected_before_backup(self):
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE submission_concept_resolution RENAME COLUMN resolution_note TO wrong_note')
        db.close()
        before = self.content()
        with self.assertRaisesRegex(RuntimeError,'submission_concept_resolution'):
            self.apply()
        self.assertEqual(before,self.content())
        self.assertFalse(self.backup.exists())

    def test_partial_installation_rejected_without_repair(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE collection(collection_id INTEGER PRIMARY KEY)')
        db.close()
        before = self.content()
        with self.assertRaisesRegex(RuntimeError,'parcial'):
            self.apply()
        self.assertEqual(before,self.content())
        self.assertFalse(self.backup.exists())

    def test_never_overwrites_backup_and_backup_failure_prevents_install(self):
        self.backup.write_bytes(b'previous backup artifact')
        before = self.content()
        with self.assertRaisesRegex(RuntimeError,'Backup fallido'):
            self.apply()
        self.assertEqual(before,self.content())
        self.assertEqual(b'previous backup artifact',self.backup.read_bytes())
        with patch.object(self.migration,'consistent_backup',side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(RuntimeError,'disk failure'):
                self.apply(backup_path=Path(self.temp.name)/'another.db')
        self.assertEqual(before,self.content())

    def test_postvalidation_failure_rolls_back_and_keeps_backup(self):
        before = self.content()
        with patch.object(self.migration,'check_installed',side_effect=RuntimeError('validation failure')):
            with self.assertRaisesRegex(RuntimeError,'Backup conservado'):
                self.apply()
        self.assertEqual(before,self.content())
        self.assertEqual(before,self.content(self.backup))

    def test_row_mutation_causes_rollback_even_with_identical_counts(self):
        before = self.content()
        original_install = self.migration.install
        def faulty_install(db):
            original_install(db)
            if db.execute('PRAGMA database_list').fetchone()[2]:
                db.execute("UPDATE concept SET preferred_label='UNEXPECTED'")
        with patch.object(self.migration,'install',side_effect=faulty_install):
            with self.assertRaisesRegex(RuntimeError,'conteo o contenido'):
                self.apply()
        self.assertEqual(before,self.content())
        self.assertEqual(before,self.content(self.backup))

    def test_missing_trigger_causes_rollback(self):
        before = self.content()
        original_install = self.migration.install
        def faulty_install(db):
            original_install(db)
            if db.execute('PRAGMA database_list').fetchone()[2]:
                db.execute('DROP TRIGGER immutable_classification_decision')
        with patch.object(self.migration,'install',side_effect=faulty_install):
            with self.assertRaisesRegex(RuntimeError,'immutable_classification_decision'):
                self.apply()
        self.assertEqual(before,self.content())

    def test_wrong_seed_causes_rollback(self):
        before = self.content()
        original_install = self.migration.install
        def faulty_install(db):
            original_install(db)
            if db.execute('PRAGMA database_list').fetchone()[2]:
                db.execute("UPDATE classification_category SET name='WRONG',name_key='wrong' WHERE category_id=1")
        with patch.object(self.migration,'install',side_effect=faulty_install):
            with self.assertRaisesRegex(RuntimeError,'Seeds 19A'):
                self.apply()
        self.assertEqual(before,self.content())
        self.assertEqual(before,self.content(self.backup))

    def test_integrity_failure_causes_rollback(self):
        before = self.content()
        with patch.object(self.migration,'check_integrity',side_effect=[None,None,RuntimeError('integrity failure')]):
            with self.assertRaisesRegex(RuntimeError,'integrity failure'):
                self.apply()
        self.assertEqual(before,self.content())
        self.assertEqual(before,self.content(self.backup))

    def test_startup_remains_validation_only_before_and_after_bootstrap(self):
        import database
        before = self.content()
        with patch.object(database,'BASE_DATOS',self.path), patch.object(database,'USING_EXPLICIT_DATABASE',True), \
             patch.object(database,'crear_base',side_effect=AssertionError('Unexpected automatic migration')):
            with self.assertRaisesRegex(RuntimeError,'tablas requeridas'):
                database.preparar_base_para_startup()
            self.assertEqual(before,self.content())
            self.apply()
            database.preparar_base_para_startup()
            db = sqlite3.connect(self.path)
            try:
                database.validar_columnas_produccion(db)
            finally:
                db.close()

    def test_backup_includes_committed_wal_content(self):
        keeper = sqlite3.connect(self.path)
        try:
            keeper.execute('PRAGMA journal_mode=WAL')
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute("UPDATE concept SET semantic_field_1='WAL value'")
            keeper.commit()
            self.apply()
            with sqlite3.connect(self.backup) as backup:
                self.assertEqual('WAL value',backup.execute('SELECT semantic_field_1 FROM concept').fetchone()[0])
                self.assertNotIn('classification_decision_json',self.migration.columns(backup,'submission_concept_resolution'))
            backup.close()
        finally:
            keeper.close()

    def test_cli_standalone_without_runtime_and_requires_apply(self):
        root = Path(__file__).resolve().parents[1]
        bundle = Path(self.temp.name)/'bootstrap'
        (bundle/'migrations').mkdir(parents=True)
        for relative in ('migrations/022_concept_classification.py','classification_schema.py'):
            (bundle/relative).write_bytes((root/relative).read_bytes())
        command = [sys.executable,'-B','-S',str(bundle/'migrations/022_concept_classification.py'),'--database',str(self.path)]
        refused = subprocess.run(command,cwd=bundle,capture_output=True,text=True)
        self.assertEqual(1,refused.returncode,refused.stderr)
        self.assertIn('--apply',refused.stderr)
        self.assertFalse(self.backup.exists())
        applied = subprocess.run(command+['--apply'],cwd=bundle,capture_output=True,text=True)
        self.assertEqual(0,applied.returncode,applied.stderr)
        self.assertTrue(self.backup.exists())


class ScopedConceptConcurrencyTests(unittest.TestCase):
    def setUp(self):
        fixtures.ClassificationTests.setUp(self)
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = 'scope-tests'
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.sid = create_alternative_submission(self.db,1,'NEW',phonological_relation_answer='NO',
            morphology={'component_count_not_applicable':True},access_role='analyst')

    def token(self, target=None):
        return edit_token(self.db,'submission_concept',self.sid,metadata_target=target)

    def metadata(self, concept, values=None, collections=None):
        apply_metadata(self.db,concept,{'classifications':{self.sf:values or []},'collections':collections or {}},access_role='reviewer')

    def save(self, token, target=1, **kwargs):
        return save_resolution(self.db,self.sid,'USE_EXISTING',concept_id=target,note='Revisión',access_role='reviewer',
            expected_edit_token=token,**kwargs)

    def test_unrelated_metadata_does_not_invalidate_open_review(self):
        token = self.token()
        self.metadata(2,self.fields[:1],{self.collection:'join'})
        state = edit_state(self.db,'submission_concept',self.sid)
        self.assertEqual([],state['classification_history'])
        self.assertEqual([],state['memberships'])
        self.save(token,concept_metadata={'classifications':{self.sf:self.fields[1:2]}},metadata_reviewed=True,metadata_target=1)

    def test_related_metadata_does_invalidate(self):
        token = self.token()
        self.metadata(1,self.fields[:1])
        with self.assertRaises(StaleEdit):
            self.save(token)

    def test_catalogs_stay_global(self):
        token = self.token()
        administer(self.db,'category',identifier=self.areas[0],name='Nuevo nombre',access_role='master')
        with self.assertRaises(StaleEdit):
            self.save(token)

    def test_closed_history_detects_revert_leave_and_rejoin(self):
        self.metadata(1,[],{self.collection:'join'})
        token = self.token()
        self.metadata(1,self.fields[:1])
        self.metadata(1,[],{self.collection:'leave'})
        self.metadata(1,[],{self.collection:'join'})
        with self.assertRaises(StaleEdit):
            self.save(token)

    def test_destination_must_match_signature_with_or_without_payload(self):
        token = self.token()
        for payload in (None,{'classifications':{self.sf:self.fields[:1]}}):
            with self.subTest(payload=payload), self.assertRaises(ConceptResolutionError):
                self.save(token,target=2,metadata_target=2,concept_metadata=payload)
        scope = unsign(token)['concept_scope']
        self.assertEqual([1],scope['concept_ids'])
        self.assertEqual(1,scope['target_concept_id'])

    def test_fresh_destination_token_validates_and_protects_new_target(self):
        token = self.token(2)
        self.assertEqual([1,2],unsign(token)['concept_scope']['concept_ids'])
        self.metadata(2,self.fields[:1])
        with self.assertRaises(StaleEdit):
            self.save(token,target=2,metadata_target=2)
        self.save(self.token(2),target=2,metadata_target=2)

    def test_previous_global_concept_list_is_preserved(self):
        token = self.token()
        self.db.execute("UPDATE concept SET preferred_label='RENAMED' WHERE concept_id=2")
        self.db.commit()
        with self.assertRaises(StaleEdit):
            self.save(token)

    def test_accept_proposal_existing_target_is_signed_and_checked(self):
        self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('DOS','pending')")
        self.db.execute('UPDATE alternative_submission SET reference_concept_id=NULL,reference_concept_proposal_id=1 WHERE submission_id=?',(self.sid,))
        self.db.execute('UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1 WHERE occurrence_id=1')
        self.db.commit()
        wrong = self.token(1)
        with self.assertRaises(ConceptResolutionError):
            save_resolution(self.db,self.sid,'ACCEPT_PROPOSAL',access_role='reviewer',expected_edit_token=wrong,metadata_target=1)
        token = self.token()
        self.assertEqual(2,unsign(token)['concept_scope']['target_concept_id'])
        self.metadata(2,self.fields[:1])
        with self.assertRaises(StaleEdit):
            save_resolution(self.db,self.sid,'ACCEPT_PROPOSAL',access_role='reviewer',expected_edit_token=token)
        save_resolution(self.db,self.sid,'ACCEPT_PROPOSAL',access_role='reviewer',expected_edit_token=self.token(),metadata_target=2)

    def test_create_new_is_supported_without_existing_destination(self):
        token = self.token()
        save_resolution(self.db,self.sid,'CREATE_NEW',label='NUEVO',note='Concept nuevo',access_role='reviewer',expected_edit_token=token)
        self.assertEqual(3,self.db.execute('SELECT concept_id FROM submission_concept_resolution WHERE submission_id=?',(self.sid,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
