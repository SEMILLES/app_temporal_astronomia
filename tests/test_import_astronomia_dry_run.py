import csv
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from import_astronomia import CorpusExpectations, OccurrenceInput, format_report, run_dry_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "astronomia_import"
NOTE = "Nota gramatical ficticia exclusiva de esta evidencia."
NOTE_KEY = "legacy:PRUEBA-001"
NEGATED_KEY = "unal:CENTRO-DE-PRUEBA|OBJETO-DE-PRUEBA-1a|GLOSA-FICTICIA-BETA"
EXPECTATIONS = CorpusExpectations(
    3, (("INSTITUTIONAL", 1), ("PERSONAL", 2)), "CODIGO-COMPARTIDO", 2,
    3, 4, 1, 1, 4, 2,
    ((NOTE_KEY, ("PRUEBA-001", "GLOSA-FICTICIA-ALFA", "FENOMENO-ALFA-1a", "CENTRO-DE-PRUEBA"), NOTE),),
    (NEGATED_KEY, None, "GLOSA-FICTICIA-BETA", "OBJETO-DE-PRUEBA-1a", "CENTRO-DE-PRUEBA"),
    1, 2, 1, 1, 1,
    frozenset({"FENOMENO-ALFA-1b"}),
)


class ImportAstronomiaDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_dry_run(FIXTURES, EXPECTATIONS)

    def copied_inputs(self):
        temporary = tempfile.TemporaryDirectory()
        target = Path(temporary.name) / "astronomia"
        shutil.copytree(FIXTURES, target)
        return temporary, target

    def rewrite_tsv(self, name, mutate, target):
        path = target / name
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            headers, rows = reader.fieldnames, list(reader)
        mutate(rows)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def test_synthetic_inputs_pass_and_create_validated_plan(self):
        self.assertTrue(self.result.ready_for_apply, self.result.errors)
        self.assertEqual(self.result.errors, ())
        self.assertIsNotNone(self.result.validated_plan)

    def test_structural_counts(self):
        m = self.result.models
        self.assertEqual(tuple(map(len, (m.sources, m.concepts, m.alternatives, m.relations,
                                        m.occurrences, m.assignments, m.grammar, m.excluded))),
                         (3, 3, 4, 1, 4, 4, 4, 1))

    def test_legacy_new_and_unique_occurrences(self):
        items = self.result.models.occurrences
        self.assertEqual(sum(x.legacy_occurrence_id is not None for x in items), 2)
        self.assertEqual(sum(x.legacy_occurrence_id is None for x in items), 2)
        self.assertEqual(len({x.key for x in items}), 4)

    def test_hyphenated_concept_short_label_and_original_code(self):
        items = {x.canonical_code: x for x in self.result.models.alternatives}
        self.assertEqual((items["OBJETO-DE-PRUEBA-1a"].concept_label,
                          items["OBJETO-DE-PRUEBA-1a"].working_label),
                         ("OBJETO-DE-PRUEBA", "1a"))
        self.assertEqual(items["FENOMENO-ALFA-1b"].original_code, "LEGACY-FICTICIO-7z")
        self.assertIsNone(items["FENOMENO-ALFA-1a"].original_code)

    def test_source_scopes_and_shared_code_keep_sources_distinct(self):
        sources = self.result.models.sources
        self.assertEqual(Counter(x.scope for x in sources), {"INSTITUTIONAL": 1, "PERSONAL": 2})
        shared = [x for x in sources if x.legacy_code == "CODIGO-COMPARTIDO"]
        self.assertEqual({x.name for x in shared}, {"REPOSITORIO-FICTICIO-A", "REPOSITORIO-FICTICIO-B"})
        self.assertEqual(len({x.key for x in shared}), 2)

    def test_occurrences_resolve_and_have_one_assignment(self):
        m = self.result.models
        self.assertEqual(len(m.assignments), len(m.occurrences))
        self.assertTrue({x.alternative_code for x in m.occurrences} <= {x.canonical_code for x in m.alternatives})

    def test_explicit_relation(self):
        relation = self.result.models.relations[0]
        self.assertEqual((relation.alternative_a, relation.alternative_b, relation.parameter),
                         ("FENOMENO-ALFA-1a", "FENOMENO-ALFA-1b", "CM_1"))

    def test_grammar_note_belongs_to_one_occurrence(self):
        noted = [x for x in self.result.models.grammar if x.grammar_note]
        self.assertEqual([(x.occurrence_key, x.grammar_note) for x in noted], [(NOTE_KEY, NOTE)])
        self.assertEqual(noted[0].conjugated_form, "SÍ")

    def test_negation_belongs_to_one_occurrence(self):
        negated = [x for x in self.result.models.grammar if x.negation == "CON-NEG"]
        self.assertEqual([x.occurrence_key for x in negated], [NEGATED_KEY])

    def test_exclusion(self):
        self.assertEqual([x.legacy_occurrence_id for x in self.result.models.excluded],
                         ["PRUEBA-EXCLUIDA-001"])

    def test_null_values(self):
        item = next(x for x in self.result.models.occurrences if x.original_gloss == "GLOSA-FICTICIA-GAMMA")
        self.assertIsNone(item.legacy_occurrence_id)
        self.assertIsNone(item.legacy_source_detail_1)
        self.assertIsNone(item.legacy_source_detail_2)
        self.assertIsNone(item.source_locator)
        self.assertIsNone(item.hyperlink)
        self.assertIsNone(item.occurrence_year)

    def test_literal_details_and_source_year_are_separate(self):
        item = next(x for x in self.result.models.occurrences if x.legacy_occurrence_id == "PRUEBA-002")
        self.assertEqual(item.legacy_source_detail_1, "detalle literal ficticio / A")
        self.assertEqual(item.legacy_source_detail_2, "página simulada 42")
        self.assertEqual(item.source_locator, "localizador explícito ficticio")
        self.assertEqual(item.legacy_source_year, "1999-2001")
        self.assertIsNone(item.occurrence_year)

    def test_provenance_warning_and_deferred_contract(self):
        self.assertEqual((self.result.provenance_reviewed, self.result.provenance_component,
                          self.result.provenance_candidates, self.result.provenance_review), (2, 1, 1, 1))
        self.assertEqual(len(self.result.warnings), 2)
        self.assertTrue(any("metadata_anomaly_note preservada" in x for x in self.result.warnings))
        self.assertTrue(any("provenance pendiente de revisión" in x for x in self.result.warnings))
        self.assertTrue(self.result.deferred)
        self.assertIn("provenance_note import currently disabled", format_report(self.result))
        self.assertNotIn("provenance_note", {x.name for x in fields(OccurrenceInput)})

    def test_models_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.result.models.sources[0].name = "Changed"

    def test_missing_input_blocks_plan(self):
        temporary, target = self.copied_inputs()
        try:
            (target / "resumen_occurrences_astronomia_v1.md").unlink()
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertIsNone(result.validated_plan)
            self.assertTrue(any("input obligatorio ausente" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_duplicate_source_is_blocking(self):
        temporary, target = self.copied_inputs()
        try:
            self.rewrite_tsv("reconstruccion_sources_astronomia_v1.tsv",
                             lambda rows: rows[1].__setitem__("source_name", rows[0]["source_name"]), target)
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertTrue(any("source_name vacío o duplicado" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_unresolved_alternative_blocks_plan(self):
        temporary, target = self.copied_inputs()
        try:
            self.rewrite_tsv("reconstruccion_occurrences_astronomia_v1.tsv",
                             lambda rows: rows[2].__setitem__("Alternative canónica", "NO-EXISTE-1a"), target)
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertIsNone(result.validated_plan)
            self.assertTrue(any("alternative no resoluble" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_invalid_relation_is_blocking(self):
        temporary, target = self.copied_inputs()
        try:
            path = target / "reconstruccion_alternatives_astronomia_v2.md"
            path.write_text(path.read_text(encoding="utf-8-sig").replace("| CM_1 |", "| INVALID |", 1), encoding="utf-8-sig")
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertTrue(any("parámetro desconocido" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_invalid_grammar_blocks_plan(self):
        temporary, target = self.copied_inputs()
        try:
            self.rewrite_tsv("reconstruccion_occurrences_astronomia_v1.tsv",
                             lambda rows: rows[2].__setitem__("Género", ""), target)
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertIsNone(result.validated_plan)
            self.assertTrue(any("grammar inválido" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_exclusion_overlap_blocks_plan(self):
        temporary, target = self.copied_inputs()
        try:
            self.rewrite_tsv("reconstruccion_occurrences_astronomia_v1.tsv",
                             lambda rows: rows[2].__setitem__("Legacy occurrence", "PRUEBA-EXCLUIDA-001"), target)
            result = run_dry_run(target, EXPECTATIONS)
            self.assertFalse(result.ready_for_apply)
            self.assertIsNone(result.validated_plan)
            self.assertTrue(any("IDs presentes también" in x for x in result.errors))
        finally:
            temporary.cleanup()

    def test_deterministic_report(self):
        self.assertEqual(format_report(run_dry_run(FIXTURES, EXPECTATIONS)),
                         format_report(run_dry_run(FIXTURES, EXPECTATIONS)))

    def test_no_input_mutation_or_sqlite_write(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in FIXTURES.iterdir()}
        database = ROOT / "lesico_prototipo.db"
        database_hash = hashlib.sha256(database.read_bytes()).hexdigest() if database.exists() else None
        run_dry_run(FIXTURES, EXPECTATIONS)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in FIXTURES.iterdir()})
        if database_hash is None:
            self.assertFalse(database.exists())
        else:
            self.assertEqual(database_hash, hashlib.sha256(database.read_bytes()).hexdigest())

    def test_apply_requires_database(self):
        result = subprocess.run([sys.executable, str(ROOT / "import_astronomia.py"), "--apply", str(FIXTURES)],
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("READY FOR APPLY", result.stdout)


if __name__ == "__main__":
    unittest.main()
