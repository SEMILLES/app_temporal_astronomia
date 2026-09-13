import unittest
from dataclasses import replace

from lexical_simulation import (
    Alternative, Assignment, ConceptState, LexicalState, Occurrence, Relation,
    calculate_concept_nomenclature, validate_concept_nomenclature,
)


def snapshot(years=(1900, 1910, 2000), edges=((1, 2),)):
    return ConceptState(1, LexicalState(
        (1,), tuple(Alternative(i, 1, None, None, True) for i in range(1, len(years)+1)),
        tuple(Occurrence(i, year, None, None, None, None, None)
              for i, year in enumerate(years, 1)),
        tuple(Assignment(i, i, i) for i in range(1, len(years)+1)),
        tuple(Relation(i, a, b, "movement") for i, (a, b) in enumerate(edges))))


class CanonicalNomenclatureTests(unittest.TestCase):
    def validate(self, labels, state=None):
        return validate_concept_nomenclature(state or snapshot(), labels)

    def assertConflict(self, code, labels, state=None):
        result = self.validate(labels, state)
        self.assertFalse(result["valid"])
        self.assertFalse(result["applicable"])
        self.assertIn(code, [c["code"] for c in result["conflicts"]])

    def test_automatic_and_correct_a(self):
        state = snapshot()
        result = calculate_concept_nomenclature(state)
        self.assertEqual(result["automatic_labels"], {1: "1a", 2: "1b", 3: "2a"})
        self.assertTrue(result["validation"]["valid"])
        self.assertTrue(result["applicable"])
        self.assertTrue(self.validate({1: " 1a ", 2: "1b", 3: "2a"})["valid"])

    def test_invalid_formats(self):
        for label in ("0a", "01a", "1A", "1aa", "a1", "1", "-1a", "texto", "CONCEPTO-1a", "", None):
            with self.subTest(label=label):
                self.assertConflict("INVALID_LABEL_FORMAT", {1: label, 2: "1b", 3: "2a"})

    def test_missing(self):
        self.assertConflict("MISSING_LABEL", {1: "1a", 3: "2a"})

    def test_unknown(self):
        self.assertConflict("UNKNOWN_ALTERNATIVE", {1: "1a", 2: "1b", 3: "2a", 99: "3a"})

    def test_duplicate_and_two_a(self):
        labels = {1: "1a", 2: "1a", 3: "2a"}
        self.assertConflict("DUPLICATE_LABEL", labels)
        self.assertConflict("INVALID_A_COUNT", labels)

    def test_component_two_numbers(self):
        self.assertConflict("COMPONENT_GROUP_MISMATCH", {1: "1a", 2: "2b", 3: "3a"})

    def test_disconnected_shared_number(self):
        self.assertConflict("SHARED_GROUP_NUMBER", {1: "1a", 2: "1b"}, snapshot((1900, 2000), ()))

    def test_group_gap(self):
        self.assertConflict("NONCONSECUTIVE_GROUPS", {1: "1a", 2: "1b", 3: "3a"})

    def test_group_chronology(self):
        self.assertConflict("GROUP_CHRONOLOGY_MISMATCH", {1: "2a", 2: "2b", 3: "1a"})

    def test_missing_a(self):
        self.assertConflict("INVALID_A_COUNT", {1: "1b", 2: "1c", 3: "2a"})

    def test_wrong_a(self):
        self.assertConflict("A_CHRONOLOGY_MISMATCH", {1: "1c", 2: "1a", 3: "2a"})

    def test_letter_gap_allowed(self):
        self.assertTrue(self.validate({1: "1a", 2: "1c", 3: "2a"})["valid"])

    def test_non_a_chronology_free(self):
        state = snapshot(edges=((1, 2), (2, 3)))
        self.assertTrue(self.validate({1: "1a", 2: "1c", 3: "1b"}, state)["valid"])

    def test_capacity(self):
        for count in (26, 27):
            state = snapshot(tuple(range(1900, 1900+count)), tuple((i, i+1) for i in range(1, count)))
            result = calculate_concept_nomenclature(state)
            if count == 26:
                self.assertTrue(result["valid"])
                self.assertEqual(result["automatic_labels"][26], "1z")
            else:
                self.assertEqual(result["automatic_labels"], {})
                self.assertFalse(result["conclusive"])
                self.assertFalse(result["applicable"])
                self.assertIn("VARIANT_CAPACITY_EXCEEDED", [c["code"] for c in result["conflicts"]])

    def test_unknown_and_registration_tie(self):
        for years in ((None, None, None), (1900, 1900, 1900)):
            state = snapshot(years, ((1, 2), (2, 3)))
            alternatives = tuple(replace(a, created_at="2000-01-01 00:00:00" if a.ref == 2 else None)
                                 for a in state.lexical_state.alternatives)
            state = replace(state, lexical_state=replace(state.lexical_state, alternatives=alternatives))
            result = calculate_concept_nomenclature(state)
            self.assertEqual(result["automatic_labels"], {2: "1a", 1: "1b", 3: "1c"})
            self.assertTrue(result["valid"])
            self.assertEqual(result, calculate_concept_nomenclature(state))

    def test_empty_retired_foreign_and_virtual(self):
        state = snapshot()
        lexical = state.lexical_state
        virtual = Alternative("new_alternative:1", 1, None, None, True, 1)
        state = replace(state, lexical_state=replace(lexical,
            alternatives=lexical.alternatives + (virtual, Alternative(4, 1, None, None, False),
                                                  Alternative(5, 2, None, None, True)),
            assignments=()))
        result = calculate_concept_nomenclature(state)
        self.assertTrue(result["valid"])
        self.assertTrue(all(r["reference_year"] is None for r in result["preview_rows"]))
        labels = result["automatic_labels"]
        self.assertIn(virtual.ref, labels)
        self.assertConflict("MISSING_LABEL", {k: v for k, v in labels.items() if k != virtual.ref}, state)
        for ref in (4, 5):
            self.assertConflict("UNKNOWN_ALTERNATIVE", dict(labels, **{str(ref): "4a"}), state)
            self.assertConflict("UNKNOWN_ALTERNATIVE", {**labels, ref: "4a"}, state)


if __name__ == "__main__":
    unittest.main()
