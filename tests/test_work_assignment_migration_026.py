import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database
from work_assignment_schema import install as install_025, validate_schema as validate_025
from work_assignment_reviewer_schema import validate_schema
from work_assignments import assign, remove

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('migration026', ROOT / 'migrations/026_work_assignment_reviewer_roles.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class Migration026Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'legacy025.db'
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA foreign_keys=ON')
        database.crear_esquema(self.db)
        self.db.execute('DROP TABLE concept_work_assignment')
        install_025(self.db)
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('Concepto')")
        self.db.executemany('INSERT INTO collaborator(display_name) VALUES(?)', [('Ana',), ('Carlos',)])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def other_rows(self):
        return {name: self.db.execute(f'SELECT * FROM "{name}"').fetchall()
                for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('concept_work_assignment','sqlite_sequence')")}

    def seed_history(self):
        assign(self.db, [1], [1], actor_id=2, access_role='master')
        remove(self.db, 1, actor_id=2, access_role='master')
        assign(self.db, [1], [1, 2], actor_id=2, access_role='master')
        # Preserve a high-water mark greater than all remaining row IDs.
        self.db.execute("INSERT INTO concept_work_assignment(work_assignment_id,concept_id,analyst_id,analyst_name_snapshot,created_access_role,active,removed_at,removed_access_role) VALUES(500,1,1,'Ana','master',0,'2026-01-01','master')")
        self.db.execute('DELETE FROM concept_work_assignment WHERE work_assignment_id=500')
        self.db.commit()

    def test_empty_preview_apply_and_second_run(self):
        before = self.path.read_bytes()
        self.assertEqual(migration.migrate(self.path)['changes'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        validate_025(self.db)
        result = migration.migrate(self.path, apply=True)
        self.assertTrue(Path(result['backup']).is_file())
        validate_schema(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM concept_work_assignment').fetchone()[0], 0)
        before = self.path.read_bytes()
        result = migration.migrate(self.path, apply=True)
        self.assertEqual(result['changes'], 0)
        self.assertIsNone(result['backup'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_preserve_active_history_ids_all_fields_sequence_and_other_tables(self):
        self.seed_history()
        before = self.db.execute('SELECT * FROM concept_work_assignment ORDER BY work_assignment_id').fetchall()
        others = self.other_rows()
        migration.migrate(self.path, apply=True)
        self.assertEqual(self.db.execute('SELECT * FROM concept_work_assignment ORDER BY work_assignment_id').fetchall(), before)
        self.assertEqual(self.other_rows(), others)
        self.assertEqual(self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='concept_work_assignment'").fetchone()[0], 500)
        remove(self.db, 2, actor_id=2, access_role='reviewer')
        assign(self.db, [1], [1], actor_id=2, access_role='reviewer')
        self.assertEqual(self.db.execute('SELECT max(work_assignment_id) FROM concept_work_assignment').fetchone()[0], 501)
        self.assertEqual(self.db.execute('SELECT created_access_role FROM concept_work_assignment WHERE work_assignment_id=501').fetchone()[0], 'reviewer')
        self.assertEqual(self.db.execute('SELECT removed_access_role FROM concept_work_assignment WHERE work_assignment_id=2').fetchone()[0], 'reviewer')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(self.other_rows(), others)

    def test_roles_checks_unique_index_and_foreign_keys(self):
        migration.migrate(self.path, apply=True)
        self.assertEqual(assign(self.db, [1], [1], access_role='reviewer'), 1)
        self.assertEqual(remove(self.db, 1, access_role='master'), 1)
        self.assertEqual(assign(self.db, [1], [1], access_role='master'), 1)
        self.assertEqual(remove(self.db, 2, access_role='reviewer'), 1)
        assign(self.db, [1], [1], access_role='master')
        self.assertEqual(assign(self.db, [1], [1], access_role='master'), 0)
        invalid = [
            "UPDATE concept_work_assignment SET created_access_role='analyst' WHERE work_assignment_id=1",
            "UPDATE concept_work_assignment SET removed_access_role='analyst' WHERE work_assignment_id=1",
            "UPDATE concept_work_assignment SET removed_access_role=NULL WHERE work_assignment_id=1",
            "UPDATE concept_work_assignment SET removed_at=NULL WHERE work_assignment_id=1",
            "UPDATE concept_work_assignment SET removed_access_role='reviewer' WHERE work_assignment_id=3",
            "UPDATE concept_work_assignment SET removed_at='2026-01-01' WHERE work_assignment_id=3",
            "INSERT INTO concept_work_assignment(concept_id,analyst_id,analyst_name_snapshot,created_access_role) VALUES(1,1,'Ana','reviewer')",
            "INSERT INTO concept_work_assignment(concept_id,analyst_id,analyst_name_snapshot,created_access_role) VALUES(999,2,'Carlos','reviewer')",
            "INSERT INTO concept_work_assignment(concept_id,analyst_id,analyst_name_snapshot,created_access_role) VALUES(1,2,'Carlos','analyst')",
        ]
        for sql in invalid:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)
            self.db.rollback()
        indexes = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='concept_work_assignment'")}
        self.assertEqual(indexes, {'one_active_concept_work_assignment', 'idx_work_assignment_analyst'})

    def test_empty_table_preserves_deleted_high_water_mark(self):
        self.seed_history()
        self.db.execute('DELETE FROM concept_work_assignment')
        self.db.commit()
        migration.migrate(self.path, apply=True)
        assign(self.db, [1], [1], access_role='reviewer')
        self.assertEqual(self.db.execute('SELECT work_assignment_id FROM concept_work_assignment').fetchone()[0], 501)

    def test_failed_final_validation_rolls_back_rebuild(self):
        self.seed_history()
        before = list(self.db.iterdump())
        checks = 0

        def fail_on_actual_final(db):
            nonlocal checks
            checks += 1
            if checks == 4:
                raise ValueError('Fallo simulado de validación final')
            return validate_schema(db)

        with patch.object(migration, 'validate_schema', side_effect=fail_on_actual_final):
            with self.assertRaisesRegex(ValueError, 'Fallo simulado'):
                migration.migrate(self.path, apply=True)
        self.assertEqual(checks, 4)
        self.assertEqual(list(self.db.iterdump()), before)
        validate_025(self.db)

    def test_unexpected_dependents_and_schema_are_rejected(self):
        self.db.execute('CREATE TABLE dependent(id INTEGER REFERENCES CONCEPT_WORK_ASSIGNMENT(work_assignment_id))')
        self.db.commit()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'referencian'):
            migration.migrate(self.path, apply=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_startup_requires_explicit_026(self):
        with patch.object(database, 'BASE_DATOS', self.path):
            with self.assertRaisesRegex(ValueError, '026'):
                database.validar_base_explicita()
            migration.migrate(self.path, apply=True)
            database.validar_base_explicita()


if __name__ == '__main__':
    unittest.main()
