import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("migration010",ROOT/"migrations/010_alternative_nomenclature_history.py")
MIGRATION=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MIGRATION)


class AlternativeNomenclatureMigrationTests(unittest.TestCase):
    def test_post009_to_010_preserves_labels_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite"; db=sqlite3.connect(path); database.crear_esquema(db)
            db.execute("DROP TABLE renumber_change"); db.execute("DROP TABLE renumber_event")
            db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
            db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1a",),("0MISC",)])
            db.commit(); before=db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id").fetchall(); db.close()
            self.assertTrue(MIGRATION.migrate(path,Path(raw)/"backup.db")); self.assertFalse(MIGRATION.migrate(path,Path(raw)/"unused.db"))
            db=sqlite3.connect(path); self.assertEqual(db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id").fetchall(),before)
            self.assertEqual(db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],0); self.assertEqual(db.execute("SELECT count(*) FROM renumber_change").fetchone()[0],0); self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[]); db.close()

    def test_partial_installation_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite"; db=sqlite3.connect(path); database.crear_esquema(db); db.execute("DROP TABLE renumber_change"); db.commit(); db.close()
            with self.assertRaises(RuntimeError): MIGRATION.migrate(path,None)


if __name__=="__main__": unittest.main()
