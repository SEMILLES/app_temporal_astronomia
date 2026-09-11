from contextlib import closing
import importlib
import sqlite3
import tempfile
from pathlib import Path

from tests.test_submission_lexical_decision import LexicalFixture

migration = importlib.import_module('migrations.021_submission_lexical_decision')


class LexicalMigrationTests(LexicalFixture):
    def test_new_installation(self):
        self.assertTrue(migration.migration_is_complete(self.db))
        self.assertEqual([],self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_previous_database_backup_no_backfill_idempotence(self):
        self.db.execute('DROP TABLE submission_lexical_decision');self.db.commit()
        original=self.dump()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.db'
            with closing(sqlite3.connect(path)) as disk: self.db.backup(disk)
            self.assertTrue(migration.migrate(path))
            backup=path.with_name('old.pre_migration_021.db')
            content=backup.read_bytes()
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(original,'\n'.join(db.iterdump()))
            self.assertFalse(migration.migrate(path))
            self.assertEqual(content,backup.read_bytes())
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(0,db.execute('SELECT count(*) FROM submission_lexical_decision').fetchone()[0])
                db.execute('DROP TABLE submission_lexical_decision');db.commit()
                self.assertEqual(original,'\n'.join(db.iterdump()))
            with self.assertRaises(FileExistsError): migration.migrate(path)
            self.assertEqual(content,backup.read_bytes())

    def test_incompatible_table(self):
        self.db.execute('DROP TABLE submission_lexical_decision')
        self.db.execute('CREATE TABLE submission_lexical_decision(submission_id INTEGER PRIMARY KEY)')
        with self.assertRaises(RuntimeError): migration.migration_is_complete(self.db)

    def test_invalid_existing_fk_fails(self):
        self.db.execute('DROP TABLE submission_lexical_decision');self.db.commit()
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.db.execute('UPDATE alternative SET concept_id=999');self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.db'
            with closing(sqlite3.connect(path)) as disk: self.db.backup(disk)
            with self.assertRaises(RuntimeError): migration.migrate(path)

    def test_direct_sql_checks_and_fk(self):
        self.save()
        row=dict(self.db.execute('SELECT * FROM submission_lexical_decision').fetchone())
        self.db.execute('DELETE FROM submission_lexical_decision');self.db.commit()
        for changes in [
            {'submission_id':999}, {'concept_resolution_id':999}, {'decision_action':'BAD'},
            {'access_role':'analyst'}, {'concept_label_snapshot':' '},
            {'assignment_effect':'CREATED'}, {'assignment_effect':'REPLACED'},
            {'decision_action':'CREATE_NEW'}, {'relations_resolution':'ACCEPTED'},
            {'morphology_resolution':'ACCEPTED'}, {'morphology_result_id':999},
            {'decision_action':'REJECT_REST'}, {'assignment_result_id':None},
            {'assignment_effect':'UNCHANGED'}, {'relations_resolution':'BAD'},
        ]:
            values=dict(row,**changes)
            with self.subTest(changes=changes),self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f"INSERT INTO submission_lexical_decision ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",tuple(values.values()))
            self.db.rollback()
