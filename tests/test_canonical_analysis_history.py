import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from assignments import create_or_replace_assignment
from alternative_relations import (
    DuplicateCurrentRelationError,
    SelfRelationError,
    create_current_relation,
    current_relation,
    list_current_relations,
    normalize_alternative_pair,
    replace_current_relation,
    retire_current_relation,
)
from occurrence_grammar import (
    InvalidGrammarUncertaintyError,
    create_or_replace_occurrence_grammar,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "009_canonical_analysis_history.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("migration009", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_post_008_database(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    database.crear_esquema(connection)
    for table in ("alternative_relation", "occurrence_grammar", "assignment"):
        connection.execute(f"DROP TABLE {table}")
    connection.executescript("""
        CREATE TABLE assignment (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            alternative_id INTEGER NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
            supersedes_assignment_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_assignment_id) REFERENCES assignment(assignment_id)
        );
        CREATE UNIQUE INDEX one_current_assignment_per_occurrence
            ON assignment(occurrence_id) WHERE is_current = 1;
        CREATE INDEX idx_assignment_alternative ON assignment(alternative_id);
        CREATE INDEX idx_assignment_occurrence ON assignment(occurrence_id);

        CREATE TABLE occurrence_grammar (
            occurrence_grammar_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            gender TEXT,
            plural TEXT,
            agentive TEXT,
            conjugated_form TEXT,
            negation TEXT,
            grammar_note TEXT,
            is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
            supersedes_occurrence_grammar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            change_note TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (supersedes_occurrence_grammar_id)
                REFERENCES occurrence_grammar(occurrence_grammar_id)
        );
        CREATE UNIQUE INDEX one_current_grammar_per_occurrence
            ON occurrence_grammar(occurrence_id) WHERE is_current = 1;
        CREATE INDEX idx_occurrence_grammar_occurrence
            ON occurrence_grammar(occurrence_id);

        CREATE TABLE alternative_relation (
            alternative_relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_a_id INTEGER NOT NULL,
            alternative_b_id INTEGER NOT NULL,
            phonological_parameter TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (alternative_a_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (alternative_b_id) REFERENCES alternative(alternative_id),
            CHECK (alternative_a_id <> alternative_b_id)
        );
        CREATE UNIQUE INDEX one_symmetric_alternative_relation
            ON alternative_relation(
                CASE WHEN alternative_a_id < alternative_b_id
                    THEN alternative_a_id ELSE alternative_b_id END,
                CASE WHEN alternative_a_id < alternative_b_id
                    THEN alternative_b_id ELSE alternative_a_id END
            );
    """)
    connection.execute("INSERT INTO source (source_name) VALUES ('Source')")
    connection.execute("INSERT INTO concept (preferred_label) VALUES ('CONCEPT')")
    connection.executemany(
        "INSERT INTO occurrence (source_id, original_gloss) VALUES (1, ?)",
        [("First",), ("Second",)],
    )
    connection.executemany(
        "INSERT INTO alternative (concept_id, working_label) VALUES (1, ?)",
        [("1a",), ("1b",)],
    )
    connection.execute(
        "INSERT INTO submission "
        "(submission_id, occurrence_id, submission_type, status, resolution) "
        "VALUES (7, 1, 'GRAMMAR', 'resolved', 'accepted')"
    )
    connection.execute("""
        INSERT INTO assignment (
            assignment_id, occurrence_id, alternative_id, is_current,
            created_at, created_by
        ) VALUES (11, 1, 1, 1, '2026-01-01', 'Legacy')
    """)
    connection.execute("""
        INSERT INTO occurrence_grammar (
            occurrence_grammar_id, occurrence_id, gender, plural, agentive,
            conjugated_form, negation, grammar_note, is_current, created_at,
            created_by, change_note
        ) VALUES (
            21, 1, 'G', 'P', 'A', 'C', 'N', 'Note', 1,
            '2026-01-02', 'Legacy', 'Initial'
        )
    """)
    connection.execute("""
        INSERT INTO alternative_relation (
            alternative_relation_id, alternative_a_id, alternative_b_id,
            phonological_parameter, created_at, created_by
        ) VALUES (31, 2, 1, 'MOV', '2026-01-03', 'Legacy')
    """)
    connection.commit()
    connection.close()


class Migration009Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "post008.db"
        self.backup = Path(self.temporary.name) / "backup.db"
        make_post_008_database(self.path)
        self.migration = load_migration()

    def tearDown(self):
        self.temporary.cleanup()

    def test_migrates_post_008_and_is_idempotent(self):
        self.assertTrue(self.migration.migrate(self.path, self.backup))
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.migration.migrate(self.path, self.backup))

    def test_legacy_ids_content_metadata_and_indexes_are_preserved(self):
        self.migration.migrate(self.path, self.backup)
        connection = sqlite3.connect(self.path)
        try:
            assignment = connection.execute(
                "SELECT * FROM assignment"
            ).fetchone()
            self.assertEqual(assignment, (
                11, 1, 1, 1, None, "2026-01-01", "Legacy", None
            ))
            grammar = connection.execute(
                "SELECT occurrence_grammar_id, occurrence_id, gender, "
                "gender_uncertain, plural, plural_uncertain, agentive, "
                "agentive_uncertain, conjugated_form, "
                "conjugated_form_uncertain, negation, negation_uncertain, "
                "grammar_note, is_current, "
                "supersedes_occurrence_grammar_id, created_at, created_by, "
                "change_note, created_from_submission_id "
                "FROM occurrence_grammar"
            ).fetchone()
            self.assertEqual(grammar, (
                21, 1, "G", 0, "P", 0, "A", 0, "C", 0, "N", 0,
                "Note", 1, None, "2026-01-02", "Legacy", "Initial", None,
            ))
            relation = connection.execute(
                "SELECT * FROM alternative_relation"
            ).fetchone()
            self.assertEqual(relation, (
                31, 1, 2, "MOV", 1, None, None,
                "2026-01-03", "Legacy",
            ))
            self.assertEqual(connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall(), [])
            self.assertIn(
                "one_current_assignment_per_occurrence",
                {r[1] for r in connection.execute("PRAGMA index_list(assignment)")},
            )
            self.assertIn(
                "one_current_grammar_per_occurrence",
                {r[1] for r in connection.execute(
                    "PRAGMA index_list(occurrence_grammar)"
                )},
            )
        finally:
            connection.close()

    def test_duplicate_normalized_relations_abort_before_backup(self):
        connection = sqlite3.connect(self.path)
        connection.execute("DROP INDEX one_symmetric_alternative_relation")
        connection.execute(
            "INSERT INTO alternative_relation "
            "(alternative_a_id, alternative_b_id, phonological_parameter) "
            "VALUES (1, 2, 'MOV')"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "duplicadas"):
            self.migration.migrate(self.path, self.backup)
        self.assertFalse(self.backup.exists())

    def test_fresh_and_migrated_relevant_structures_are_equivalent(self):
        self.migration.migrate(self.path, self.backup)
        fresh_path = Path(self.temporary.name) / "fresh.db"
        fresh = sqlite3.connect(fresh_path)
        database.crear_esquema(fresh)
        fresh.commit()
        migrated = sqlite3.connect(self.path)

        def structure(connection, table):
            index_rows = connection.execute(
                f"PRAGMA index_list({table})"
            ).fetchall()
            return (
                connection.execute(f"PRAGMA table_info({table})").fetchall(),
                connection.execute(
                    f"PRAGMA foreign_key_list({table})"
                ).fetchall(),
                sorted((row[1], row[2], row[4]) for row in index_rows),
                {
                    row[1]: connection.execute(
                        f"PRAGMA index_info({row[1]})"
                    ).fetchall()
                    for row in index_rows
                },
            )

        try:
            for table in (
                "assignment", "occurrence_grammar", "alternative_relation"
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        structure(migrated, table), structure(fresh, table)
                    )
        finally:
            migrated.close()
            fresh.close()


class CanonicalAnalysisHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "fresh.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)
        self.connection.execute("INSERT INTO source (source_name) VALUES ('Source')")
        self.connection.executemany(
            "INSERT INTO concept (preferred_label) VALUES (?)",
            [("ONE",), ("TWO",)],
        )
        self.connection.executemany(
            "INSERT INTO alternative (concept_id, working_label) VALUES (?, ?)",
            [(1, "1a"), (1, "1b"), (1, "1c"), (2, "1a")],
        )
        self.connection.executemany(
            "INSERT INTO occurrence (source_id, original_gloss) VALUES (1, ?)",
            [("First",), ("Second",)],
        )
        self.connection.execute(
            "INSERT INTO submission "
            "(submission_id, occurrence_id, submission_type, status, resolution) "
            "VALUES (7, 1, 'GRAMMAR', 'resolved', 'accepted')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_assignment_provenance_fk_and_current_uniqueness(self):
        self.connection.execute(
            "INSERT INTO assignment "
            "(occurrence_id, alternative_id, created_from_submission_id) "
            "VALUES (1, 1, 7)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO assignment (occurrence_id, alternative_id) "
                "VALUES (1, 2)"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO assignment "
                "(occurrence_id, alternative_id, created_from_submission_id) "
                "VALUES (2, 2, 999)"
            )
        self.connection.rollback()

    def test_assignment_helper_versions_and_rolls_back_atomically(self):
        first, created = create_or_replace_assignment(
            self.connection, 1, 1, created_from_submission_id=7
        )
        self.assertTrue(created)
        second, created = create_or_replace_assignment(
            self.connection, 1, 2
        )
        self.assertTrue(created)
        self.assertEqual(self.connection.execute(
            "SELECT assignment_id, alternative_id, is_current, "
            "supersedes_assignment_id, created_from_submission_id "
            "FROM assignment ORDER BY assignment_id"
        ).fetchall(), [
            (first, 1, 0, None, 7),
            (second, 2, 1, first, None),
        ])
        self.connection.execute("""
            CREATE TRIGGER reject_assignment
            BEFORE INSERT ON assignment
            WHEN NEW.alternative_id = 3
            BEGIN SELECT RAISE(ABORT, 'failure'); END
        """)
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            create_or_replace_assignment(self.connection, 1, 3)
        self.assertEqual(self.connection.execute(
            "SELECT assignment_id, is_current FROM assignment "
            "ORDER BY assignment_id"
        ).fetchall(), [(first, 0), (second, 1)])

    def test_grammar_uncertainty_versioning_and_provenance(self):
        first, _ = create_or_replace_occurrence_grammar(
            self.connection, 1, gender="VALUE"
        )
        second, created = create_or_replace_occurrence_grammar(
            self.connection,
            1,
            gender="VALUE",
            gender_uncertain=1,
            created_from_submission_id=7,
        )
        self.assertTrue(created)
        rows = self.connection.execute(
            "SELECT occurrence_grammar_id, gender, gender_uncertain, "
            "is_current, supersedes_occurrence_grammar_id, "
            "created_from_submission_id FROM occurrence_grammar "
            "ORDER BY occurrence_grammar_id"
        ).fetchall()
        self.assertEqual(rows, [
            (first, "VALUE", 0, 0, None, None),
            (second, "VALUE", 1, 1, first, 7),
        ])

    def test_grammar_null_uncertain_and_invalid_flag_fail(self):
        for kwargs in (
            {"gender_uncertain": 1},
            {"gender": "VALUE", "gender_uncertain": 2},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InvalidGrammarUncertaintyError):
                    create_or_replace_occurrence_grammar(
                        self.connection, 1, **kwargs
                    )
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM occurrence_grammar"
        ).fetchone()[0], 0)

    def test_grammar_failed_insert_restores_previous_current(self):
        first, _ = create_or_replace_occurrence_grammar(
            self.connection, 1, gender="STABLE"
        )
        self.connection.execute("""
            CREATE TRIGGER reject_new_grammar
            BEFORE INSERT ON occurrence_grammar
            WHEN NEW.gender = 'FAIL'
            BEGIN SELECT RAISE(ABORT, 'failure'); END
        """)
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            create_or_replace_occurrence_grammar(
                self.connection, 1, gender="FAIL"
            )
        self.assertEqual(self.connection.execute(
            "SELECT occurrence_grammar_id, gender, is_current "
            "FROM occurrence_grammar"
        ).fetchall(), [(first, "STABLE", 1)])

    def test_pair_normalization_self_duplicate_and_distinct_parameters(self):
        self.assertEqual(normalize_alternative_pair(2, 1), (1, 2))
        with self.assertRaises(SelfRelationError):
            normalize_alternative_pair(1, 1)
        first = create_current_relation(self.connection, 2, 1, "MOV")
        with self.assertRaises(DuplicateCurrentRelationError):
            create_current_relation(self.connection, 1, 2, "MOV")
        second = create_current_relation(self.connection, 1, 2, "LOC")
        self.assertNotEqual(first, second)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM alternative_relation WHERE is_current = 1"
        ).fetchone()[0], 2)

    def test_relation_replacement_and_retirement_preserve_history(self):
        first = create_current_relation(
            self.connection, 2, 1, "MOV", created_from_submission_id=7
        )
        second = replace_current_relation(
            self.connection, first, phonological_parameter="LOC"
        )
        rows = self.connection.execute(
            "SELECT alternative_relation_id, alternative_low_id, "
            "alternative_high_id, phonological_parameter, is_current, "
            "supersedes_alternative_relation_id, "
            "created_from_submission_id FROM alternative_relation "
            "ORDER BY alternative_relation_id"
        ).fetchall()
        self.assertEqual(rows, [
            (first, 1, 2, "MOV", 0, None, 7),
            (second, 1, 2, "LOC", 1, first, None),
        ])
        retire_current_relation(self.connection, second)
        self.assertIsNone(current_relation(self.connection, 1, 2, "LOC"))
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM alternative_relation"
        ).fetchone()[0], 2)

    def test_failed_relation_replacement_restores_previous_current(self):
        first = create_current_relation(self.connection, 1, 2, "MOV")
        self.connection.execute("""
            CREATE TRIGGER reject_new_relation
            BEFORE INSERT ON alternative_relation
            WHEN NEW.phonological_parameter = 'FAIL'
            BEGIN SELECT RAISE(ABORT, 'failure'); END
        """)
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            replace_current_relation(
                self.connection, first, phonological_parameter="FAIL"
            )
        self.assertEqual(self.connection.execute(
            "SELECT alternative_relation_id, is_current "
            "FROM alternative_relation"
        ).fetchall(), [(first, 1)])

    def test_relation_listing_does_not_infer_transitive_edge(self):
        create_current_relation(self.connection, 1, 2, "MOV")
        create_current_relation(self.connection, 2, 3, "MOV")
        self.assertIsNone(current_relation(self.connection, 1, 3, "MOV"))
        self.assertEqual(len(list_current_relations(
            self.connection, alternative_id=2
        )), 2)
        self.assertEqual(len(list_current_relations(
            self.connection, concept_id=1
        )), 2)


if __name__ == "__main__":
    unittest.main()
