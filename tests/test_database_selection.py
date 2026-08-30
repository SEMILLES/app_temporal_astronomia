import importlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import database


ROOT = Path(__file__).resolve().parents[1]


class DatabaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LESICO_DATABASE_PATH", None)
            importlib.reload(database)
        self.temporary.cleanup()

    def reload_database(self, configured_path=None):
        environment = {}
        if configured_path is not None:
            environment["LESICO_DATABASE_PATH"] = str(configured_path)
        with patch.dict(os.environ, environment, clear=False):
            if configured_path is None:
                os.environ.pop("LESICO_DATABASE_PATH", None)
            return importlib.reload(database)

    def create_usable_database(self, path):
        connection = sqlite3.connect(path)
        database.crear_esquema(connection)
        connection.commit()
        connection.close()

    def test_default_remains_prototype_database(self):
        module = self.reload_database()
        self.assertFalse(module.USING_EXPLICIT_DATABASE)
        self.assertEqual(module.BASE_DATOS, ROOT / "lesico_prototipo.db")

    def test_connections_use_exact_configured_path(self):
        path = self.directory / "selected.db"
        self.create_usable_database(path)
        module = self.reload_database(path)

        for _ in range(2):
            connection = module.conectar()
            active = Path(
                connection.execute("PRAGMA database_list").fetchone()[2]
            )
            connection.close()
            self.assertEqual(active.resolve(), path.resolve())

    def test_missing_explicit_path_fails_without_creating_file(self):
        path = self.directory / "missing.db"
        module = self.reload_database(path)

        with self.assertRaisesRegex(RuntimeError, "no existe"):
            module.preparar_base_para_startup()
        self.assertFalse(path.exists())

    def test_non_sqlite_explicit_file_fails_and_remains_intact(self):
        path = self.directory / "not-sqlite.db"
        content = b"not a sqlite database"
        path.write_bytes(content)
        module = self.reload_database(path)

        with self.assertRaisesRegex(RuntimeError, "SQLite utilizable"):
            module.preparar_base_para_startup()
        self.assertEqual(path.read_bytes(), content)

    def test_usable_explicit_database_skips_create_base(self):
        path = self.directory / "usable.db"
        self.create_usable_database(path)
        module = self.reload_database(path)

        with patch.object(
            module, "crear_base", side_effect=AssertionError("DDL inesperado")
        ) as create_base:
            output = StringIO()
            with redirect_stdout(output):
                module.preparar_base_para_startup()

        create_base.assert_not_called()
        self.assertIn(str(path.resolve()), output.getvalue())

    def test_explicit_database_missing_required_tables_fails(self):
        path = self.directory / "incomplete.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE source (source_id INTEGER)")
        connection.commit()
        connection.close()
        module = self.reload_database(path)

        with self.assertRaisesRegex(RuntimeError, "tablas requeridas"):
            module.preparar_base_para_startup()


if __name__ == "__main__":
    unittest.main()
