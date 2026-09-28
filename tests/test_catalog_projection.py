import json
import sqlite3
import unittest

from catalog_projection import build_catalog_projection
from database import crear_esquema
from concept_classification import apply_metadata, administer


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

    def test_orders_working_labels_structurally(self):
        self.db.executemany(
            "INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",
            [("10a",), ("2a",), ("1c",), ("1a",), ("1b",)],
        )
        self.db.commit()
        projection = build_catalog_projection(self.db)
        zeta = next(c for c in projection["concepts"] if c["preferred_label"] == "ZETA")
        self.assertEqual(
            [item["working_label"] for item in zeta["alternatives"]],
            ["1a", "1b", "1b", "1c", "2a", "10a"],
        )

    def test_is_repeatable_deterministic_and_json_serializable(self):
        first = build_catalog_projection(self.db)
        second = build_catalog_projection(self.db)
        self.assertEqual(first, second)
        encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertEqual(json.loads(encoded), first)
        self.assertNotIn("created_at", encoded)
        self.assertNotIn("submission", encoded)
        self.assertNotIn("conflict", encoded)

    def test_current_metadata_structured_or_legacy_and_read_only(self):
        systems = {r['code']: r['system_id'] for r in self.db.execute('SELECT * FROM classification_system')}
        categories = {code: [dict(r) for r in self.db.execute(
            'SELECT * FROM classification_category WHERE system_id=? ORDER BY display_order', (sid,))]
            for code, sid in systems.items()}
        academic = self.db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        other = administer(self.db, 'collection', code='aaa-other', name='Otra', access_role='master')
        self.db.execute("UPDATE concept SET semantic_field_1='Legacy SF', semantic_field_2='Legacy SF 2', knowledge_area_1='Legacy KA'")
        self.db.commit()
        def apply(classifications=None, collections=None, concept=1):
            apply_metadata(self.db, concept, {'classifications': classifications or {},
                'collections': collections or {}}, access_role='master')
        def concept():
            return next(c for c in build_catalog_projection(self.db)['concepts'] if c['concept_id'] == 1)
        self.assertEqual(concept()['semantic_fields'], ['Legacy SF', 'Legacy SF 2'])
        self.assertEqual(concept()['knowledge_areas'], ['Legacy KA'])
        self.assertEqual(concept()['classifications'], {})
        sf, ka = systems['semantic-fields'], systems['knowledge-areas']
        apply({sf: [categories['semantic-fields'][0]['category_id']]})
        selected = {code: list(reversed(rows[1:3])) for code, rows in categories.items()}
        apply({systems[code]: [r['category_id'] for r in rows] for code, rows in selected.items()},
              {academic: 'join', other: 'join'})
        # Metadata on an ineligible concept must not make it eligible.
        apply(collections={academic: 'join'}, concept=3)
        administer(self.db, 'collection', identifier=academic, name='Nombre actual', active=0, access_role='master')
        administer(self.db, 'category', identifier=selected['semantic-fields'][0]['category_id'],
                   name='Nombre posterior', active=0, access_role='master')
        before = '\n'.join(self.db.iterdump())
        changes = self.db.total_changes
        self.db.execute('PRAGMA query_only=ON')
        first = build_catalog_projection(self.db)
        self.assertEqual(first, build_catalog_projection(self.db))
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        self.assertEqual(changes, self.db.total_changes)
        self.db.execute('PRAGMA query_only=OFF')
        current = concept()
        self.assertEqual([c['concept_id'] for c in first['concepts']], [2, 1])
        self.assertEqual(current['collections'], [
            {'collection_id': other, 'code': 'aaa-other', 'name': 'Otra'},
            {'collection_id': academic, 'code': 'academic-vocabulary', 'name': 'Nombre actual'}])
        self.assertEqual(list(current['classifications']), sorted(systems))
        for code, legacy in [('semantic-fields', 'semantic_fields'), ('knowledge-areas', 'knowledge_areas')]:
            self.assertEqual(current['classifications'][code], [{'code': r['code'], 'name': r['name']} for r in selected[code]])
            self.assertEqual(current[legacy], [r['name'] for r in selected[code]])
        apply({sf: []}, {academic: 'leave'})
        self.assertEqual(concept()['semantic_fields'], [])
        self.assertEqual(concept()['classifications'], {'semantic-fields': []})
        self.assertEqual(concept()['knowledge_areas'], ['Legacy KA'])
        self.assertEqual([c['collection_id'] for c in concept()['collections']], [other])


if __name__ == "__main__":
    unittest.main()
