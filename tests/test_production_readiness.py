import hashlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import database


ROOT = Path(__file__).resolve().parents[1]


class ProductionReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "persistent.db"
        with closing(sqlite3.connect(self.path)) as connection:
            database.crear_esquema(connection)
            connection.commit()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("LESICO_", "FLASK_"))}
        self.env.update(LESICO_ENV="production", LESICO_DATABASE_PATH=str(self.path),
                        LESICO_SECRET_KEY="test-only-external-stable-key")

    def run_code(self, code="import app", success=True):
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                env=self.env, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("test-only-external-stable-key", result.stderr)
        return result

    def test_missing_database_has_no_fallback(self):
        del self.env["LESICO_DATABASE_PATH"]
        result = self.run_code(success=False)
        self.assertIn("LESICO_DATABASE_PATH", result.stderr)

    def test_missing_file_is_not_created(self):
        missing = self.path.with_name("missing.db")
        self.env["LESICO_DATABASE_PATH"] = str(missing)
        self.assertIn("no existe", self.run_code(success=False).stderr)
        self.assertFalse(missing.exists())

    def test_missing_or_blank_secret(self):
        for value in (None, "", "  "):
            if value is None:
                self.env.pop("LESICO_SECRET_KEY", None)
            else:
                self.env["LESICO_SECRET_KEY"] = value
            self.assertIn("LESICO_SECRET_KEY", self.run_code(success=False).stderr)

    def test_wsgi_startup_read_only_and_connection_settings(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        del self.env["LESICO_ENV"]
        self.env["FLASK_DEBUG"] = "1"
        self.run_code('''
from wsgi import application
import database
assert not application.debug
assert application.secret_key == "test-only-external-stable-key"
c = database.conectar()
assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
assert c.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
c.close()
''')
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_incompatible_columns_fail(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("ALTER TABLE concept DROP COLUMN knowledge_area_1")
            connection.commit()
        self.assertIn("esquema incompatible", self.run_code(success=False).stderr)

    def test_incompatible_tables_fail(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DROP TABLE application_setting")
            connection.commit()
        self.assertIn("tablas requeridas", self.run_code(success=False).stderr)

    def test_development_default_prepares_only_temporary_database(self):
        self.env.pop("LESICO_ENV")
        self.env.pop("LESICO_DATABASE_PATH")
        self.env.pop("LESICO_SECRET_KEY")
        self.run_code(f'''
import database
from pathlib import Path
assert database.BASE_DATOS == database.DEFAULT_BASE_DATOS
database.BASE_DATOS = Path({str(self.path)!r})
import app
assert app.app.secret_key is None
''')

    def test_invalid_mode_fails(self):
        self.env["LESICO_ENV"] = "prodution"
        self.assertIn("LESICO_ENV", self.run_code(success=False).stderr)

    def test_wsgi_rejects_development(self):
        self.env["LESICO_ENV"] = "development"
        self.assertIn("LESICO_ENV=production", self.run_code("import wsgi", False).stderr)

    def test_removed_production_database_is_not_recreated(self):
        self.run_code('''
import database
import sqlite3
database.preparar_base_para_startup()
database.BASE_DATOS.unlink()
try:
    database.conectar()
except sqlite3.OperationalError:
    pass
else:
    raise AssertionError("Database recreated")
assert not database.BASE_DATOS.exists()
''')

    def test_direct_production_launch_rejects_development_server(self):
        self.assertIn("servidor WSGI", self.run_code(
            "import runpy; runpy.run_module('app', run_name='__main__')", False).stderr)
