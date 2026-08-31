import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from occurrence_registration import RegistrationError, complete_registration, save_draft
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp

ROOT = Path(__file__).resolve().parents[1]


class OccurrenceRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.previous = database.BASE_DATOS
        database.BASE_DATOS = self.path
        db = self.connect()
        database.crear_esquema(db)
        db.execute("INSERT INTO source(source_name,start_year) VALUES('Synthetic',1999)")
        db.execute("INSERT INTO concept(preferred_label) VALUES('ASTRONOMIA')")
        db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('LUZ','pending')")
        db.commit(); db.close()
        app = Flask(__name__, template_folder=str(ROOT / "templates"))
        app.jinja_env.filters.update(human_concept_label=human_concept_label, alternative_display_label=alternative_display_label)
        app.register_blueprint(occurrences_bp); app.register_blueprint(submissions_bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        database.BASE_DATOS = self.previous
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def counts(self, db):
        return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "occurrence", "occurrence_concept_reference", "assignment",
            "occurrence_grammar", "submission",
        )}

    def test_incomplete_draft_is_not_occurrence_and_can_be_deleted(self):
        db = self.connect()
        draft_id = save_draft(db, original_gloss="INCOMPLETE")
        self.assertEqual(self.counts(db)["occurrence"], 0)
        db.close()
        page = self.client.get("/borradores").get_data(as_text=True)
        self.assertIn("INCOMPLETE", page)
        self.assertEqual(self.client.post(f"/borradores/{draft_id}/eliminar").status_code, 302)
        db = self.connect(); self.assertEqual(db.execute("SELECT count(*) FROM occurrence_draft").fetchone()[0], 0); db.close()

    def test_complete_draft_creates_occurrence_reference_and_deletes_draft_only(self):
        db = self.connect()
        draft_id = save_draft(db, source_id=1, original_gloss="DRAFT", reference_concept_id=1)
        occurrence_id = complete_registration(db, draft_id=draft_id)
        # Los identificadores pertenecen a secuencias distintas; el draft_id
        # nunca se inserta explícitamente como occurrence_id.
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence_draft").fetchone()[0], 0)
        ref = db.execute("SELECT concept_id,concept_proposal_id,is_current FROM occurrence_concept_reference").fetchone()
        self.assertEqual(tuple(ref), (1, None, 1))
        counts = self.counts(db)
        self.assertEqual((counts["submission"], counts["assignment"], counts["occurrence_grammar"]), (0, 0, 0))
        db.close()

    def test_existing_and_pending_proposal_references(self):
        db = self.connect()
        first = complete_registration(db, source_id=1, original_gloss="EXISTING", concept_id=1)
        second = complete_registration(db, source_id=1, original_gloss="PENDING", concept_proposal_id=1)
        rows = db.execute("SELECT occurrence_id,concept_id,concept_proposal_id FROM occurrence_concept_reference ORDER BY occurrence_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(first, 1, None), (second, None, 1)])
        db.close()

    def test_new_proposal_and_normalized_pending_reuse(self):
        db = self.connect()
        one = complete_registration(db, source_id=1, original_gloss="ONE", proposed_label="cielo azul")
        two = complete_registration(db, source_id=1, original_gloss="TWO", proposed_label="CIELO-AZUL")
        proposals = db.execute("SELECT concept_proposal_id,proposed_label FROM concept_proposal WHERE proposed_label='CIELO-AZUL'").fetchall()
        self.assertEqual(len(proposals), 1)
        refs = db.execute("SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id IN (?,?)", (one,two)).fetchall()
        self.assertEqual(refs[0][0], refs[1][0])
        db.close()

    def test_new_label_matching_concept_uses_existing(self):
        db = self.connect()
        oid = complete_registration(db, source_id=1, original_gloss="MATCH", proposed_label="astronomia")
        ref = db.execute("SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=?", (oid,)).fetchone()
        self.assertEqual(tuple(ref), (1, None)); db.close()

    def test_completion_validation_rolls_back_and_preserves_draft(self):
        db = self.connect()
        draft_id = save_draft(db, original_gloss="KEEP-ME")
        with self.assertRaises(RegistrationError):
            complete_registration(db, draft_id=draft_id)
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT original_gloss FROM occurrence_draft WHERE draft_id=?", (draft_id,)).fetchone()[0], "KEEP-ME")
        db.close()

    def test_failure_after_occurrence_insert_rolls_back_and_preserves_draft(self):
        db = self.connect()
        draft_id = save_draft(db, source_id=1, original_gloss="ROLLBACK", reference_concept_id=1)
        db.execute("CREATE TRIGGER fail_reference BEFORE INSERT ON occurrence_concept_reference BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            complete_registration(db, draft_id=draft_id)
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence_draft").fetchone()[0], 1)
        db.close()

    def test_route_registration_has_order_and_creates_no_submission(self):
        page = self.client.get("/aportes/nuevo").get_data(as_text=True)
        self.assertLess(page.index("Fuente"), page.index("Evidencia/glosa"))
        self.assertLess(page.index("Evidencia/glosa"), page.index("Año de la ocurrencia"))
        self.assertLess(page.index("Año de la ocurrencia"), page.index("Concepto de referencia"))
        response = self.client.post("/aportes", data=dict(source_id="1", original_gloss="ROUTE", occurrence_year="2024", reference_kind="concept", reference_concept_id="1"))
        self.assertEqual(response.status_code, 302)
        db = self.connect(); counts = self.counts(db); db.close()
        self.assertEqual((counts["occurrence"], counts["occurrence_concept_reference"], counts["submission"]), (1,1,0))

    def test_listing_treats_missing_analysis_as_normal(self):
        db = self.connect(); complete_registration(db, source_id=1, original_gloss="UNANALYZED", concept_id=1); db.close()
        page = self.client.get("/ocurrencias").get_data(as_text=True)
        self.assertIn("Sin analizar", page)


if __name__ == "__main__":
    unittest.main()
