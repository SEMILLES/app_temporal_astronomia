import unittest

from concept_labels import (
    InvalidConceptLabel,
    alternative_display_label,
    human_concept_label,
    normalize_concept_label,
)


class NormalizeConceptLabelTests(unittest.TestCase):

    def test_valid_labels(self):
        cases = {
            "agujero negro": "AGUJERO-NEGRO",
            "AGUJERO-NEGRO": "AGUJERO-NEGRO",
            "  materia   oscura  ": "MATERIA-OSCURA",
            "AGUJERO---NEGRO": "AGUJERO-NEGRO",
            "rotación / traslación": "ROTACIÓN/TRASLACIÓN",
            "A B/C D": "A-B/C-D",
            "áéíóú ü": "ÁÉÍÓÚ-Ü",
            "niño": "NIÑO",
            "niño 2": "NIÑO-2",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_concept_label(value), expected)

    def test_normalization_is_idempotent(self):
        canonical = "ROTACIÓN/TRASLACIÓN-2"
        self.assertEqual(normalize_concept_label(canonical), canonical)

    def test_invalid_labels(self):
        invalid = (
            "", "   ", "AGUJERO (NEGRO)", "AGUJERO.NEGRO", "A,B",
            "A:B", "A_B", "O'BRIEN", "A–B", "A—B", "/A", "A/", "A//B",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(InvalidConceptLabel):
                    normalize_concept_label(value)


class ConceptLabelPresentationTests(unittest.TestCase):

    def test_human_concept_label(self):
        self.assertEqual(human_concept_label("AGUJERO-NEGRO"), "AGUJERO NEGRO")
        self.assertEqual(
            human_concept_label("ROTACIÓN/TRASLACIÓN"),
            "ROTACIÓN/TRASLACIÓN",
        )

    def test_alternative_display_label(self):
        self.assertEqual(
            alternative_display_label("AGUJERO-NEGRO", "1a"),
            "AGUJERO-NEGRO-1a",
        )
        self.assertIsNone(alternative_display_label("AGUJERO-NEGRO", None))


if __name__ == "__main__":
    unittest.main()
