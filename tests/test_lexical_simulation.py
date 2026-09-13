import sqlite3
import unittest
from dataclasses import replace

from database import crear_esquema
from alternative_nomenclature import calculate_nomenclature_preview
from lexical_simulation import (
    ConceptState, LexicalOperation, Relation, ValidityChange, VirtualAlternative,
    build_effective_state, calculate_concept_nomenclature, load_lexical_state,
    simulate_lexical_operation,
)


class LexicalSimulationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('Contract')")
        self.db.executemany("INSERT INTO concept(preferred_label) VALUES(?)", [("X",), ("Y",), ("Unrelated",)])
        for aid, cid, year in ((1, 1, 1990), (2, 1, 2000), (3, 2, 1995), (4, 2, 2005), (5, 3, 1800)):
            self.db.execute("INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,?,?)", (aid, cid, str(aid)+"a"))
            self.add_occurrence(aid, year, aid)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def add_occurrence(self, oid, year, aid=None):
        self.db.execute("INSERT INTO occurrence(occurrence_id,source_id,occurrence_year) VALUES(?,1,?)", (oid, year))
        if aid is not None:
            self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)", (oid, aid))

    def operation(self, destination=4, concept=2, **kwargs):
        return LexicalOperation(1, 1, destination, concept, **kwargs)

    def simulate(self, operation=None):
        return simulate_lexical_operation(self.db, operation or self.operation())

    def years(self, result, cid):
        return {r["alternative_id"]: r["reference_year"] for r in result["affected_concepts"][cid]["preview_rows"]}

    def relation(self, parameter):
        return self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,?)", (parameter,)).lastrowid

    def test_unassigned_to_existing(self):
        self.add_occurrence(6, 1980)
        result = self.simulate(LexicalOperation(6, None, 2, 1))
        self.assertEqual(set(result["affected_concepts"]), {1})
        self.assertEqual(self.years(result, 1)[2], 1980)

    def test_capacity_preview_and_apply_do_not_write(self):
        from alternative_nomenclature import apply_nomenclature, InvalidNomenclatureError
        for aid in range(6, 31):
            self.db.execute("INSERT INTO alternative(alternative_id,concept_id) VALUES(?,1)", (aid,))
            self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,?,'movement')", (aid,))
        self.db.commit()
        # Joining Alternative 2 creates a 27-member component in the effective state.
        result = self.simulate(self.operation(None, None,
            added_relations=(Relation("new_relation:capacity", 1, 2, "movement"),)))
        preview = result["affected_concepts"][1]
        self.assertFalse(preview["conclusive"])
        self.assertFalse(preview["applicable"])
        self.assertEqual(preview["automatic_labels"], {})
        self.assertEqual(preview["conflicts"][0]["code"], "VARIANT_CAPACITY_EXCEEDED")
        refs = (1, 2, *range(6, 31))
        labels = {ref: f"{index+1}a" for index, ref in enumerate(refs)}
        before = "\n".join(self.db.iterdump())
        with self.assertRaisesRegex(InvalidNomenclatureError, "VARIANT_CAPACITY_EXCEEDED"):
            apply_nomenclature(self.db, 1, labels, origin="automatic_assisted", required_edges=((1, 2),))
        self.assertEqual(before, "\n".join(self.db.iterdump()))

    def test_same_concept_move_preserves_destination_evidence(self):
        result = self.simulate(self.operation(2, 1))
        self.assertEqual(set(result["affected_concepts"]), {1})
        self.assertEqual(self.years(result, 1), {1: None, 2: 1990})
        assigned = {(a.occurrence_id, a.alternative_ref) for a in result["effective_state"].assignments}
        self.assertIn((1, 2), assigned)
        self.assertIn((2, 2), assigned)
        self.assertNotIn((1, 1), assigned)

    def test_cross_concept_recalculates_both_and_ignores_reference(self):
        self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,2)")
        result = self.simulate()
        self.assertEqual(set(result["affected_concepts"]), {1, 2})
        self.assertEqual(result["affected_concepts"][1]["automatic_labels"], {2: "1a", 1: "2a"})
        self.assertEqual(result["affected_concepts"][2]["automatic_labels"], {4: "1a", 3: "2a"})
        for concept in result["affected_concepts"].values():
            self.assertTrue(concept["validation"]["valid"])
            self.assertTrue(concept["conclusive"])
            self.assertTrue(concept["applicable"])
        self.assertEqual(self.years(result, 2), {3: 1995, 4: 1990})
        self.assertEqual(result["affected_concepts"][1]["roles"], ("origin",))
        self.assertNotIn(5, {a.ref for a in result["effective_state"].alternatives})

    def test_origin_remaining_evidence_replaces_old_reference(self):
        self.add_occurrence(6, 2010, 1)
        result = self.simulate()
        self.assertEqual(self.years(result, 1)[1], 2010)

    def test_older_destination_evidence_is_not_replaced(self):
        self.add_occurrence(6, 1800, 4)
        self.assertEqual(self.years(self.simulate(), 2)[4], 1800)

    def test_empty_origin_remains_active_and_connected(self):
        rid = self.relation("handshape")
        result = self.simulate()
        self.assertEqual(self.years(result, 1)[1], None)
        self.assertTrue(next(a for a in result["effective_state"].alternatives if a.ref == 1).active)
        self.assertIn(rid, {r.ref for r in result["effective_state"].relations})
        self.assertEqual(result["affected_concepts"][1]["components"], [(2, 1)])

    def test_cross_concept_virtual(self):
        new = VirtualAlternative(2)
        result = self.simulate(self.operation(new.ref, 2, new_alternatives=(new,)))
        self.assertEqual(set(result["affected_concepts"]), {1, 2})
        self.assertEqual(self.years(result, 2)[new.ref], 1990)
        self.assertEqual(result["affected_concepts"][2]["automatic_labels"][new.ref], "1a")

    def test_virtual_ties_after_existing_even_unreliable_registration(self):
        self.db.execute("UPDATE occurrence SET occurrence_year=1990 WHERE occurrence_id=4")
        self.db.execute("UPDATE alternative SET created_at='unknown' WHERE alternative_id=4")
        new = VirtualAlternative(2)
        result = self.simulate(self.operation(new.ref, 2, new_alternatives=(new,),
            added_relations=(Relation("new_relation:1", 4, new.ref, "movement"),)))
        labels = result["affected_concepts"][2]["automatic_labels"]
        self.assertEqual((labels[4], labels[new.ref]), ("1a", "1b"))

    def test_multiple_virtuals_have_stable_order_without_timestamps(self):
        one, two = VirtualAlternative(2, 1), VirtualAlternative(2, 2)
        operation = self.operation(None, None, new_alternatives=(two, one))
        result = self.simulate(operation)
        labels = result["affected_concepts"][2]["automatic_labels"]
        self.assertEqual((labels[one.ref], labels[two.ref]), ("3a", "4a"))
        self.assertTrue(all(a.created_at is None for a in result["effective_state"].alternatives if a.virtual_order))
        self.assertEqual(result, self.simulate(operation))

    def test_virtual_group_tie_after_existing_with_future_timestamp(self):
        self.db.execute("UPDATE occurrence SET occurrence_year=1990 WHERE occurrence_id=4")
        self.db.execute("UPDATE alternative SET created_at='9999-12-31 23:59:59' WHERE alternative_id=4")
        new = VirtualAlternative(2)
        result = self.simulate(self.operation(new.ref, 2, new_alternatives=(new,)))
        labels = result["affected_concepts"][2]["automatic_labels"]
        self.assertEqual((labels[4], labels[new.ref]), ("1a", "2a"))

    def test_relation_endpoints_derive_additional_concepts(self):
        result = self.simulate(self.operation(None, None,
            added_relations=(Relation("new_relation:1", 3, 4, "location"),)))
        self.assertEqual(set(result["affected_concepts"]), {2})
        self.assertEqual(result["affected_concepts"][2]["roles"], ("relation",))

    def test_source_range_and_unknown_evidence(self):
        self.db.execute("UPDATE occurrence SET occurrence_year=NULL WHERE occurrence_id=1")
        self.db.execute("UPDATE source SET start_year=1800,end_year=1900,end_year_status='known'")
        row = next(r for r in self.simulate()["affected_concepts"][2]["preview_rows"] if r["alternative_id"] == 4)
        self.assertEqual((row["reference_year"], row["reference_basis"]), (1800, "source_range_start"))
        self.db.execute("UPDATE source SET start_year=NULL,end_year=NULL")
        self.assertEqual(self.years(self.simulate(), 2)[4], 2005)

    def test_duplicate_virtual_order_rejected(self):
        with self.assertRaises(ValueError):
            self.simulate(self.operation("new_alternative:1", 2,
                new_alternatives=(VirtualAlternative(2), VirtualAlternative(2))))

    def test_new_relation_joins_components(self):
        result = self.simulate(self.operation(None, None, added_relations=(Relation("new_relation:1", 1, 2, "location"),)))
        self.assertEqual(result["affected_concepts"][1]["components"], [(1, 2)])

    def test_remove_one_of_parallel_relations_keeps_edge(self):
        first, second = self.relation("location"), self.relation("movement")
        operation = self.operation(None, None, removed_relation_ids=(first,))
        result = self.simulate(operation)
        self.assertEqual(result["affected_concepts"][1]["components"], [(1, 2)])
        self.assertEqual([r.ref for r in result["effective_state"].relations], [second])
        result = self.simulate(replace(operation, removed_relation_ids=(first, second)))
        self.assertEqual(result["affected_concepts"][1]["components"], [(1,), (2,)])

    def test_rejection_does_not_retire_canonical_relation(self):
        rid = self.relation("location")
        result = self.simulate(self.operation(None, None))
        self.assertEqual(result["affected_concepts"], {})
        self.assertEqual([r.ref for r in result["effective_state"].relations], [rid])

    def test_explicit_validity_only(self):
        result = self.simulate(self.operation(None, None, validity_changes=(ValidityChange(2, False),)))
        self.assertEqual(result["affected_concepts"][1]["automatic_labels"], {1: "1a"})

    def test_stale_assignment_and_mismatched_destination_fail(self):
        for operation in (replace(self.operation(), expected_assignment_id=None), self.operation(4, 1)):
            with self.assertRaises(ValueError):
                self.simulate(operation)

    def test_pure_transform_after_connection_closed_and_input_unchanged(self):
        operation = self.operation()
        state = load_lexical_state(self.db, operation)
        before = repr(state)
        self.db.close()
        effective = build_effective_state(state, operation)
        self.assertEqual(repr(state), before)
        self.assertEqual(calculate_concept_nomenclature(ConceptState(2, effective))["automatic_labels"], {4: "1a", 3: "2a"})

    def test_matches_existing_calculator_and_source_temporal_rules(self):
        self.db.execute("UPDATE occurrence SET occurrence_year=NULL WHERE occurrence_id=1")
        self.db.execute("UPDATE source SET start_year=1850,end_year=1850,end_year_status='known'")
        self.relation("location")
        state = load_lexical_state(self.db, self.operation())
        for cid in (1, 2):
            expected = calculate_nomenclature_preview(self.db, cid)
            actual = calculate_concept_nomenclature(ConceptState(cid, state))
            self.assertEqual(actual["automatic_labels"], expected["suggestions"])
            self.assertEqual(actual["components"], expected["components"])
            for row, old in zip(actual["preview_rows"], expected["rows"]):
                self.assertEqual({k: row[k] for k in old}, old)

    def test_read_only_authorizer_and_unchanged_database(self):
        rid = self.relation("location")
        self.db.commit()
        before = "\n".join(self.db.iterdump())
        changes = self.db.total_changes
        denied = {getattr(sqlite3, name) for name in dir(sqlite3)
                  if name.startswith(("SQLITE_CREATE_", "SQLITE_DROP_"))}
        denied.update((sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                       sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT))
        attempts = []
        def authorize(action, *args):
            if action in denied:
                attempts.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)
        try:
            new = VirtualAlternative(2)
            result = self.simulate(self.operation(new.ref, 2, new_alternatives=(new,),
                removed_relation_ids=(rid,), added_relations=(Relation("new_relation:1", 4, new.ref, "location"),)))
            self.assertEqual(set(result["affected_concepts"]), {1, 2})
            self.assertEqual(attempts, [])
        finally:
            self.db.set_authorizer(None)
        self.assertEqual(before, "\n".join(self.db.iterdump()))
        self.assertEqual(changes, self.db.total_changes)
        self.assertFalse(self.db.in_transaction)


if __name__ == "__main__":
    unittest.main()
