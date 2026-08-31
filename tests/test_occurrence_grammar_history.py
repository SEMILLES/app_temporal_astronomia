import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from occurrence_grammar import (
    EmptyGrammarError,
    OccurrenceNotFoundError,
    create_or_replace_occurrence_grammar,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "007_occurrence_grammar_history.py"
LINGUISTIC_COLUMNS = (
    "gender", "plural", "agentive", "conjugated_form", "negation"
)


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_007", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_info(connection):
    return {
        row[1]: {"type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]}
        for row in connection.execute("PRAGMA table_info(occurrence_grammar)")
    }


class OccurrenceGrammarSchemaTests(unittest.TestCase):

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_schema_columns_nullability_defaults_and_checks(self):
        info = table_info(self.connection)
        self.assertTrue(info)
        for column in (*LINGUISTIC_COLUMNS, "grammar_note"):
            with self.subTest(column=column):
                self.assertEqual(info[column]["type"], "TEXT")
                self.assertEqual(info[column]["notnull"], 0)
                self.assertIsNone(info[column]["default"])
        self.assertEqual(info["is_current"]["notnull"], 1)
        self.assertEqual(info["is_current"]["default"], "1")

        sql = self.connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'occurrence_grammar'"
        ).fetchone()[0]
        self.assertIn("CHECK (is_current IN (0, 1))", sql)
        for column in LINGUISTIC_COLUMNS:
            self.assertNotIn(f"{column} IN", sql)
        for value in ("SIN-MARCA", "SIN-NEG", "CON-NEG"):
            self.assertNotIn(value, sql)

    def test_foreign_keys_and_indexes(self):
        foreign_keys = {
            (row[3], row[2], row[4])
            for row in self.connection.execute(
                "PRAGMA foreign_key_list(occurrence_grammar)"
            )
        }
        self.assertIn(
            ("occurrence_id", "occurrence", "occurrence_id"), foreign_keys
        )
        self.assertIn(
            (
                "supersedes_occurrence_grammar_id",
                "occurrence_grammar",
                "occurrence_grammar_id",
            ),
            foreign_keys,
        )
        indexes = {
            row[1]: {"unique": row[2], "partial": row[4]}
            for row in self.connection.execute(
                "PRAGMA index_list(occurrence_grammar)"
            )
        }
        self.assertEqual(
            indexes["one_current_grammar_per_occurrence"],
            {"unique": 1, "partial": 1},
        )
        self.assertEqual(
            indexes["idx_occurrence_grammar_occurrence"]["unique"], 0
        )


class OccurrenceGrammarBehaviorTests(unittest.TestCase):

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)
        self.connection.execute(
            "INSERT INTO source (source_name) VALUES ('Synthetic source')"
        )
        self.connection.executemany(
            "INSERT INTO occurrence (source_id, original_gloss) VALUES (1, ?)",
            [(f"OCC-{number}",) for number in range(1, 10)],
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def grammar_rows(self, occurrence_id):
        return self.connection.execute(
            "SELECT * FROM occurrence_grammar WHERE occurrence_id = ? "
            "ORDER BY occurrence_grammar_id",
            (occurrence_id,),
        ).fetchall()

    def test_occurrence_without_grammar_is_valid(self):
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM occurrence_grammar"
            ).fetchone()[0],
            0,
        )

    def test_first_analysis_is_current_and_independent(self):
        occurrence_before = self.connection.execute(
            "SELECT * FROM occurrence WHERE occurrence_id = 1"
        ).fetchone()
        grammar_id, created = create_or_replace_occurrence_grammar(
            self.connection,
            1,
            gender="SIN-MARCA",
            negation="SIN-NEG",
            created_by="analyst",
            change_note="Initial analysis",
        )
        row = self.connection.execute(
            "SELECT occurrence_id, gender, negation, is_current, "
            "supersedes_occurrence_grammar_id "
            "FROM occurrence_grammar WHERE occurrence_grammar_id = ?",
            (grammar_id,),
        ).fetchone()
        self.assertTrue(created)
        self.assertEqual(row, (1, "SIN-MARCA", "SIN-NEG", 1, None))
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM submission").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM assignment").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM occurrence_revision"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT * FROM occurrence WHERE occurrence_id = 1"
            ).fetchone(),
            occurrence_before,
        )

    def test_each_linguistic_field_may_individually_be_null(self):
        values = {
            "gender": "SIN-MARCA",
            "plural": "SIN-MARCA",
            "agentive": "SIN-MARCA",
            "conjugated_form": "SÍ",
            "negation": "CON-NEG",
        }
        for occurrence_id, populated_column in enumerate(LINGUISTIC_COLUMNS, 1):
            kwargs = {column: None for column in LINGUISTIC_COLUMNS}
            kwargs[populated_column] = values[populated_column]
            create_or_replace_occurrence_grammar(
                self.connection, occurrence_id, **kwargs
            )
            row = self.connection.execute(
                "SELECT gender, plural, agentive, conjugated_form, negation "
                "FROM occurrence_grammar WHERE occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
            for index, column in enumerate(LINGUISTIC_COLUMNS):
                expected = values[column] if column == populated_column else None
                self.assertEqual(row[index], expected)

    def test_note_only_is_valid_and_preserved_literally(self):
        note = "La dirección  depende del contexto discursivo."
        grammar_id, created = create_or_replace_occurrence_grammar(
            self.connection, 1, grammar_note=note
        )
        row = self.connection.execute(
            "SELECT gender, plural, agentive, conjugated_form, negation, "
            "grammar_note FROM occurrence_grammar WHERE occurrence_grammar_id = ?",
            (grammar_id,),
        ).fetchone()
        self.assertTrue(created)
        self.assertEqual(row, (None, None, None, None, None, note))

    def test_empty_content_and_change_note_only_are_rejected(self):
        for kwargs in (
            {},
            {"grammar_note": ""},
            {"gender": "   ", "change_note": "Administrative note"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(EmptyGrammarError):
                    create_or_replace_occurrence_grammar(
                        self.connection, 1, **kwargs
                    )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM occurrence_grammar"
            ).fetchone()[0],
            0,
        )

    def test_correction_versions_content_note_and_change_note(self):
        first_id, _ = create_or_replace_occurrence_grammar(
            self.connection,
            1,
            conjugated_form="SIN-MARCA",
            grammar_note="Original note",
            change_note="First",
        )
        second_id, created = create_or_replace_occurrence_grammar(
            self.connection,
            1,
            conjugated_form="SÍ",
            grammar_note="Corrected note",
            change_note="Observed in context",
        )
        rows = self.connection.execute(
            "SELECT occurrence_grammar_id, conjugated_form, grammar_note, "
            "is_current, supersedes_occurrence_grammar_id, change_note "
            "FROM occurrence_grammar WHERE occurrence_id = 1 "
            "ORDER BY occurrence_grammar_id"
        ).fetchall()
        self.assertTrue(created)
        self.assertEqual(rows[0], (
            first_id, "SIN-MARCA", "Original note", 0, None, "First"
        ))
        self.assertEqual(rows[1], (
            second_id, "SÍ", "Corrected note", 1, first_id,
            "Observed in context",
        ))

    def test_multiple_corrections_keep_one_current_and_same_occurrence_chain(self):
        for value in ("SIN-NEG", "CON-NEG", "SIN-NEG"):
            create_or_replace_occurrence_grammar(
                self.connection, 1, negation=value
            )
        rows = self.connection.execute(
            "SELECT occurrence_grammar_id, occurrence_id, is_current, "
            "supersedes_occurrence_grammar_id FROM occurrence_grammar "
            "WHERE occurrence_id = 1 ORDER BY occurrence_grammar_id"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row[2] for row in rows), 1)
        self.assertEqual([row[3] for row in rows], [None, rows[0][0], rows[1][0]])
        ids = {row[0]: row[1] for row in rows}
        for row in rows[1:]:
            self.assertEqual(ids[row[3]], row[1])

    def test_same_content_is_noop_even_with_different_change_note(self):
        first_id, created = create_or_replace_occurrence_grammar(
            self.connection,
            1,
            plural="SIN-MARCA",
            grammar_note="Stable note",
            change_note="Initial",
        )
        self.assertTrue(created)
        for change_note in ("Initial", "Different reason"):
            grammar_id, created = create_or_replace_occurrence_grammar(
                self.connection,
                1,
                plural="SIN-MARCA",
                grammar_note="Stable note",
                change_note=change_note,
            )
            self.assertEqual(grammar_id, first_id)
            self.assertFalse(created)
        self.assertEqual(len(self.grammar_rows(1)), 1)

    def test_missing_occurrence_is_rejected_cleanly(self):
        with self.assertRaises(OccurrenceNotFoundError):
            create_or_replace_occurrence_grammar(
                self.connection, 999, gender="SIN-MARCA"
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM occurrence_grammar"
            ).fetchone()[0],
            0,
        )

    def test_partial_unique_index_rejects_two_current_rows(self):
        create_or_replace_occurrence_grammar(
            self.connection, 1, gender="SIN-MARCA"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO occurrence_grammar (occurrence_id, plural) "
                "VALUES (1, 'SIN-MARCA')"
            )
        self.connection.rollback()

    def test_failed_replacement_rolls_back_deactivation(self):
        first_id, _ = create_or_replace_occurrence_grammar(
            self.connection, 1, gender="SIN-MARCA"
        )
        self.connection.execute("""
            CREATE TRIGGER reject_test_grammar
            BEFORE INSERT ON occurrence_grammar
            WHEN NEW.gender = 'FAIL'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic failure');
            END
        """)
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            create_or_replace_occurrence_grammar(
                self.connection, 1, gender="FAIL"
            )
        rows = self.connection.execute(
            "SELECT occurrence_grammar_id, gender, is_current "
            "FROM occurrence_grammar WHERE occurrence_id = 1"
        ).fetchall()
        self.assertEqual(rows, [(first_id, "SIN-MARCA", 1)])


