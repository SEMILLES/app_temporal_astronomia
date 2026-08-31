import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astronomy_persistence import (
    NonEmptyDatabaseError,
    persist_validated_plan,
)
from database import crear_esquema
from import_astronomia import run_dry_run
from tests.test_import_astronomia_dry_run import EXPECTATIONS, FIXTURES, NOTE


PLAN_TABLES = (
    "source",
    "concept",
    "alternative",
    "alternative_relation",
    "occurrence",
    "assignment",
    "occurrence_grammar",
)


class AstronomyPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run_dry_run(FIXTURES, EXPECTATIONS)
        if not result.ready_for_apply:
            raise AssertionError(result.errors)
        cls.plan = result.validated_plan

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "astronomy.db"
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        crear_esquema(self.connection)
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def counts(self, tables=PLAN_TABLES):
        return {
            table: self.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }

    def assert_plan_tables_empty(self):
        self.assertEqual(self.counts(), {table: 0 for table in PLAN_TABLES})

    def test_happy_path_persists_plan_and_valid_foreign_keys(self):
        persist_validated_plan(self.connection, self.plan)

        self.assertEqual(
            self.counts(),
            {
                "source": len(self.plan.sources),
                "concept": len(self.plan.concepts),
                "alternative": len(self.plan.alternatives),
                "alternative_relation": len(self.plan.relations),
                "occurrence": len(self.plan.occurrences),
                "assignment": len(self.plan.assignments),
                "occurrence_grammar": len(self.plan.grammar),
            },
        )
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )
        self.assertFalse(self.connection.in_transaction)

    def test_only_accepts_validated_import_plan(self):
        invalid_result = run_dry_run(Path(self.temporary.name) / "missing")
        for invalid in (invalid_result, invalid_result.validated_plan, object()):
            with self.subTest(value=type(invalid).__name__):
                with self.assertRaisesRegex(TypeError, "ValidatedImportPlan"):
                    persist_validated_plan(self.connection, invalid)
                self.assert_plan_tables_empty()

    def test_early_failure_rolls_back_everything(self):
        from astronomy_persistence import _insert_sources

        def fail_after_sources(connection, plan):
            _insert_sources(connection, plan)
            raise RuntimeError("synthetic early failure")

        with patch(
            "astronomy_persistence._insert_sources",
            side_effect=fail_after_sources,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic early failure"):
                persist_validated_plan(self.connection, self.plan)

        self.assert_plan_tables_empty()
        self.assertFalse(self.connection.in_transaction)

    def test_intermediate_failure_rolls_back_everything(self):
        with patch(
            "astronomy_persistence._insert_occurrences",
            side_effect=RuntimeError("synthetic occurrence failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "synthetic occurrence failure"
            ):
                persist_validated_plan(self.connection, self.plan)

        self.assert_plan_tables_empty()
        self.assertFalse(self.connection.in_transaction)

    def test_grammar_failure_rolls_back_entire_import(self):
        with patch(
            "astronomy_persistence.create_or_replace_occurrence_grammar",
            side_effect=sqlite3.IntegrityError("synthetic grammar failure"),
        ):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "synthetic grammar failure"
            ):
                persist_validated_plan(self.connection, self.plan)

        self.assert_plan_tables_empty()
        self.assertFalse(self.connection.in_transaction)

    def test_initial_load_rejects_nonempty_database_inside_transaction(self):
        from astronomy_persistence import _require_empty_database

        original_empty_check = _require_empty_database
        cases = ("source", "source_revision")
        for table in cases:
            with self.subTest(table=table):
                self.connection.close()
                self.database_path.unlink()
                self.connection = sqlite3.connect(self.database_path)
                self.connection.row_factory = sqlite3.Row
                crear_esquema(self.connection)
                source_id = self.connection.execute(
                    "INSERT INTO source (source_name) VALUES ('PREEXISTENTE')"
                ).lastrowid
                if table == "source_revision":
                    self.connection.execute(
                        """
                        INSERT INTO source_revision (source_id, source_name)
                        VALUES (?, 'PREEXISTENTE')
                        """,
                        (source_id,),
                    )
                self.connection.commit()

                transaction_states = []
                original_execute = self.connection.execute

                def observe_empty_check(connection):
                    transaction_states.append(connection.in_transaction)
                    return original_empty_check(connection)

                with patch(
                    "astronomy_persistence._require_empty_database",
                    side_effect=observe_empty_check,
                ):
                    with self.assertRaises(NonEmptyDatabaseError) as raised:
                        persist_validated_plan(self.connection, self.plan)

                self.assertEqual(transaction_states, [True])
                self.assertIn(table, str(raised.exception))
                self.assertEqual(
                    [
                        tuple(row)
                        for row in original_execute(
                            "SELECT source_name FROM source"
                        ).fetchall()
                    ],
                    [("PREEXISTENTE",)],
                )
                expected_history = 1 if table == "source_revision" else 0
                self.assertEqual(
                    original_execute(
                        "SELECT COUNT(*) FROM source_revision"
                    ).fetchone()[0],
                    expected_history,
                )
                self.assertEqual(
                    self.counts(
                        tuple(item for item in PLAN_TABLES if item != "source")
                    ),
                    {
                        item: 0
                        for item in PLAN_TABLES
                        if item != "source"
                    },
                )

    def test_current_assignment_and_grammar_are_unique(self):
        persist_validated_plan(self.connection, self.plan)
        for table in ("assignment", "occurrence_grammar"):
            duplicates = self.connection.execute(
                f"""
                SELECT occurrence_id
                FROM {table}
                WHERE is_current = 1
                GROUP BY occurrence_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            self.assertEqual(duplicates, [])

    def test_does_not_create_history_or_workflow(self):
        persist_validated_plan(self.connection, self.plan)
        for table in (
            "source_revision",
            "source_systematization",
            "occurrence_revision",
            "submission",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    self.connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0],
                    0,
                )

    def test_preserves_literal_and_null_occurrence_values(self):
        persist_validated_plan(self.connection, self.plan)
        literal = self.connection.execute(
            """
            SELECT legacy_occurrence_id, original_gloss, hyperlink,
                   legacy_source_detail_1, legacy_source_detail_2,
                   source_locator, occurrence_year
            FROM occurrence
            WHERE legacy_occurrence_id = 'PRUEBA-002'
            """
        ).fetchone()
        self.assertEqual(
            tuple(literal),
            (
                "PRUEBA-002",
                "GLOSA-FICTICIA-DELTA",
                None,
                "detalle literal ficticio / A",
                "página simulada 42",
                "localizador explícito ficticio",
                None,
            ),
        )
        nulls = self.connection.execute(
            """
            SELECT legacy_occurrence_id, hyperlink, legacy_source_detail_1,
                   legacy_source_detail_2, source_locator, occurrence_year
            FROM occurrence
            WHERE original_gloss = 'GLOSA-FICTICIA-GAMMA'
            """
        ).fetchone()
        self.assertEqual(tuple(nulls), (None, None, None, None, None, None))

    def test_preserves_source_year_alternative_labels_and_grammar_note(self):
        persist_validated_plan(self.connection, self.plan)
        source = self.connection.execute(
            """
            SELECT start_year, end_year
            FROM source WHERE source_name = 'CENTRO-DE-PRUEBA'
            """
        ).fetchone()
        occurrence = self.connection.execute(
            """
            SELECT legacy_occurrence_id, original_gloss, hyperlink,
                   occurrence_year
            FROM occurrence
            WHERE legacy_occurrence_id = 'PRUEBA-001'
            """
        ).fetchone()
        alternative = self.connection.execute(
            """
            SELECT a.original_code, a.working_label
            FROM alternative AS a
            JOIN concept AS c ON c.concept_id = a.concept_id
            WHERE c.preferred_label = 'FENOMENO-ALFA'
              AND a.working_label = '1b'
            """
        ).fetchone()
        grammar_note = self.connection.execute(
            """
            SELECT g.grammar_note
            FROM occurrence_grammar AS g
            JOIN occurrence AS o ON o.occurrence_id = g.occurrence_id
            WHERE o.legacy_occurrence_id = 'PRUEBA-001'
            """
        ).fetchone()[0]
        self.assertEqual(tuple(source), (1999, 2001))
        self.assertEqual(
            tuple(occurrence),
            (
                "PRUEBA-001",
                "GLOSA-FICTICIA-ALFA",
                "https://example.invalid/ficticio",
                None,
            ),
        )
        self.assertEqual(
            tuple(alternative), ("LEGACY-FICTICIO-7z", "1b")
        )
        self.assertEqual(grammar_note, NOTE)

    def test_persists_only_explicit_relations(self):
        persist_validated_plan(self.connection, self.plan)
        rows = self.connection.execute(
            """
            SELECT ca.preferred_label || '-' || aa.working_label,
                   cb.preferred_label || '-' || ab.working_label,
                   r.phonological_parameter
            FROM alternative_relation AS r
            JOIN alternative AS aa ON aa.alternative_id = r.alternative_low_id
            JOIN concept AS ca ON ca.concept_id = aa.concept_id
            JOIN alternative AS ab ON ab.alternative_id = r.alternative_high_id
            JOIN concept AS cb ON cb.concept_id = ab.concept_id
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("FENOMENO-ALFA-1a", "FENOMENO-ALFA-1b", "CM_1")],
        )


if __name__ == "__main__":
    unittest.main()
