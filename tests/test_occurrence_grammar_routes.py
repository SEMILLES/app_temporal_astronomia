import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from occurrence_grammar import create_or_replace_occurrence_grammar
from routes.main import main_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp


ROOT = Path(__file__).resolve().parents[1]


class OccurrenceGrammarRouteTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.db"
        self.previous_database_path = database.BASE_DATOS
        database.BASE_DATOS = self.database_path

        connection = sqlite3.connect(self.database_path)
        database.crear_esquema(connection)
        connection.execute(
            "INSERT INTO source (source_name) VALUES ('Synthetic source')"
        )
        for gloss in (
            "NO-WORKFLOW", "PENDING", "REJECTED", "ACCEPTED", "ASSIGNED"
        ):
            connection.execute(
                "INSERT INTO occurrence (source_id, original_gloss, hyperlink) "
                "VALUES (1, ?, 'https://example.test/evidence')",
                (gloss,),
            )
        for occurrence_id, status in ((2, "pending"), (3, "rejected"), (4, "accepted")):
            connection.execute(
                "INSERT INTO submission (occurrence_id, proposal_type, status) "
                "VALUES (?, 'not_sure', ?)",
                (occurrence_id, status),
            )
        connection.execute(
            "INSERT INTO concept (preferred_label) VALUES ('ASTRONOMIA')"
        )
        connection.execute(
            "INSERT INTO alternative (concept_id) VALUES (1)"
        )
        connection.execute(
            "INSERT INTO assignment (occurrence_id, alternative_id) VALUES (5, 1)"
        )
        connection.commit()
        connection.close()

        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.jinja_env.filters["human_concept_label"] = human_concept_label
        app.jinja_env.filters["alternative_display_label"] = alternative_display_label
        app.register_blueprint(main_bp)
        app.register_blueprint(occurrences_bp)
        app.register_blueprint(submissions_bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        database.BASE_DATOS = self.previous_database_path
        self.temporary_directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def grammar_rows(self, occurrence_id=1):
        connection = self.connect()
        try:
            return connection.execute(
                "SELECT * FROM occurrence_grammar WHERE occurrence_id = ? "
                "ORDER BY occurrence_grammar_id",
                (occurrence_id,),
            ).fetchall()
        finally:
            connection.close()

    def test_get_without_grammar_shows_empty_registration_form_and_context(self):
        response = self.client.get("/ocurrencias/1/gramatica")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Synthetic source", page)
        self.assertIn("NO-WORKFLOW", page)
        self.assertIn("https://example.test/evidence", page)
        self.assertIn("Registrar análisis gramatical", page)
        self.assertIn('name="gender"', page)
        self.assertIn('value=""', page)
        self.assertIn('list="negation-options"', page)
        self.assertIn('option value="SIN-NEG"', page)
        self.assertIn('option value="CON-NEG"', page)
        self.assertEqual(
            self.client.get("/ocurrencias/999/gramatica").status_code, 404
        )

    def test_get_with_current_prefills_content_but_not_change_note(self):
        connection = self.connect()
        create_or_replace_occurrence_grammar(
            connection, 1, gender="SIN-MARCA", grammar_note="Visible note",
            change_note="Historical reason"
        )
        connection.close()

        response = self.client.get("/ocurrencias/1/gramatica")
        page = response.get_data(as_text=True)
        self.assertIn('name="gender" list="gender-options" value="SIN-MARCA"', page)
        self.assertIn("Visible note", page)
        self.assertIn("Guardar corrección", page)
        change_note = re.search(
            r'<textarea name="change_note">(.*?)</textarea>', page, re.DOTALL
        )
        self.assertIsNotNone(change_note)
        self.assertEqual(change_note.group(1), "")
        self.assertIn("Historical reason", page)

    def test_first_post_accepts_suggestions_open_values_and_notes(self):
        response = self.client.post(
            "/ocurrencias/1/gramatica",
            data={
                "gender": "SIN-MARCA",
                "plural": "DUAL-INNOVADOR",
                "agentive": "",
                "conjugated_form": "SÍ",
                "negation": "SIN-NEG",
                "grammar_note": "Observed grammar",
                "change_note": "Initial analysis",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("?result=saved"))
        rows = self.grammar_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["is_current"], 1)
        self.assertEqual(rows[0]["gender"], "SIN-MARCA")
        self.assertEqual(rows[0]["plural"], "DUAL-INNOVADOR")
        self.assertIsNone(rows[0]["agentive"])
        self.assertEqual(rows[0]["negation"], "SIN-NEG")
        self.assertEqual(rows[0]["grammar_note"], "Observed grammar")
        self.assertEqual(rows[0]["change_note"], "Initial analysis")
        self.assertIsNone(rows[0]["created_by"])

    def test_correction_versions_content_and_empty_field_becomes_null(self):
        connection = self.connect()
        first_id, _ = create_or_replace_occurrence_grammar(
            connection, 1, gender="SIN-MARCA", plural="SIN-MARCA",
            grammar_note="First"
        )
        connection.close()
        response = self.client.post(
            "/ocurrencias/1/gramatica",
            data={
                "gender": "",
                "plural": "SIN-MARCA",
                "agentive": "",
                "conjugated_form": "",
                "negation": "",
                "grammar_note": "Corrected",
                "change_note": "Correction reason",
            },
        )
        self.assertEqual(response.status_code, 302)
        rows = self.grammar_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["is_current"] for row in rows], [0, 1])
        self.assertIsNone(rows[1]["gender"])
        self.assertEqual(rows[1]["grammar_note"], "Corrected")
        self.assertEqual(rows[1]["supersedes_occurrence_grammar_id"], first_id)

    def test_identical_content_and_change_note_only_are_noop(self):
        connection = self.connect()
        create_or_replace_occurrence_grammar(
            connection, 1, plural="SIN-MARCA", grammar_note="Stable"
        )
        connection.close()
        for change_note in ("", "A new reason only"):
            with self.subTest(change_note=change_note):
                response = self.client.post(
                    "/ocurrencias/1/gramatica",
                    data={
                        "gender": "", "plural": "SIN-MARCA", "agentive": "",
                        "conjugated_form": "", "negation": "",
                        "grammar_note": "Stable", "change_note": change_note,
                    },
                )
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.endswith("?result=noop"))
        self.assertEqual(len(self.grammar_rows()), 1)
        page = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("No hubo cambios en el análisis gramatical.", page)

    def test_empty_post_returns_400_preserves_input_and_keeps_current(self):
        connection = self.connect()
        current_id, _ = create_or_replace_occurrence_grammar(
            connection, 1, negation="SIN-NEG"
        )
        connection.close()
        response = self.client.post(
            "/ocurrencias/1/gramatica",
            data={
                "gender": "   ", "plural": "", "agentive": "",
                "conjugated_form": "", "negation": "", "grammar_note": "",
                "change_note": "Reason alone",
            },
        )
        self.assertEqual(response.status_code, 400)
        page = response.get_data(as_text=True)
        self.assertIn("debe contener al menos un dato", page)
        self.assertIn('value="   "', page)
        self.assertIn("Reason alone", page)
        rows = self.grammar_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["occurrence_grammar_id"], current_id)
        self.assertEqual(rows[0]["is_current"], 1)

    def test_history_contains_all_versions_in_order_and_is_read_only(self):
        connection = self.connect()
        first_id, _ = create_or_replace_occurrence_grammar(
            connection, 1, gender="SIN-MARCA", grammar_note="Old note",
            change_note="Old reason"
        )
        second_id, _ = create_or_replace_occurrence_grammar(
            connection, 1, plural="SIN-MARCA", grammar_note="New note",
            change_note="New reason"
        )
        connection.close()
        page = self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True)
        current_position = page.index(f"Versión #{second_id}")
        historical_position = page.index(f"Versión #{first_id}")
        self.assertLess(current_position, historical_position)
        self.assertIn("Vigente", page[current_position:historical_position])
        self.assertIn("Histórica", page[historical_position:])
        self.assertIn("Old note", page)
        self.assertIn("Old reason", page)
        self.assertIn("No registrado", page)
        self.assertEqual(page.count('<form method="post"'), 1)
        self.assertEqual(page.count('name="gender"'), 1)

    def test_grammar_is_independent_and_does_not_modify_other_entities(self):
        connection = self.connect()
        before_occurrences = [tuple(row) for row in connection.execute(
            "SELECT * FROM occurrence ORDER BY occurrence_id"
        )]
        before_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("submission", "assignment", "occurrence_revision")
        }
        connection.close()

        for occurrence_id in (1, 2, 3, 4, 5):
            response = self.client.post(
                f"/ocurrencias/{occurrence_id}/gramatica",
                data={"gender": f"OPEN-{occurrence_id}"},
            )
            self.assertEqual(response.status_code, 302)

        connection = self.connect()
        try:
            after_occurrences = [tuple(row) for row in connection.execute(
                "SELECT * FROM occurrence ORDER BY occurrence_id"
            )]
            after_counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("submission", "assignment", "occurrence_revision")
            }
        finally:
            connection.close()
        self.assertEqual(after_occurrences, before_occurrences)
        self.assertEqual(after_counts, before_counts)

    def test_post_missing_occurrence_is_404(self):
        response = self.client.post(
            "/ocurrencias/999/gramatica", data={"gender": "SIN-MARCA"}
        )
        self.assertEqual(response.status_code, 404)

    def test_result_messages_are_controlled(self):
        saved = self.client.get("/ocurrencias/1/gramatica?result=saved")
        noop = self.client.get("/ocurrencias/1/gramatica?result=noop")
        unknown = self.client.get(
            "/ocurrencias/1/gramatica?result=UNTRUSTED-MESSAGE"
        )
        self.assertIn("Análisis gramatical guardado.", saved.get_data(as_text=True))
        self.assertIn(
            "No hubo cambios en el análisis gramatical.",
            noop.get_data(as_text=True),
        )
        self.assertNotIn("UNTRUSTED-MESSAGE", unknown.get_data(as_text=True))

    def test_listing_has_one_compact_grammar_link_per_occurrence(self):
        connection = self.connect()
        create_or_replace_occurrence_grammar(
            connection, 1, gender="SIN-MARCA"
        )
        connection.close()
        response = self.client.get("/ocurrencias")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertEqual(page.count("/gramatica"), 5)
        self.assertEqual(page.count("Gramática"), 5)
        self.assertEqual(page.count("(registrada)"), 1)
        self.assertEqual(page.count("(sin análisis)"), 4)
        for gloss in ("NO-WORKFLOW", "PENDING", "REJECTED", "ACCEPTED", "ASSIGNED"):
            self.assertEqual(page.count(gloss), 1)


if __name__ == "__main__":
    unittest.main()
