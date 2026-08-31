import json
import sqlite3
import unittest

from catalog_projection import build_catalog_projection
from database import crear_esquema


class CatalogProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('Fuente')")
        self.db.executemany(
            "INSERT INTO concept(preferred_label) VALUES(?)",
            [("ZETA",), ("ALFA",), ("SOLO-PROPUESTA",)],
        )
        self.db.executemany(
            "INSERT INTO occurrence(source_id,original_gloss) VALUES(1,?)",
            [("vigente",), ("histórica",), ("sin asignar",)],
        )
        self.db.executemany(
            "INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(?,?,?)",
            [(1, "1b", None), (2, "1a", None), (1, "9z", "2020-01-01")],
        )
        self.db.executemany(
            "INSERT INTO assignment(occurrence_id,alternative_id,is_current) VALUES(?,?,?)",
            [(1, 1, 1), (2, 1, 0), (2, 3, 1)],
        )
        self.db.executemany(
            "INSERT INTO occurrence_grammar(occurrence_id,gender,is_current) VALUES(?,?,?)",
            [(1, "ANTERIOR", 0), (1, "VIGENTE", 1)],
        )
        self.db.executemany(
            "INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation,is_current,note) VALUES(?,?,?,?,?)",
            [(1, 1, "N/A", 0, "anterior"), (1, 1, "N/A", 1, "vigente")],
        )
        morphology_id = self.db.execute(
            "SELECT alternative_morphology_id FROM alternative_morphology WHERE is_current=1"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO alternative_component(alternative_morphology_id,position,component_alternative_id) VALUES(?,?,?)",
            (morphology_id, 1, 2),
        )
        self.db.executemany(
            "INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,is_current) VALUES(?,?,?,?)",
            [(1, 2, "CM_2", 1), (1, 2, "CM_1", 1), (1, 2, "OLD", 0), (1, 3, "RETIRED", 1)],
        )
        # Workflow-only rows must never create lexical catalog entries.
        self.db.execute(
            "INSERT INTO concept_proposal(proposed_label,status) VALUES('PENDIENTE','pending')"
        )
        self.db.execute(
            "INSERT INTO submission(occurrence_id,submission_type,status,resolution) VALUES(3,'ALTERNATIVE','pending',NULL)"
        )
        self.db.execute(
            "INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id) VALUES(1,'NEW',3)"
        )
        self.db.execute(
            "INSERT INTO submission(occurrence_id,submission_type,status,resolution) VALUES(3,'ALTERNATIVE','resolved','rejected')"
        )
        self.db.execute(
            "INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id) VALUES(2,'NEW',3)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_projects_only_current_canonical_state(self):
        projection = build_catalog_projection(self.db)
        self.assertEqual([c["preferred_label"] for c in projection["concepts"]], ["ALFA", "ZETA"])
        alternatives = [a for c in projection["concepts"] for a in c["alternatives"]]
        self.assertEqual({a["alternative_id"] for a in alternatives}, {1, 2})
        zeta = next(c for c in projection["concepts"] if c["preferred_label"] == "ZETA")
        alternative = zeta["alternatives"][0]
        self.assertEqual([o["original_gloss"] for o in alternative["occurrences"]], ["vigente"])
        self.assertEqual(alternative["occurrences"][0]["grammar"]["fields"]["gender"]["value"], "VIGENTE")
        self.assertEqual(alternative["morphology"]["note"], "vigente")
        self.assertEqual(alternative["morphology"]["components"][0]["component_alternative_name"], "ALFA-1a")
        self.assertEqual([r["phonological_parameter"] for r in zeta["relations"]], ["CM_1", "CM_2"])

    def test_is_repeatable_deterministic_and_json_serializable(self):
        first = build_catalog_projection(self.db)
        second = build_catalog_projection(self.db)
        self.assertEqual(first, second)
        encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertEqual(json.loads(encoded), first)
        self.assertNotIn("created_at", encoded)
        self.assertNotIn("submission", encoded)
        self.assertNotIn("conflict", encoded)


if __name__ == "__main__":
    unittest.main()
