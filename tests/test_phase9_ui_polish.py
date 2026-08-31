import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from concept_labels import alternative_display_label, human_concept_label
from database import crear_esquema
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from source_period import format_source_period

ROOT = Path(__file__).resolve().parents[1]


class Phase9UIPolishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ui.db"
        db = self.connect()
        crear_esquema(db)
        db.executemany(
            "INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES(?,?,?,?)",
            [("Cerrada", 2016, 2016, "known"), ("Rango", 2007, 2009, "known")],
        )
        db.execute("INSERT INTO concept(preferred_label) VALUES('COSMOS')")
        db.execute("INSERT INTO collaborator(display_name) VALUES('Persona')")
        db.commit()
        db.close()
        self.role = "analyst"
        self.app = Flask(__name__, template_folder=str(ROOT / "templates"))
        self.app.testing = True
        self.app.jinja_env.filters.update(
            alternative_display_label=alternative_display_label,
            human_concept_label=human_concept_label,
            source_period=format_source_period,
        )
        self.app.register_blueprint(occurrences_bp)
        self.app.register_blueprint(submissions_bp)

        @self.app.before_request
        def set_role():
            g.current_access_role = self.role

        self.patches = [
            patch("routes.occurrences.conectar", side_effect=self.connect),
            patch("routes.submissions.conectar", side_effect=self.connect),
        ]
        for item in self.patches:
            item.start()
        self.client = self.app.test_client()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def test_year_ui_and_single_primary_registration_action(self):
        self.role = "reviewer"
        html = self.client.get("/aportes/nuevo").get_data(as_text=True)
        self.assertIn('data-min-year="2016" data-max-year="2016"', html)
        self.assertIn('data-min-year="2007" data-max-year="2009"', html)
        self.assertIn("No es necesario registrar un año adicional", html)
        self.assertIn("yearField.hidden=single", html)
        self.assertIn("year.value=''", html)
        self.assertEqual(html.count(">Completar registro</button>"), 1)
        self.assertNotIn(">Aceptar concepto inmediatamente</button>", html)
        self.assertIn('id="concept-immediate-options" hidden', html)
        self.assertIn("selected.value!=='new'", html)

    def test_analyst_new_concept_stays_pending_and_source_year_is_not_copied(self):
        response = self.client.post("/aportes", data={
            "source_id": "1", "original_gloss": "LUCERO",
            "reference_kind": "new", "proposed_label": "ASTRO",
            "concept_immediate_action": "new",
        })
        self.assertEqual(response.status_code, 302)
        db = self.connect()
        occurrence = db.execute(
            "SELECT occurrence_year FROM occurrence WHERE original_gloss='LUCERO'"
        ).fetchone()
        proposal = db.execute(
            "SELECT status FROM concept_proposal WHERE proposed_label='ASTRO'"
        ).fetchone()
        db.close()
        self.assertIsNone(occurrence[0])
        self.assertEqual(proposal[0], "pending")

    def test_immediate_preview_uses_natural_language_and_explicit_none(self):
        self.role = "reviewer"
        response = self.client.post("/aportes", data={
            "source_id": "2", "original_gloss": "NOVA", "occurrence_year": "2008",
            "reference_kind": "new", "proposed_label": "NUEVO-CONCEPTO",
            "concept_immediate_action": "new", "collaborator_id": "1",
        })
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Se registrará este aporte en el historial", html)
        self.assertIn("Conflictos previsibles", html)
        self.assertIn("Ninguno.", html)
        self.assertNotIn("submission o proposal histórica", html)
        db = self.connect()
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence").fetchone()[0], 0)
        db.close()

    def test_human_grammar_versions_and_aportes_context_labels(self):
        grammar = (ROOT / "templates" / "gramatica_ocurrencia.html").read_text(encoding="utf-8")
        detail = (ROOT / "templates" / "revision_aportes.html").read_text(encoding="utf-8")
        listing = (ROOT / "templates" / "aportes.html").read_text(encoding="utf-8")
        self.assertIn("Versión vigente", grammar)
        self.assertIn("history|length - loop.index0", grammar)
        self.assertNotIn("occurrence_grammar_id", grammar)
        for label in (
            "Contexto al crear el aporte", "Propuesta del analista",
            "Resolución final", "Clasificación vigente actual",
        ):
            self.assertIn(label, detail)
        for heading in ("Concepto", "Alternativa", "Resolución"):
            self.assertIn(f"<th>{heading}</th>", listing)


if __name__ == "__main__":
    unittest.main()
