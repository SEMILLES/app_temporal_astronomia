import importlib.util, sqlite3, tempfile, unittest
from pathlib import Path
from database import crear_esquema

ROOT=Path(__file__).resolve().parents[1]
def load():
    spec=importlib.util.spec_from_file_location("migration014",ROOT/"migrations/014_catalog_publication.py"); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
class CatalogPublicationMigrationTests(unittest.TestCase):
    def test_empty_idempotent_partial_and_fresh_equivalence(self):
        migration=load()
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite"; db=sqlite3.connect(path); crear_esquema(db)
            for trigger in migration.TRIGGERS: db.execute(f"DROP TRIGGER {trigger}")
            for table in ("publication_open_conflict","catalog_publication"): db.execute(f"DROP TABLE {table}")
            db.commit(); db.close()
            self.assertTrue(migration.migrate(path,None)); self.assertFalse(migration.migrate(path,None))
            db=sqlite3.connect(path); self.assertTrue(migration.migration_is_complete(db)); self.assertEqual(db.execute("SELECT count(*) FROM catalog_publication").fetchone()[0],0); db.close()
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"partial.sqlite"; db=sqlite3.connect(path); crear_esquema(db); db.execute("DROP TRIGGER immutable_catalog_publication_update"); db.commit(); db.close()
            with self.assertRaisesRegex(RuntimeError,"parcial"): migration.migrate(path,None)
if __name__=="__main__": unittest.main()
