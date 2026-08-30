import csv
import hashlib
import io
import shutil
import sqlite3
import subprocess
import sys
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
from import_astronomia import (
    EXCLUDED_HEADERS,
    OBSERVATION_HEADERS,
    OCCURRENCE_HEADERS,
    SOURCE_HEADERS,
    main,
    run_dry_run,
)
from tests.test_import_astronomia_dry_run import EXPECTATIONS, FIXTURES


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "import_astronomia.py"
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

    def write_tsv(self, path, headers, rows):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=headers, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)

    def create_default_contract_synthetic_inputs(self):
        inputs = self.directory / "subprocess-inputs"
        inputs.mkdir()

        sources = []
        institutional_names = [
            "DBLSC",
            "Planetario de Medellín",
            "UNIVERSIDAD NACIONAL",
            *[f"FUENTE-INSTITUCIONAL-{index:02d}" for index in range(4, 41)],
        ]
        personal_names = [
            f"REPOSITORIO-PERSONAL-FICTICIO-{index}" for index in range(1, 5)
        ]
        for index, name in enumerate(
            [*institutional_names, *personal_names], 1
        ):
            row = {header: "" for header in SOURCE_HEADERS}
            row.update({
                "source_reconstruction_key": f"source:synthetic-{index:02d}",
                "source_name": name,
                "legacy_source_code": (
                    "0MISC" if name in personal_names else f"SYN-{index:02d}"
                ),
                "source_scope": (
                    "PERSONAL" if name in personal_names else "INSTITUTIONAL"
                ),
                "create_source_systematization": "0",
            })
            sources.append(row)
        self.write_tsv(
            inputs / "reconstruccion_sources_astronomia_v1.tsv",
            SOURCE_HEADERS,
            sources,
        )

        concepts = [
            "LUZ",
            "CONTAMINACIÓN-LUMÍNICA",
            *[f"CONCEPTO-SINTETICO-{index:03d}" for index in range(3, 151)],
        ]
        alternatives = [(concept, f"{concept}-1a") for concept in concepts]
        alternatives.append(("LUZ", "LUZ-1b"))
        alternatives.extend(
            (concept, f"{concept}-1b") for concept in concepts[2:79]
        )
        alternative_lines = [
            "# Corpus sintético para regresión subprocess",
            "",
            "## Alternatives",
            "",
            "| Concept | Canonical code | Legacy code | C4 | C5 | C6 | C7 | C8 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for index, (concept, code) in enumerate(alternatives):
            legacy = f"LEGACY-SINTETICO-{index + 1:03d}" if index < 27 else "—"
            alternative_lines.append(
                f"| {concept} | {code} | {legacy} | x | x | x | x | x |"
            )
        alternative_lines.extend([
            "",
            "## Relaciones fonológicas confirmadas",
            "",
            "| Alternative A | Alternative B | Parameter | Note |",
            "|---|---|---|---|",
            "| LUZ-1a | LUZ-1b | CM_1 | sintética |",
        ])
        for concept in concepts[2:20]:
            alternative_lines.append(
                f"| {concept}-1a | {concept}-1b | CM_1 | sintética |"
            )
        (inputs / "reconstruccion_alternatives_astronomia_v2.md").write_text(
            "\n".join(alternative_lines) + "\n", encoding="utf-8"
        )

        occurrences = []

        def occurrence_row(
            legacy_id, gloss, concept, alternative, source,
            conjugated="SIN-MARCA", negation="SIN-NEG",
        ):
            row = {header: "" for header in OCCURRENCE_HEADERS}
            row.update({
                "Área de conocimiento": "AREA-SINTETICA",
                "Legacy occurrence": legacy_id,
                "Glosa original": gloss,
                "Concepto canónico": concept,
                "Alternative canónica": alternative,
                "Assignment": "ASIGNADA",
                "Fuente": source,
                "Alcance": "INSTITUTIONAL",
                "Género": "SIN-MARCA",
                "Plural": "SIN-MARCA",
                "Negación": negation,
                "Forma conjugada": conjugated,
                "Agentivo": "SIN-MARCA",
            })
            return row

        occurrences.extend([
            occurrence_row(
                "2183-LUZ", "LUZ", "LUZ", "LUZ-1a", "DBLSC",
                conjugated="SÍ",
            ),
            occurrence_row(
                "11162-LUZ", "LUZ", "LUZ", "LUZ-1b",
                "Planetario de Medellín", conjugated="SÍ",
            ),
            occurrence_row(
                "", "CONTAMINACIÓN LUMÍNICA", "CONTAMINACIÓN-LUMÍNICA",
                "CONTAMINACIÓN-LUMÍNICA-1a", "UNIVERSIDAD NACIONAL",
                negation="CON-NEG",
            ),
        ])
        for index in range(1, 233):
            concept = concepts[2 + (index % 148)]
            occurrences.append(
                occurrence_row(
                    f"LEGACY-SINTETICO-{index:03d}",
                    f"GLOSA-LEGACY-SINTETICA-{index:03d}",
                    concept,
                    f"{concept}-1a",
                    institutional_names[index % len(institutional_names)],
                )
            )
        for index in range(1, 19):
            concept = concepts[2 + ((index + 50) % 148)]
            occurrences.append(
                occurrence_row(
                    "",
                    f"GLOSA-NUEVA-SINTETICA-{index:03d}",
                    concept,
                    f"{concept}-1a",
                    "UNIVERSIDAD NACIONAL",
                )
            )
        self.write_tsv(
            inputs / "reconstruccion_occurrences_astronomia_v1.tsv",
            OCCURRENCE_HEADERS,
            occurrences,
        )

        observation_rows = []
        for index, occurrence in enumerate(occurrences[:115]):
            legacy_id = occurrence["Legacy occurrence"]
            key = (
                f"legacy:{legacy_id}"
                if legacy_id
                else "unal:"
                + occurrence["Fuente"]
                + "|"
                + occurrence["Alternative canónica"]
                + "|"
                + occurrence["Glosa original"]
            )
            row = {header: "" for header in OBSERVATION_HEADERS}
            row.update({
                "occurrence_reconstruction_key": key,
                "legacy_occurrence_id": legacy_id,
                "alternative_canonica": occurrence["Alternative canónica"],
                "fuente": occurrence["Fuente"],
                "observacion_original": f"Observación sintética {index + 1}",
                "categories": (
                    "OCCURRENCE_PROVENANCE" if index < 21 else "OTHER_REVIEW"
                ),
                "provenance_note_candidate": (
                    f"Candidato sintético {index + 1}" if index < 21 else ""
                ),
                "provenance_decision": (
                    "PROPOSED" if index < 21 else "NOT_APPLICABLE"
                ),
            })
            observation_rows.append(row)
        self.write_tsv(
            inputs / "revision_observaciones_astronomia_v1.tsv",
            OBSERVATION_HEADERS,
            observation_rows,
        )
        self.write_tsv(
            inputs / "occurrences_excluidas_astronomia_v1.tsv",
            EXCLUDED_HEADERS,
            [
                {
                    "Legacy occurrence": f"EXCLUIDA-SINTETICA-{index}",
                    "Razón de exclusión": "Razón enteramente sintética",
                }
                for index in range(1, 4)
            ],
        )
        (inputs / "resumen_occurrences_astronomia_v1.md").write_text(
            "# Resumen sintético subprocess\n", encoding="utf-8"
        )
        return inputs

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

    def test_script_entrypoint_uses_canonical_validated_plan_identity(self):
        inputs = self.create_default_contract_synthetic_inputs()
        database_path = self.directory / "subprocess.db"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
                str(inputs),
                "--database",
                str(database_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(
            "plan debe ser una instancia de ValidatedImportPlan",
            result.stderr,
        )
        self.assertTrue(database_path.is_file())
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "source",
                        "concept",
                        "alternative",
                        "alternative_relation",
                        "occurrence",
                        "assignment",
                        "occurrence_grammar",
                    )
                },
                {
                    "source": 44,
                    "concept": 150,
                    "alternative": 228,
                    "alternative_relation": 19,
                    "occurrence": 253,
                    "assignment": 253,
                    "occurrence_grammar": 253,
                },
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
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
