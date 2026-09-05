import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g

import database
from routes.sources import sources_bp


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "005_source_metadata_systematization.py"
METADATA_COLUMNS = {
    "legacy_source_code", "source_scope", "format_original",
    "format_detail", "region_description", "characterization",
    "reported_entry_count",
}


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_005", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


class SourceSchemaTests(unittest.TestCase):

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_new_database_contains_source_metadata(self):
        self.assertLessEqual(METADATA_COLUMNS, table_columns(self.connection, "source"))
        self.assertLessEqual(
            METADATA_COLUMNS, table_columns(self.connection, "source_revision")
        )
        self.assertTrue(table_columns(self.connection, "source_systematization"))

    def test_source_scope_domain(self):
        for index, scope in enumerate(("INSTITUTIONAL", "PERSONAL", None)):
            with self.subTest(scope=scope):
                self.connection.execute(
                    "INSERT INTO source (source_name, source_scope) VALUES (?, ?)",
                    (f"Source {index}", scope),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO source (source_name, source_scope) VALUES (?, ?)",
                ("Invalid scope", "MIXED"),
            )

    def test_reported_entry_count_domain(self):
        for index, count in enumerate((None, 0, 25)):
            with self.subTest(count=count):
                self.connection.execute(
                    "INSERT INTO source (source_name, reported_entry_count) "
                    "VALUES (?, ?)",
                    (f"Count {index}", count),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO source (source_name, reported_entry_count) "
                "VALUES ('Negative', -1)"
            )

    def test_systematization_history_and_status_domain(self):
        source_id = self.connection.execute(
            "INSERT INTO source (source_name) VALUES ('History')"
        ).lastrowid
        history = (
            ("COMPLETE", "2026-06-01"),
            ("PARTIAL", "2027-01-01"),
            ("COMPLETE", "2027-04-01"),
        )
        self.connection.executemany(
            "INSERT INTO source_systematization "
            "(source_id, status, reviewed_at) VALUES (?, ?, ?)",
            [(source_id, status, reviewed_at) for status, reviewed_at in history],
        )
        actual = self.connection.execute(
            "SELECT status FROM source_systematization WHERE source_id = ? "
            "ORDER BY reviewed_at, source_systematization_id",
            (source_id,),
        ).fetchall()
        self.assertEqual([row[0] for row in actual], [row[0] for row in history])
        for status in ("NOT_STARTED", "UNKNOWN"):
            self.connection.execute(
                "INSERT INTO source_systematization "
                "(source_id, status, reviewed_at) VALUES (?, ?, '2025-01')",
                (source_id, status),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO source_systematization "
                "(source_id, status, reviewed_at) VALUES (?, 'DONE', '2028-01')",
                (source_id,),
            )


class Migration005Tests(unittest.TestCase):

    def test_migration_applies_to_004_shape_and_is_idempotent(self):
        migration = load_migration()
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "database.db"
            backup_path = Path(directory) / "backup.db"
            connection = sqlite3.connect(database_path)
            connection.executescript("""
                CREATE TABLE source (
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT NOT NULL UNIQUE,
                    source_type TEXT,
                    source_reference TEXT,
                    start_year INTEGER,
                    end_year INTEGER,
                    end_year_status TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    created_by TEXT,
                    updated_by TEXT
                );
                CREATE TABLE source_revision (
                    source_revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT,
                    source_reference TEXT,
                    start_year INTEGER,
                    end_year INTEGER,
                    end_year_status TEXT,
                    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    changed_by TEXT,
                    change_note TEXT,
                    FOREIGN KEY (source_id) REFERENCES source(source_id)
                );
                INSERT INTO source (source_name, source_type, source_reference)
                VALUES ('Legacy', 'old type', 'old reference');
            """)
            connection.close()

            self.assertTrue(migration.migrate(database_path, backup_path))
            self.assertTrue(backup_path.exists())
            self.assertFalse(migration.migrate(database_path, backup_path))

            connection = sqlite3.connect(database_path)
            try:
                self.assertLessEqual(METADATA_COLUMNS, table_columns(connection, "source"))
                self.assertLessEqual(
                    METADATA_COLUMNS, table_columns(connection, "source_revision")
                )
                legacy = connection.execute(
                    "SELECT source_type, source_reference FROM source"
                ).fetchone()
                self.assertEqual(legacy, ("old type", "old reference"))
                connection.execute(
                    "INSERT INTO source (source_name, source_scope, "
                    "reported_entry_count) VALUES ('Valid', 'PERSONAL', 0)"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO source (source_name, source_scope) "
                        "VALUES ('Bad scope', 'OTHER')"
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO source (source_name, reported_entry_count) "
                        "VALUES ('Bad count', -1)"
                    )
            finally:
                connection.close()


class SourceRouteTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "routes.db"
        self.previous_database_path = database.BASE_DATOS
        database.BASE_DATOS = self.database_path
        connection = sqlite3.connect(self.database_path)
        database.crear_esquema(connection)
        connection.execute(
            """
            INSERT INTO source (
                source_name, source_type, source_reference,
                legacy_source_code, source_scope, format_original,
                format_detail, region_description, characterization,
                reported_entry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Original", "legacy type", "legacy reference", "CODIGO-LEGACY-FICTICIO",
                "INSTITUTIONAL", "VIDEO", "seña + definición", "Bogotá",
                "Original characterization", 100,
            ),
        )
        connection.execute("INSERT INTO occurrence(source_id,original_gloss,source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status) VALUES(1,'G','Material','01:02','VALUE','VALUE')")
        connection.commit()
        connection.close()
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        @app.before_request
        def reviewer_context():
            g.current_access_role = "reviewer"
        app.register_blueprint(sources_bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        database.BASE_DATOS = self.previous_database_path
        self.temporary_directory.cleanup()

    def test_edit_snapshots_metadata_and_preserves_compatibility_fields(self):
        response = self.client.post(
            "/fuentes/1/actualizar",
            data={
                "source_name": "Updated",
                "source_type": "OTRO",
                "source_reference": "legacy reference",
                "legacy_source_code": "CODIGO-COMPARTIDO-FICTICIO",
                "source_scope": "PERSONAL",
                "format_original": "PAPER",
                "format_detail": "printed material",
                "start_year": "2020",
                "end_year": "2021",
                "end_year_status": "known",
                "region_description": "Medellín",
                "characterization": "Updated characterization",
                "reported_entry_count": "125",
                "change_note": "Metadata update",
            },
        )
        self.assertEqual(response.status_code, 302)

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            source = connection.execute("SELECT * FROM source").fetchone()
            revision = connection.execute("SELECT * FROM source_revision").fetchone()
            occurrence = connection.execute("SELECT source_detail_1,source_detail_2 FROM occurrence").fetchone()
        finally:
            connection.close()

        self.assertEqual(source["source_type"], "OTRO")
        self.assertEqual(source["source_reference"], "legacy reference")
        self.assertEqual(source["source_scope"], "PERSONAL")
        self.assertEqual(source["reported_entry_count"], 125)
        self.assertEqual(revision["source_name"], "Original")
        self.assertEqual(revision["source_type"], "legacy type")
        self.assertEqual(revision["source_reference"], "legacy reference")
        self.assertEqual(revision["legacy_source_code"], "CODIGO-LEGACY-FICTICIO")
        self.assertEqual(revision["source_scope"], "INSTITUTIONAL")
        self.assertEqual(revision["format_original"], "VIDEO")
        self.assertEqual(revision["format_detail"], "seña + definición")
        self.assertEqual(revision["region_description"], "Bogotá")
        self.assertEqual(revision["characterization"], "Original characterization")
        self.assertEqual(revision["reported_entry_count"], 100)
        self.assertEqual(tuple(occurrence),("Material","01:02"))

    def test_create_source_with_new_metadata(self):
        response = self.client.post(
            "/fuentes/nueva",
            data={
                "source_name": "New source",
                "source_type": "VIDEO_POR_SENA",
                "legacy_source_code": "CODIGO-COMPARTIDO-FICTICIO",
                "source_scope": "PERSONAL",
                "format_original": "VIDEO",
                "format_detail": "seña + definición",
                "start_year": "2024",
                "end_year_status": "ongoing",
                "region_description": "Colombia",
                "characterization": "Test source",
                "reported_entry_count": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        connection = sqlite3.connect(self.database_path)
        try:
            created = connection.execute(
                "SELECT legacy_source_code, source_scope, format_original, "
                "format_detail, start_year, end_year_status, "
                "region_description, characterization, reported_entry_count, "
                "source_type, source_reference FROM source "
                "WHERE source_name = 'New source'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            created,
            (
                "CODIGO-COMPARTIDO-FICTICIO", "PERSONAL", "VIDEO", "seña + definición", 2024,
                "ongoing", "Colombia", "Test source", 0, "VIDEO_POR_SENA", None,
            ),
        )


if __name__ == "__main__":
    unittest.main()
