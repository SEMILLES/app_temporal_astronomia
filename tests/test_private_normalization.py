import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import crear_esquema
from normalization.astronomy_normalizer import (
    NormalizationError, _safe_database, audit_database, normalize,
)


class PrivateNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.raw = tempfile.TemporaryDirectory()
        self.path = Path(self.raw.name) / "normalization_test.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_id,source_name) VALUES(1,'PRIVATE SOURCE')")
        self.db.execute("INSERT INTO concept(concept_id,preferred_label) VALUES(1,'ONE')")
        self.db.executemany(
            "INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,1,?)",
            ((1, "ONE-A"), (2, "ONE-B")),
        )
        self.db.executemany(
            "INSERT INTO occurrence(occurrence_id,source_id,original_gloss,occurrence_year) VALUES(?,1,?,?)",
            ((1, "ONE", None), (2, "TWO", 1999)),
        )
        self.db.executemany(
            "INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)", ((1, 1), (2, 2))
        )
        self.db.execute("""
            INSERT INTO occurrence_grammar(occurrence_id,gender,plural,agentive,
            conjugated_form,negation,is_current) VALUES(1,'SIN-MARCA','SIN-MARCA',
            'LEGACY','LEGACY','SIN-NEG',1)
        """)
        self.db.execute("""
            INSERT INTO occurrence_grammar(occurrence_id,gender,plural,agentive,
            conjugated_form,negation,is_current) VALUES(2,'FEM','SIN-MARCA',
            'LEGACY','SI','CON-NEG',1)
        """)
        self.db.execute("""
            INSERT INTO alternative_morphology(alternative_id,component_count,
            component_count_not_applicable,free_permutation,is_current)
            VALUES(2,1,0,'N/A',1)
        """)
        self.db.execute("""
            INSERT INTO alternative_component(alternative_morphology_id,position,component_label)
            VALUES(last_insert_rowid(),1,'EXPLICIT')
        """)
        self.db.commit()
        self.config = {
            "legacy_grammar_placeholders": {"agentive": ["LEGACY"], "conjugated_form": ["LEGACY"]},
            "sources": [{"match": {"source_name": "PRIVATE SOURCE"}, "rename": "RENAMED", "year": 2026}],
        }

    def tearDown(self):
        self.db.close(); self.raw.cleanup()

    def snapshot(self):
        return self.db.serialize()

    def test_dry_run_reports_exact_plan_and_writes_nothing(self):
        before = self.snapshot(); result = normalize(self.db, self.config)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result["grammar_versions_to_create"], 2)
        self.assertEqual(result["morphology_versions_to_create"], 1)
        self.assertEqual(result["indirectly_affected_occurrences"], 2)
        self.assertEqual(result["occurrence_year_modified"], 0)

    def test_apply_preserves_explicit_values_history_and_unrelated_data(self):
        assignments = self.db.execute("SELECT * FROM assignment ORDER BY assignment_id").fetchall()
        explicit_morphology = self.db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=2").fetchall()
        result = normalize(self.db, self.config, apply=True)
        source = self.db.execute("SELECT source_name,start_year,end_year FROM source").fetchone()
        self.assertEqual(tuple(source), ("RENAMED", 2026, 2026))
        self.assertEqual(self.db.execute("SELECT occurrence_year FROM occurrence WHERE occurrence_id=1").fetchone()[0], None)
        self.assertEqual(self.db.execute("SELECT occurrence_year FROM occurrence WHERE occurrence_id=2").fetchone()[0], 1999)
        grammar = self.db.execute("SELECT * FROM occurrence_grammar WHERE occurrence_id=2 AND is_current=1").fetchone()
        self.assertEqual((grammar["gender"], grammar["agentive"], grammar["conjugated_form"], grammar["negation"]),
                         ("FEM", "N/A", "SI", "CON-NEG"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM occurrence_grammar WHERE is_current=0").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT * FROM assignment ORDER BY assignment_id").fetchall(), assignments)
        self.assertEqual(self.db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=2").fetchall(), explicit_morphology)
        self.assertEqual(self.db.execute("SELECT count(*) FROM submission").fetchone()[0], 0)
        self.assertEqual(result["videos_modified"], 0)

    def test_generic_morphology_is_na_without_components(self):
        normalize(self.db, self.config, apply=True)
        row = self.db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=1 AND is_current=1").fetchone()
        self.assertEqual((row["component_count"], row["component_count_not_applicable"], row["free_permutation"]),
                         (None, 1, "N/A"))
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_component WHERE alternative_morphology_id=?", (row["alternative_morphology_id"],)).fetchone()[0], 0)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_second_apply_is_idempotent(self):
        normalize(self.db, self.config, apply=True)
        counts = tuple(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                       for table in ("source_revision", "occurrence_grammar", "alternative_morphology", "alternative_component"))
        second = normalize(self.db, self.config, apply=True)
        self.assertEqual(second["source_changes"], [])
        self.assertEqual(second["grammar_versions_to_create"], 0)
        self.assertEqual(second["morphology_versions_to_create"], 0)
        self.assertEqual(counts, tuple(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                       for table in ("source_revision", "occurrence_grammar", "alternative_morphology", "alternative_component")))

    def test_exact_private_mapping_must_be_unambiguous(self):
        self.db.execute("INSERT INTO source(source_name) VALUES('SECOND')"); self.db.commit()
        bad = {"sources": [{"match": {"start_year": None}, "year": 2020}]}
        with self.assertRaisesRegex(NormalizationError, "match exacto permitido"):
            normalize(self.db, bad)

    def test_candidate_prototype_and_write_test_names_are_protected(self):
        for name in ("lesico_prototipo.db", "lesico_astronomia_candidate.db", "lesico_astronomia_write_test.db"):
            path = Path(self.raw.name) / name; path.touch()
            with self.assertRaisesRegex(NormalizationError, "protegida"):
                _safe_database(path)

    def test_audit_is_json_serializable(self):
        first = audit_database(self.db)
        self.assertEqual(first, audit_database(self.db))
        json.dumps(first)


if __name__ == "__main__":
    unittest.main()
