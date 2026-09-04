import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from routes.occurrences import occurrences_bp, validate_occurrence_year


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "006_occurrence_legacy_provenance.py"
LEGACY_COLUMNS = {
    "legacy_occurrence_id",
    "legacy_source_detail_1",
    "legacy_source_detail_2",
}


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_006", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


class OccurrenceSchemaTests(unittest.TestCase):

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)
        self.source_id = self.connection.execute(
            "INSERT INTO source (source_name) VALUES ('Synthetic source')"
        ).lastrowid

    def tearDown(self):
        self.connection.close()

    def test_legacy_columns_exist_in_occurrence_and_revision(self):
        self.assertLessEqual(LEGACY_COLUMNS, table_columns(self.connection, "occurrence"))
        self.assertLessEqual(
            LEGACY_COLUMNS, table_columns(self.connection, "occurrence_revision")
        )

    def test_legacy_occurrence_id_accepts_text_and_null(self):
        self.connection.execute(
            "INSERT INTO occurrence (source_id, legacy_occurrence_id) VALUES (?, ?)",
            (self.source_id, "1442-¿CÓMO-ESTÁ?"),
        )
        self.connection.execute(
            "INSERT INTO occurrence (source_id, legacy_occurrence_id) VALUES (?, NULL)",
            (self.source_id,),
        )
        values = self.connection.execute(
            "SELECT legacy_occurrence_id FROM occurrence ORDER BY occurrence_id"
        ).fetchall()
        self.assertEqual(values, [("1442-¿CÓMO-ESTÁ?",), (None,)])

    def test_legacy_occurrence_id_is_not_unique(self):
        for _ in range(2):
            self.connection.execute(
                "INSERT INTO occurrence (source_id, legacy_occurrence_id) "
                "VALUES (?, 'LEGACY-PRUEBA-001')",
                (self.source_id,),
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM occurrence "
                "WHERE legacy_occurrence_id = 'LEGACY-PRUEBA-001'"
            ).fetchone()[0],
            2,
        )

    def test_legacy_source_details_are_preserved_literally(self):
        detail_1 = "Video principal: seña + definición"
        detail_2 = "00:01:23 / página 7; texto  sin  normalizar"
        occurrence_id = self.connection.execute(
            "INSERT INTO occurrence (source_id, legacy_source_detail_1, "
            "legacy_source_detail_2) VALUES (?, ?, ?)",
            (self.source_id, detail_1, detail_2),
        ).lastrowid
        actual = self.connection.execute(
            "SELECT legacy_source_detail_1, legacy_source_detail_2 "
            "FROM occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        self.assertEqual(actual, (detail_1, detail_2))

    def test_occurrence_year_accepts_null_and_is_stored_as_integer(self):
        null_id = self.connection.execute(
            "INSERT INTO occurrence (source_id, occurrence_year) VALUES (?, NULL)",
            (self.source_id,),
        ).lastrowid
        year = validate_occurrence_year(self.connection, self.source_id, "2024")
        year_id = self.connection.execute(
            "INSERT INTO occurrence (source_id, occurrence_year) VALUES (?, ?)",
            (self.source_id, year),
        ).lastrowid
        actual = self.connection.execute(
            "SELECT occurrence_year, typeof(occurrence_year) FROM occurrence "
            "WHERE occurrence_id IN (?, ?) ORDER BY occurrence_id",
            (null_id, year_id),
        ).fetchall()
        self.assertEqual(actual, [(None, "null"), (2024, "integer")])

    def test_occurrence_year_respects_known_source_period(self):
        self.connection.execute(
            "UPDATE source SET start_year = 2020, end_year = 2021, "
            "end_year_status = 'known' WHERE source_id = ?",
            (self.source_id,),
        )
        for value in ("1999", "2025"):
            with self.assertRaises(ValueError):
                validate_occurrence_year(self.connection, self.source_id, value)
        self.assertEqual(validate_occurrence_year(
            self.connection, self.source_id, "2021"), 2021)

    def test_occurrence_year_rejects_invalid_values(self):
        for value in ("", "0", "0000", "24", "202A", "10000"):
            with self.subTest(value=value):
                if value == "":
                    self.assertIsNone(
                        validate_occurrence_year(self.connection, self.source_id, value)
                    )
                else:
                    with self.assertRaises(ValueError):
                        validate_occurrence_year(self.connection, self.source_id, value)


class Migration006Tests(unittest.TestCase):

    def test_migration_applies_to_005_shape_and_is_idempotent(self):
        migration = load_migration()
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "database.db"
            backup_path = Path(directory) / "backup.db"
            connection = sqlite3.connect(database_path)
            connection.executescript("""
                CREATE TABLE source_systematization (
                    source_systematization_id INTEGER PRIMARY KEY
                );
                CREATE TABLE occurrence (
                    occurrence_id INTEGER PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    original_gloss TEXT
                );
                CREATE TABLE occurrence_revision (
                    occurrence_revision_id INTEGER PRIMARY KEY,
                    occurrence_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    original_gloss TEXT
                );
                INSERT INTO occurrence (source_id, original_gloss)
                VALUES (1, 'Preserved');
            """)
            connection.close()

            self.assertTrue(migration.migrate(database_path, backup_path))
            self.assertTrue(backup_path.exists())
            self.assertFalse(migration.migrate(database_path, backup_path))

            connection = sqlite3.connect(database_path)
            try:
                self.assertLessEqual(LEGACY_COLUMNS, table_columns(connection, "occurrence"))
                self.assertLessEqual(
                    LEGACY_COLUMNS, table_columns(connection, "occurrence_revision")
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT original_gloss, legacy_occurrence_id, "
                        "legacy_source_detail_1, legacy_source_detail_2 "
                        "FROM occurrence"
                    ).fetchone(),
                    ("Preserved", None, None, None),
                )
            finally:
                connection.close()


class OccurrenceRouteTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "routes.db"
        self.previous_database_path = database.BASE_DATOS
        database.BASE_DATOS = self.database_path
        connection = sqlite3.connect(self.database_path)
        database.crear_esquema(connection)
        connection.execute(
            "INSERT INTO source (source_name, start_year, end_year, end_year_status) "
            "VALUES ('Synthetic source', 2020, 2021, 'known')"
        )
        connection.execute(
            """
            INSERT INTO occurrence (
                source_id, legacy_occurrence_id, original_gloss, hyperlink,
                legacy_source_detail_1, legacy_source_detail_2,
                source_locator, provenance_note, occurrence_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1, "244-¿CÓMO ESTÁS?", "GLOSA", "https://old.example",
                "Página  4", "Video A / 00:02:03", "locator curado",
                "Nota anterior", 2020,
            ),
        )
        connection.commit()
        connection.close()

        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.register_blueprint(occurrences_bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        database.BASE_DATOS = self.previous_database_path
        self.temporary_directory.cleanup()

    def test_edit_uses_current_documentary_fields_and_hides_legacy_inputs(self):
        connection=sqlite3.connect(self.database_path)
        connection.execute("UPDATE occurrence SET source_detail_1='VIDEO',source_detail_2='00:20',usage_examples_present=1,grammatical_info_present=1,grammatical_note='Nota fuente'")
        connection.commit();connection.close()
        html=self.client.get("/ocurrencias/1/editar").get_data(as_text=True)
        for text in ("Detalle Fuente 1","Detalle Fuente 2","Ejemplos de uso","Información gramatical en la fuente","Apunte gramatical","Nota de procedencia","Nota del cambio"):
            self.assertIn(text,html)
        self.assertNotIn('name="hyperlink"',html);self.assertNotIn('name="source_locator"',html)
        self.assertNotIn('id="grammatical-note-field" hidden',html)
        self.assertIn("note.hidden=grammar.value!=='1'",html)

    def test_edit_updates_documentary_metadata_and_preserves_legacy_fields(self):
        response=self.client.post("/ocurrencias/1/actualizar",data={"source_id":"1","original_gloss":"GLOSA","source_detail_1":"TÍTULO","source_detail_2":"01:02","occurrence_year":"2020","usage_examples_present":"1","grammatical_info_present":"1","grammatical_note":"Apunte","provenance_note":"Nueva","change_note":"Metadata"})
        self.assertEqual(response.status_code,302)
        connection=sqlite3.connect(self.database_path);row=connection.execute("SELECT source_detail_1,source_detail_2,usage_examples_present,grammatical_info_present,grammatical_note,hyperlink,source_locator FROM occurrence").fetchone();connection.close()
        self.assertEqual(row,("TÍTULO","01:02",1,1,"Apunte","https://old.example","locator curado"))

    def test_edit_preserves_and_snapshots_legacy_provenance(self):
        response = self.client.post(
            "/ocurrencias/1/actualizar",
            data={
                "source_id": "1",
                "original_gloss": "GLOSA ACTUALIZADA",
                "hyperlink": "https://new.example",
                "source_locator": "locator nuevo",
                "provenance_note": "Nota nueva",
                "occurrence_year": "2021",
                "change_note": "Corrección sintética",
            },
        )
        self.assertEqual(response.status_code, 302)

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            occurrence = connection.execute("SELECT * FROM occurrence").fetchone()
            revision = connection.execute(
                "SELECT * FROM occurrence_revision"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(occurrence["legacy_occurrence_id"], "244-¿CÓMO ESTÁS?")
        self.assertEqual(occurrence["legacy_source_detail_1"], "Página  4")
        self.assertEqual(occurrence["legacy_source_detail_2"], "Video A / 00:02:03")
        self.assertEqual(occurrence["occurrence_year"], 2021)
        self.assertEqual(revision["legacy_occurrence_id"], "244-¿CÓMO ESTÁS?")
        self.assertEqual(revision["legacy_source_detail_1"], "Página  4")
        self.assertEqual(revision["legacy_source_detail_2"], "Video A / 00:02:03")
        self.assertEqual(revision["source_locator"], "locator curado")
        self.assertEqual(revision["occurrence_year"], 2020)

    def test_edit_preserves_null_legacy_provenance_as_null(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                INSERT INTO occurrence (
                    source_id, original_gloss, source_locator, occurrence_year
                ) VALUES (1, 'NULL LEGACY', 'old locator', 2020)
                """
            )
            occurrence_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            f"/ocurrencias/{occurrence_id}/actualizar",
            data={
                "source_id": "1",
                "original_gloss": "NULL LEGACY",
                "hyperlink": "",
                "source_locator": "new locator",
                "provenance_note": "",
                "occurrence_year": "2020",
                "change_note": "Cambio sin campos legacy",
            },
        )
        self.assertEqual(response.status_code, 302)

        connection = sqlite3.connect(self.database_path)
        try:
            occurrence = connection.execute(
                "SELECT legacy_occurrence_id, legacy_source_detail_1, "
                "legacy_source_detail_2 FROM occurrence WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
            revision = connection.execute(
                "SELECT legacy_occurrence_id, legacy_source_detail_1, "
                "legacy_source_detail_2 FROM occurrence_revision "
                "WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(occurrence, (None, None, None))
        self.assertEqual(revision, (None, None, None))


if __name__ == "__main__":
    unittest.main()
