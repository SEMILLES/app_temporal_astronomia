import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "008_typed_submission_workflow.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("migration008", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def insert_foundation(connection):
    connection.execute("INSERT INTO source (source_name) VALUES ('Source')")
    connection.execute("INSERT INTO concept (preferred_label) VALUES ('CONCEPT')")
    connection.executemany(
        "INSERT INTO occurrence (source_id, original_gloss) VALUES (1, ?)",
        [("First",), ("Second",), ("Third",)],
    )
    connection.executemany(
        "INSERT INTO alternative (concept_id, working_label) VALUES (1, ?)",
        [("1a",), ("1b",)],
    )
    connection.commit()


def create_legacy_database(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    database.crear_esquema(connection)
    for table in (
        "grammar_submission",
        "alternative_submission_relation",
        "alternative_submission",
        "occurrence_concept_reference",
        "occurrence_draft",
        "concept_proposal",
        "submission",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.executescript("""
        CREATE TABLE submission (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL UNIQUE,
            proposed_concept_id INTEGER,
            proposed_concept_label TEXT,
            proposed_concept_note TEXT,
            proposed_alternative_id INTEGER,
            proposed_alternative_label TEXT,
            proposed_concept_status TEXT,
            concept_uncertainty_note TEXT,
            proposed_relation_answer TEXT,
            proposed_related_alternative_id INTEGER,
            proposed_related_submission_id INTEGER,
            proposed_phonological_parameter TEXT,
            alternative_uncertainty_note TEXT,
            proposal_type TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            submitted_by TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            review_comment TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (proposed_concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (proposed_alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (proposed_related_alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (proposed_related_submission_id) REFERENCES submission(submission_id)
        );
        CREATE INDEX idx_submission_status ON submission(status);
        CREATE INDEX idx_submission_occurrence ON submission(occurrence_id);
        CREATE INDEX idx_submission_related_pending_submission
            ON submission(proposed_related_submission_id);
    """)
    insert_foundation(connection)
    connection.execute(
        "INSERT INTO assignment (occurrence_id, alternative_id) VALUES (2, 2)"
    )
    rows = [
        (
            10, 1, 1, None, "Existing note", 1, None, "selected",
            None, "no", None, None, None, "Alternative note",
            "existing_alternative", "pending", "2026-01-01", "Analyst",
            None, None, None,
        ),
        (
            20, 2, None, "NEW CONCEPT", "Concept note", None,
            "Legacy proposed label", "new", "Concept uncertainty", "yes",
            1, None, "MOV", "Alternative uncertainty", "new_alternative",
            "accepted", "2026-01-02", "Analyst", "2026-01-03",
            "Reviewer", "Accepted legacy",
        ),
        (
            30, 3, None, None, None, None, None, "not_sure", "Unknown",
            "yes", None, 20, "LOC", None, "not_sure", "rejected",
            "2026-01-04", None, "2026-01-05", None, "Rejected legacy",
        ),
    ]
    connection.executemany("""
        INSERT INTO submission (
            submission_id, occurrence_id, proposed_concept_id,
            proposed_concept_label, proposed_concept_note,
            proposed_alternative_id, proposed_alternative_label,
            proposed_concept_status, concept_uncertainty_note,
            proposed_relation_answer, proposed_related_alternative_id,
            proposed_related_submission_id, proposed_phonological_parameter,
            alternative_uncertainty_note, proposal_type, status,
            submitted_at, submitted_by, reviewed_at, reviewed_by,
            review_comment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    connection.commit()
    connection.close()


class TypedWorkflowSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "schema.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        database.crear_esquema(self.connection)
        insert_foundation(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def submission(self, occurrence_id, kind, status="pending", resolution=None):
        return self.connection.execute(
            "INSERT INTO submission "
            "(occurrence_id, submission_type, status, resolution) "
            "VALUES (?, ?, ?, ?)",
            (occurrence_id, kind, status, resolution),
        ).lastrowid

    def test_pending_uniqueness_is_per_occurrence_and_type(self):
        self.submission(1, "GRAMMAR")
        self.submission(1, "ALTERNATIVE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.submission(1, "GRAMMAR")
        self.connection.rollback()

    def test_multiple_resolved_histories_are_allowed_for_both_types(self):
        for kind in ("GRAMMAR", "ALTERNATIVE"):
            self.submission(1, kind, "resolved", "accepted")
            self.submission(1, kind, "resolved", "rejected")
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM submission").fetchone()[0],
            4,
        )

    def test_status_resolution_coherence(self):
        invalid = [
            ("pending", "accepted"),
            ("pending", "rejected"),
            ("resolved", None),
        ]
        for status, resolution in invalid:
            with self.subTest(status=status, resolution=resolution):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.submission(1, "GRAMMAR", status, resolution)
                self.connection.rollback()

    def test_concept_proposal_status_requires_coherent_resolution(self):
        valid = [
            ("PENDING", "pending", None),
            ("RESOLVED", "resolved", 1),
            ("REJECTED", "rejected", None),
        ]
        for label, status, concept_id in valid:
            self.connection.execute(
                "INSERT INTO concept_proposal "
                "(proposed_label, status, resolved_concept_id) VALUES (?, ?, ?)",
                (label, status, concept_id),
            )
        for status, concept_id in (
            ("pending", 1), ("resolved", None), ("rejected", 1)
        ):
            with self.subTest(status=status, concept_id=concept_id):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(
                        "INSERT INTO concept_proposal "
                        "(proposed_label, status, resolved_concept_id) "
                        "VALUES ('INVALID', ?, ?)",
                        (status, concept_id),
                    )

    def test_new_alternative_submission_requires_context_and_existing_target(self):
        valid = self.submission(1, "ALTERNATIVE", "resolved", "accepted")
        self.connection.execute(
            "INSERT INTO alternative_submission "
            "(submission_id, proposal_kind, reference_concept_id, "
            "proposed_existing_alternative_id, resolved_alternative_id) "
            "VALUES (?, 'EXISTING', 1, 1, 1)",
            (valid,),
        )
        missing_context = self.submission(2, "ALTERNATIVE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO alternative_submission "
                "(submission_id, proposal_kind) VALUES (?, 'NEW')",
                (missing_context,),
            )
        missing_existing = self.submission(3, "ALTERNATIVE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO alternative_submission "
                "(submission_id, proposal_kind, reference_concept_id) "
                "VALUES (?, 'EXISTING', 1)",
                (missing_existing,),
            )

    def test_occurrence_concept_reference_xor_and_one_current(self):
        proposal = self.connection.execute(
            "INSERT INTO concept_proposal (proposed_label, status) "
            "VALUES ('PROPOSED', 'pending')"
        ).lastrowid
        self.connection.execute(
            "INSERT INTO occurrence_concept_reference "
            "(occurrence_id, concept_id) VALUES (1, 1)"
        )
        self.connection.execute(
            "INSERT INTO occurrence_concept_reference "
            "(occurrence_id, concept_proposal_id) VALUES (2, ?)",
            (proposal,),
        )
        for values in ((3, 1, proposal), (3, None, None)):
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(
                    "INSERT INTO occurrence_concept_reference "
                    "(occurrence_id, concept_id, concept_proposal_id) "
                    "VALUES (?, ?, ?)", values,
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO occurrence_concept_reference "
                "(occurrence_id, concept_id) VALUES (1, 1)"
            )
        self.connection.rollback()

    def test_grammar_submission_is_partial_and_has_no_linguistic_defaults(self):
        submission_id = self.submission(1, "GRAMMAR")
        self.connection.execute(
            "INSERT INTO grammar_submission (submission_id, gender) "
            "VALUES (?, 'VALUE')", (submission_id,),
        )
        row = self.connection.execute(
            "SELECT gender, plural, agentive, conjugated_form, negation, "
            "gender_uncertain, plural_uncertain FROM grammar_submission"
        ).fetchone()
        self.assertEqual(row, ("VALUE", None, None, None, None, 0, 0))

    def test_null_grammar_value_cannot_be_uncertain(self):
        submission_id = self.submission(1, "GRAMMAR")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO grammar_submission "
                "(submission_id, gender_uncertain) VALUES (?, 1)",
                (submission_id,),
            )
        self.connection.rollback()

    def test_relation_targets_xor_and_partial_uniqueness(self):
        owner = self.submission(1, "ALTERNATIVE")
        target = self.submission(2, "ALTERNATIVE")
        self.connection.execute(
            "INSERT INTO alternative_submission "
            "(submission_id, proposal_kind, reference_concept_id) "
            "VALUES (?, 'NEW', 1)", (owner,),
        )
        self.connection.execute(
            "INSERT INTO alternative_submission "
            "(submission_id, proposal_kind, reference_concept_id) "
            "VALUES (?, 'NEW', 1)", (target,),
        )
        self.connection.execute(
            "INSERT INTO alternative_submission_relation "
            "(submission_id, target_alternative_id, phonological_parameter) "
            "VALUES (?, 1, 'MOV')", (owner,),
        )
        self.connection.execute(
            "INSERT INTO alternative_submission_relation "
            "(submission_id, target_submission_id, phonological_parameter) "
            "VALUES (?, ?, 'MOV')", (owner, target),
        )
        for alternative, submission in ((1, target), (None, None)):
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(
                    "INSERT INTO alternative_submission_relation "
                    "(submission_id, target_alternative_id, "
                    "target_submission_id, phonological_parameter) "
                    "VALUES (?, ?, ?, 'LOC')",
                    (owner, alternative, submission),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO alternative_submission_relation "
                "(submission_id, target_alternative_id, phonological_parameter) "
                "VALUES (?, 1, 'MOV')", (owner,),
            )
        self.connection.execute(
            "INSERT INTO alternative_submission_relation "
            "(submission_id, target_alternative_id, phonological_parameter) "
            "VALUES (?, 1, 'LOC')", (owner,),
        )


class Migration008Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "legacy.db"
        self.backup = self.directory / "backup.db"
        create_legacy_database(self.path)
        self.migration = load_migration()

    def tearDown(self):
        self.temporary.cleanup()

    def test_applies_and_second_run_is_idempotent(self):
        self.assertTrue(self.migration.migrate(self.path, self.backup))
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.migration.migrate(self.path, self.backup))

    def test_legacy_rows_ids_status_kind_context_notes_and_relations_survive(self):
        self.migration.migrate(self.path, self.backup)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            submissions = connection.execute(
                "SELECT * FROM submission ORDER BY submission_id"
            ).fetchall()
            self.assertEqual([row["submission_id"] for row in submissions], [10, 20, 30])
            self.assertEqual(
                [(row["status"], row["resolution"]) for row in submissions],
                [("pending", None), ("resolved", "accepted"),
                 ("resolved", "rejected")],
            )
            alternatives = connection.execute(
                "SELECT * FROM alternative_submission ORDER BY submission_id"
            ).fetchall()
            self.assertEqual(
                [row["proposal_kind"] for row in alternatives],
                ["EXISTING", "NEW", "UNSURE"],
            )
            self.assertEqual(alternatives[0]["reference_concept_id"], 1)
            self.assertEqual(alternatives[1]["resolved_alternative_id"], 2)
            self.assertEqual(
                alternatives[1]["legacy_proposed_alternative_label"],
                "Legacy proposed label",
            )
            self.assertEqual(
                alternatives[1]["legacy_concept_uncertainty_note"],
                "Concept uncertainty",
            )
            proposal = connection.execute(
                "SELECT * FROM concept_proposal"
            ).fetchone()
            self.assertEqual(proposal["proposed_label"], "NEW CONCEPT")
            self.assertEqual(proposal["status"], "resolved")
            self.assertEqual(proposal["resolved_concept_id"], 1)
            relations = connection.execute(
                "SELECT submission_id, target_alternative_id, "
                "target_submission_id, phonological_parameter "
                "FROM alternative_submission_relation ORDER BY submission_id"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in relations],
                [(20, 1, None, "MOV"), (30, None, 20, "LOC")],
            )
            self.assertEqual(connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