class Migration007Tests(unittest.TestCase):

    def test_migration_applies_to_006_shape_and_is_idempotent(self):
        migration = load_migration()
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "database.db"
            backup_path = Path(directory) / "backup.db"
            connection = sqlite3.connect(database_path)
            database.crear_esquema(connection)
            connection.execute("DROP TABLE occurrence_grammar")
            connection.execute(
                "INSERT INTO source (source_name) VALUES ('Preserved source')"
            )
            connection.execute(
                "INSERT INTO occurrence (source_id, original_gloss) "
                "VALUES (1, 'Preserved occurrence')"
            )
            connection.commit()
            connection.close()

            self.assertTrue(migration.migrate(database_path, backup_path))
            self.assertTrue(backup_path.exists())
            self.assertFalse(migration.migrate(database_path, backup_path))

            connection = sqlite3.connect(database_path)
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                self.assertEqual(
                    set(table_info(connection)),
                    migration.GRAMMAR_COLUMNS,
                )
                indexes = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(occurrence_grammar)"
                    )
                }
                self.assertLessEqual(migration.GRAMMAR_INDEXES, indexes)
                self.assertEqual(
                    connection.execute(
                        "SELECT original_gloss FROM occurrence"
                    ).fetchone()[0],
                    "Preserved occurrence",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM occurrence_grammar"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
            finally:
                connection.close()


class OccurrenceGrammarTransactionCompositionTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "transactions.db"
        self.connection = sqlite3.connect(self.database_path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)
        self.connection.execute(
            "INSERT INTO source (source_name) VALUES ('Transactional source')"
        )
        self.connection.execute(
            "INSERT INTO occurrence (source_id, original_gloss) "
            "VALUES (1, 'Existing occurrence')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_standalone_helper_commits_its_own_transaction(self):
        create_or_replace_occurrence_grammar(
            self.connection, 1, gender="SIN-MARCA"
        )
        self.assertFalse(self.connection.in_transaction)

        observer = sqlite3.connect(self.database_path)
        try:
            stored = observer.execute(
                "SELECT gender FROM occurrence_grammar WHERE occurrence_id = 1"
            ).fetchone()
        finally:
            observer.close()
        self.assertEqual(stored, ("SIN-MARCA",))

    def test_helper_participates_in_and_does_not_commit_outer_transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        occurrence_id = self.connection.execute(
            "INSERT INTO occurrence (source_id, original_gloss) "
            "VALUES (1, 'Created in outer transaction')"
        ).lastrowid
        create_or_replace_occurrence_grammar(
            self.connection, occurrence_id, plural="SIN-MARCA"
        )
        self.assertTrue(self.connection.in_transaction)
        self.connection.commit()

        observer = sqlite3.connect(self.database_path)
        try:
            stored = observer.execute(
                "SELECT o.original_gloss, g.plural "
                "FROM occurrence AS o JOIN occurrence_grammar AS g "
                "ON g.occurrence_id = o.occurrence_id "
                "WHERE o.occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()
        finally:
            observer.close()
        self.assertEqual(
            stored, ("Created in outer transaction", "SIN-MARCA")
        )

    def test_outer_transaction_can_commit_successful_correction(self):
        first_id, _ = create_or_replace_occurrence_grammar(
            self.connection, 1, conjugated_form="SIN-MARCA"
        )
        self.connection.execute("BEGIN IMMEDIATE")
        second_id, created = create_or_replace_occurrence_grammar(
            self.connection, 1, conjugated_form="SÍ"
        )
        self.assertTrue(created)
        self.assertTrue(self.connection.in_transaction)
        self.connection.commit()

        observer = sqlite3.connect(self.database_path)
        try:
            rows = observer.execute(
                "SELECT occurrence_grammar_id, conjugated_form, is_current, "
                "supersedes_occurrence_grammar_id FROM occurrence_grammar "
                "WHERE occurrence_id = 1 ORDER BY occurrence_grammar_id"
            ).fetchall()
        finally:
            observer.close()
        self.assertEqual(
            rows,
            [
                (first_id, "SIN-MARCA", 0, None),
                (second_id, "SÍ", 1, first_id),
            ],
        )

    def test_outer_rollback_removes_helper_changes(self):
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute(
            "UPDATE occurrence SET original_gloss = 'Uncommitted change' "
            "WHERE occurrence_id = 1"
        )
        create_or_replace_occurrence_grammar(
            self.connection, 1, negation="SIN-NEG"
        )
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

        occurrence = self.connection.execute(
            "SELECT original_gloss FROM occurrence WHERE occurrence_id = 1"
        ).fetchone()
        grammar_count = self.connection.execute(
            "SELECT COUNT(*) FROM occurrence_grammar WHERE occurrence_id = 1"
        ).fetchone()[0]
        self.assertEqual(occurrence, ("Existing occurrence",))
        self.assertEqual(grammar_count, 0)

    def test_error_in_outer_transaction_remains_under_caller_control(self):
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute(
            "UPDATE occurrence SET original_gloss = 'Caller controls rollback' "
            "WHERE occurrence_id = 1"
        )
        with self.assertRaises(EmptyGrammarError):
            create_or_replace_occurrence_grammar(
                self.connection, 1, change_note="Not grammatical content"
            )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT original_gloss FROM occurrence WHERE occurrence_id = 1"
            ).fetchone(),
            ("Caller controls rollback",),
        )
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT original_gloss FROM occurrence WHERE occurrence_id = 1"
            ).fetchone(),
            ("Existing occurrence",),
        )

    def test_partial_failure_rolls_back_to_savepoint_and_outer_can_continue(self):
        first_id, _ = create_or_replace_occurrence_grammar(
            self.connection, 1, gender="SIN-MARCA"
        )
        self.connection.execute("""
            CREATE TRIGGER reject_outer_test_grammar
            BEFORE INSERT ON occurrence_grammar
            WHEN NEW.gender = 'FAIL'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic outer failure');
            END
        """)
        self.connection.commit()

        self.connection.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.IntegrityError):
            create_or_replace_occurrence_grammar(
                self.connection, 1, gender="FAIL"
            )

        self.assertTrue(self.connection.in_transaction)
        grammar_rows = self.connection.execute(
            "SELECT occurrence_grammar_id, gender, is_current "
            "FROM occurrence_grammar WHERE occurrence_id = 1"
        ).fetchall()
        self.assertEqual(grammar_rows, [(first_id, "SIN-MARCA", 1)])

        self.connection.execute(
            "UPDATE occurrence SET original_gloss = 'Committed after failure' "
            "WHERE occurrence_id = 1"
        )
        self.connection.commit()

        observer = sqlite3.connect(self.database_path)
        try:
            occurrence = observer.execute(
                "SELECT original_gloss FROM occurrence WHERE occurrence_id = 1"
            ).fetchone()
            grammar_rows = observer.execute(
                "SELECT occurrence_grammar_id, gender, is_current "
                "FROM occurrence_grammar WHERE occurrence_id = 1"
            ).fetchall()
        finally:
            observer.close()
        self.assertEqual(occurrence, ("Committed after failure",))
        self.assertEqual(grammar_rows, [(first_id, "SIN-MARCA", 1)])


if __name__ == "__main__":
    unittest.main()
