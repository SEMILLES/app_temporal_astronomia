import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from routes.main import main_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp


ROOT = Path(__file__).resolve().parents[1]


class OccurrenceSubmissionDecouplingTests(unittest.TestCase):

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
        connection.execute(
            "INSERT INTO concept (preferred_label) VALUES ('ASTRONOMIA')"
        )
        connection.executemany(
            "INSERT INTO alternative (concept_id, working_label) VALUES (1, ?)",
            (("1a",), ("1b",)),
        )
        for gloss in (
            "NO-SUBMISSION", "PENDING-SUBMISSION", "REJECTED-SUBMISSION",
            "ACCEPTED-SUBMISSION", "ASSIGNED-DIRECT", "HISTORY-DIRECT",
        ):
            connection.execute(
                "INSERT INTO occurrence (source_id, original_gloss) VALUES (1, ?)",
                (gloss,),
            )
        connection.execute(
            "UPDATE occurrence SET source_locator = 'DIRECT-LOCATOR' "
            "WHERE occurrence_id = 5"
        )
        connection.execute(
            "UPDATE occurrence SET source_locator = 'HISTORY-LOCATOR' "
            "WHERE occurrence_id = 6"
        )
        for occurrence_id, status in ((2, "pending"), (3, "rejected"), (4, "accepted")):
            connection.execute(
                "INSERT INTO submission (occurrence_id, proposal_type, status) "
                "VALUES (?, 'not_sure', ?)",
                (occurrence_id, status),
            )
        connection.execute(
            "INSERT INTO assignment (occurrence_id, alternative_id) VALUES (5, 1)"
        )
        historical_id = connection.execute(
            "INSERT INTO assignment (occurrence_id, alternative_id, is_current) "
            "VALUES (6, 1, 0)"
        ).lastrowid
        connection.execute(
            "INSERT INTO assignment (occurrence_id, alternative_id, "
            "supersedes_assignment_id) VALUES (6, 2, ?)",
            (historical_id,),
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
        return connection

    def test_listing_includes_every_workflow_state_and_current_classification(self):
        response = self.client.get("/ocurrencias")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        for gloss in (
            "NO-SUBMISSION", "PENDING-SUBMISSION", "REJECTED-SUBMISSION",
            "ACCEPTED-SUBMISSION", "ASSIGNED-DIRECT", "HISTORY-DIRECT",
        ):
            self.assertIn(gloss, page)
        for workflow_label in ("Sin aporte", "Pendiente", "Rechazado", "Aceptado"):
            self.assertIn(workflow_label, page)
        self.assertIn("Sin clasificación", page)
        self.assertIn("ASTRONOMIA-1a", page)
        self.assertIn("ASTRONOMIA-1b", page)

        history_position = page.index("HISTORY-DIRECT")
        history_row = page[page.rfind("<tr>", 0, history_position):page.find("</tr>", history_position)]
        self.assertIn("ASTRONOMIA-1b", history_row)
        self.assertNotIn("ASTRONOMIA-1a", history_row)

    def test_edit_get_and_post_work_without_submission_and_remain_listed(self):
        self.assertEqual(self.client.get("/ocurrencias/1/editar").status_code, 200)
        response = self.client.post(
            "/ocurrencias/1/actualizar",
            data={
                "source_id": "1",
                "original_gloss": "NO-SUBMISSION-EDITED",
                "hyperlink": "",
                "source_locator": "direct locator",
                "provenance_note": "",
                "occurrence_year": "",
                "change_note": "Synthetic edit",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("NO-SUBMISSION-EDITED", response.get_data(as_text=True))

    def test_classification_get_ignores_submission_state(self):
        for occurrence_id in (1, 2, 3, 4):
            with self.subTest(occurrence_id=occurrence_id):
                response = self.client.get(f"/ocurrencias/{occurrence_id}/clasificar")
                self.assertEqual(response.status_code, 200)

    def test_classification_post_assigns_existing_alternative_without_acceptance(self):
        for occurrence_id in (1, 2):
            with self.subTest(occurrence_id=occurrence_id):
                response = self.client.post(
                    f"/ocurrencias/{occurrence_id}/clasificar",
                    data={"alternative_id": "1"},
                )
                self.assertEqual(response.status_code, 302)
        connection = self.connect()
        try:
            assigned = connection.execute(
                "SELECT occurrence_id, alternative_id FROM assignment "
                "WHERE occurrence_id IN (1, 2) AND is_current = 1 "
                "ORDER BY occurrence_id"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([tuple(row) for row in assigned], [(1, 1), (2, 1)])

    def test_classification_post_returns_404_for_missing_occurrence(self):
        response = self.client.post(
            "/ocurrencias/999/clasificar", data={"alternative_id": "1"}
        )
        self.assertEqual(response.status_code, 404)

    def test_replacing_assignment_preserves_history_and_one_current(self):
        response = self.client.post(
            "/ocurrencias/5/clasificar", data={"alternative_id": "2"}
        )
        self.assertEqual(response.status_code, 302)
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT alternative_id, is_current, supersedes_assignment_id "
                "FROM assignment WHERE occurrence_id = 5 ORDER BY assignment_id"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["is_current"] for row in rows), 1)
        self.assertEqual(rows[0]["is_current"], 0)
        self.assertEqual(rows[1]["alternative_id"], 2)
        self.assertIsNotNone(rows[1]["supersedes_assignment_id"])

    def test_new_submission_form_uses_current_assignments_without_submission(self):
        response = self.client.get("/aportes/nuevo")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("DIRECT-LOCATOR", page)
        self.assertIn("HISTORY-LOCATOR", page)

        connection = self.connect()
        try:
            connection.execute(
                "UPDATE assignment SET is_current = 0 WHERE occurrence_id = 5"
            )
            connection.commit()
        finally:
            connection.close()
        page = self.client.get("/aportes/nuevo").get_data(as_text=True)
        self.assertNotIn("DIRECT-LOCATOR", page)

    def test_guardar_aporte_still_creates_occurrence_and_pending_submission(self):
        connection = self.connect()
        try:
            before_occurrences = connection.execute(
                "SELECT COUNT(*) FROM occurrence"
            ).fetchone()[0]
            before_submissions = connection.execute(
                "SELECT COUNT(*) FROM submission"
            ).fetchone()[0]
        finally:
            connection.close()

        response = self.client.post(
            "/aportes",
            data={
                "source_id": "1",
                "concept_choice": "not_sure",
                "concept_uncertainty_note": "Synthetic uncertainty",
                "proposal_type": "not_sure",
                "original_gloss": "PUBLIC-WORKFLOW",
                "hyperlink": "",
                "occurrence_year": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        connection = self.connect()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM occurrence").fetchone()[0],
                before_occurrences + 1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM submission").fetchone()[0],
                before_submissions + 1,
            )
            created = connection.execute(
                "SELECT s.status, o.original_gloss FROM submission AS s "
                "JOIN occurrence AS o ON o.occurrence_id = s.occurrence_id "
                "WHERE o.original_gloss = 'PUBLIC-WORKFLOW'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(tuple(created), ("pending", "PUBLIC-WORKFLOW"))

    def test_accept_unclassified_does_not_assign_and_reject_does_not_delete(self):
        response = self.client.post(
            "/aportes/1/decidir", data={"decision": "accept_unclassified"}
        )
        self.assertEqual(response.status_code, 302)
        connection = self.connect()
        try:
            connection.execute(
                "UPDATE submission SET status = 'pending' WHERE occurrence_id = 3"
            )
            connection.commit()
        finally:
            connection.close()
        response = self.client.post(
            "/aportes/2/decidir", data={"decision": "reject"}
        )
        self.assertEqual(response.status_code, 302)

        connection = self.connect()
        try:
            accepted_status = connection.execute(
                "SELECT status FROM submission WHERE occurrence_id = 2"
            ).fetchone()[0]
            rejected_status = connection.execute(
                "SELECT status FROM submission WHERE occurrence_id = 3"
            ).fetchone()[0]
            assignments = connection.execute(
                "SELECT COUNT(*) FROM assignment WHERE occurrence_id = 2"
            ).fetchone()[0]
            rejected_occurrence = connection.execute(
                "SELECT COUNT(*) FROM occurrence WHERE occurrence_id = 3"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(accepted_status, "accepted")
        self.assertEqual(assignments, 0)
        self.assertEqual(rejected_status, "rejected")
        self.assertEqual(rejected_occurrence, 1)


if __name__ == "__main__":
    unittest.main()
