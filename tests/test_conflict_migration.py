import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import crear_esquema

ROOT=Path(__file__).resolve().parents[1]
def load_migration():
    spec=importlib.util.spec_from_file_location("migration013",ROOT/"migrations"/"013_permanent_conflicts.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class ConflictMigrationTests(unittest.TestCase):
    def test_migration_is_empty_idempotent_and_matches_fresh_schema(self):
        migration=load_migration()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"db.sqlite";db=sqlite3.connect(path);crear_esquema(db)
            for table in migration.EXPECTED:db.execute(f"DROP TABLE {table}")
            db.commit();db.close()
            self.assertTrue(migration.migrate(path,None));self.assertFalse(migration.migrate(path,None))
            db=sqlite3.connect(path)
            self.assertTrue(migration.migration_is_complete(db))
            self.assertEqual([0,0,0],[db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in migration.EXPECTED])
            self.assertEqual([],db.execute("PRAGMA foreign_key_check").fetchall());db.close()

    def test_partial_installation_is_rejected(self):
        migration=load_migration()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"db.sqlite";db=sqlite3.connect(path);crear_esquema(db);db.execute("DROP TABLE conflict_resolution_attempt");db.commit();db.close()
            with self.assertRaisesRegex(RuntimeError,"parcial"):migration.migrate(path,None)
