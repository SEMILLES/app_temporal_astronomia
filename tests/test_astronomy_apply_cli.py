import hashlib
import io
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astronomy_apply import (
    ExistingDatabaseError,
    validate_existing_database,
)
from database import crear_esquema
from import_astronomia import main, run_dry_run
from tests.test_import_astronomia_dry_run import EXPECTATIONS, FIXTURES


ROOT = Path(__file__).resolve().parents[1]
PROTOTYPE = ROOT / "lesico_prototipo.db"


class AstronomyApplyCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run_dry_run(FIXTURES, EXPECTATIONS)
        if not result.ready_for_apply:
            raise AssertionError(result.errors)
        cls.plan = result.validated_plan

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = main(
                    [*map(str, arguments)], expectations=EXPECTATIONS
                )
        except SystemExit as exc:
            returncode = int(exc.code)
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def create_current_database(self, path):
        connection = sqlite3.connect(path)
        crear_esquema(connection)
        connection.commit()
        connection.close()

    def file_hash(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_apply_new_path_happy_path(self):
        database_path = self.directory / "new.db"
        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(database_path.is_file())
        self.assertIn("APPLY COMPLETED", result.stdout)
        self.assertIn(f"DATABASE: {database_path}", result.stdout)
        self.assertIn("sources=3", result.stdout)

        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            self.assertEqual(
                {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "source", "concept", "alternative",
                        "alternative_relation", "occurrence", "assignment",
                        "occurrence_grammar",
                    )
                },
                {
                    "source": 3,
                    "concept": 3,
                    "alternative": 4,
                    "alternative_relation": 1,
                    "occurrence": 4,
                    "assignment": 4,
                    "occurrence_grammar": 4,
                },
            )
        finally:
            connection.close()

    def test_dry_run_remains_read_only_and_needs_no_database(self):
        result = self.run_cli("--dry-run", FIXTURES)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY FOR APPLY: YES", result.stdout)
        self.assertEqual(list(self.directory.glob("*.db")), [])

    def test_invalid_inputs_do_not_create_destination(self):
        inputs = self.directory / "inputs"
        shutil.copytree(FIXTURES, inputs)
        (inputs / "resumen_occurrences_astronomia_v1.md").unlink()
        database_path = self.directory / "must-not-exist.db"

        result = self.run_cli(
            "--apply", inputs, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("input obligatorio ausente", result.stdout)
        self.assertFalse(database_path.exists())

    def test_apply_and_dry_run_are_mutually_exclusive(self):
        database_path = self.directory / "exclusive.db"
        result = self.run_cli(
            "--apply", "--dry-run", FIXTURES, "--database", database_path
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)
        self.assertFalse(database_path.exists())

    def test_apply_requires_explicit_database(self):
        result = self.run_cli("--apply", FIXTURES)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply exige --database", result.stderr)

    def test_new_database_is_removed_after_persistence_failure(self):
        database_path = self.directory / "failed-new.db"
        with patch(
            "astronomy_apply.persist_validated_plan",
            side_effect=sqlite3.IntegrityError("synthetic persistence failure"),
        ):
            result = self.run_cli(
                "--apply", FIXTURES, "--database", database_path
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic persistence failure", result.stderr)
        self.assertNotIn("APPLY COMPLETED", result.stdout)
        self.assertFalse(database_path.exists())

    def test_existing_non_sqlite_is_rejected_without_modification(self):
        database_path = self.directory / "not-sqlite.db"
        database_path.write_bytes(b"contenido ficticio que no es sqlite")
        before = self.file_hash(database_path)

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no es una SQLite válida", result.stderr)
        self.assertEqual(self.file_hash(database_path), before)

    def test_existing_sqlite_without_schema_is_rejected_unchanged(self):
        database_path = self.directory / "without-schema.db"
        database_path.touch()
        before = self.file_hash(database_path)
        with self.assertRaisesRegex(
            ExistingDatabaseError, "faltan tablas"
        ):
            validate_existing_database(database_path)
        self.assertEqual(self.file_hash(database_path), before)

    def test_existing_incomplete_schema_is_rejected_unchanged(self):
        database_path = self.directory / "incomplete.db"
        connection = sqlite3.connect(database_path)
        connection.execute(
            "CREATE TABLE source (source_id INTEGER PRIMARY KEY)"
        )
        connection.commit()
        connection.close()
        before = self.file_hash(database_path)

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("faltan tablas", result.stderr)
        self.assertEqual(self.file_hash(database_path), before)

    def test_existing_incompatible_schema_is_rejected_unchanged(self):
        database_path = self.directory / "incompatible.db"
        self.create_current_database(database_path)
        connection = sqlite3.connect(database_path)
        connection.execute("DROP TABLE concept")
        connection.execute(
            "CREATE TABLE concept (concept_id TEXT PRIMARY KEY)"
        )
        connection.commit()
        connection.close()
        before = self.file_hash(database_path)

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema incompatible", result.stderr)
        self.assertEqual(self.file_hash(database_path), before)

    def test_existing_schema_without_current_index_is_rejected_unchanged(self):
        database_path = self.directory / "missing-current-index.db"
        self.create_current_database(database_path)
        connection = sqlite3.connect(database_path)
        connection.execute("DROP INDEX one_current_assignment_per_occurrence")
        connection.commit()
        connection.close()
        before = self.file_hash(database_path)

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema incompatible", result.stderr)
        self.assertEqual(self.file_hash(database_path), before)

    def test_existing_current_empty_schema_is_allowed(self):
        database_path = self.directory / "current-empty.db"
        self.create_current_database(database_path)

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM occurrence").fetchone()[0],
                len(self.plan.occurrences),
            )
        finally:
            connection.close()

    def test_existing_current_nonempty_schema_is_rejected_with_data_intact(self):
        database_path = self.directory / "current-nonempty.db"
        self.create_current_database(database_path)
        connection = sqlite3.connect(database_path)
        connection.execute(
            "INSERT INTO source (source_name) VALUES ('PREEXISTENTE')"
        )
        connection.commit()
        connection.close()

        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base vacía", result.stderr)
        self.assertNotIn("READY FOR APPLY: YES", result.stdout)
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT source_name FROM source"
                ).fetchall(),
                [("PREEXISTENTE",)],
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM occurrence").fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_persisted_content_matches_validated_plan(self):
        database_path = self.directory / "logical-content.db"
        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        connection = sqlite3.connect(database_path)
        try:
            occurrences = connection.execute(
                """
                SELECT o.legacy_occurrence_id, o.original_gloss, s.source_name,
                       a.working_label, c.preferred_label, g.grammar_note
                FROM occurrence AS o
                JOIN source AS s ON s.source_id = o.source_id
                JOIN assignment AS x
                  ON x.occurrence_id = o.occurrence_id AND x.is_current = 1
                JOIN alternative AS a ON a.alternative_id = x.alternative_id
                JOIN concept AS c ON c.concept_id = a.concept_id
                JOIN occurrence_grammar AS g
                  ON g.occurrence_id = o.occurrence_id AND g.is_current = 1
                ORDER BY o.original_gloss
                """
            ).fetchall()
        finally:
            connection.close()

        expected = sorted([
            (
                occurrence.legacy_occurrence_id,
                occurrence.original_gloss,
                occurrence.source_name,
                next(
                    alternative.working_label
                    for alternative in self.plan.alternatives
                    if alternative.canonical_code == occurrence.alternative_code
                ),
                occurrence.concept_label,
                next(
                    grammar.grammar_note
                    for grammar in self.plan.grammar
                    if grammar.occurrence_key == occurrence.key
                ),
            )
            for occurrence in self.plan.occurrences
        ], key=lambda item: item[1])
        self.assertEqual(occurrences, expected)

    def test_apply_does_not_modify_prototype_or_create_real_candidate(self):
        before = self.file_hash(PROTOTYPE)
        database_path = self.directory / "isolated.db"
        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.file_hash(PROTOTYPE), before)
        self.assertFalse((ROOT / "lesico_astronomia_candidate.db").exists())

    def test_success_creates_no_history_or_workflow(self):
        database_path = self.directory / "no-history.db"
        result = self.run_cli(
            "--apply", FIXTURES, "--database", database_path
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        connection = sqlite3.connect(database_path)
        try:
            for table in (
                "submission", "source_revision", "source_systematization",
                "occurrence_revision",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0],
                        0,
                    )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
