from tests.form_client import FormClient
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask,g
from database import crear_esquema
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from concept_labels import alternative_display_label,human_concept_label
from source_period import format_source_period

ROOT=Path(__file__).resolve().parents[1]

class ImmediateAcceptanceRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/"immediate.db"
        db=self.connect();crear_esquema(db);db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',2000,2005,'known')");db.execute("INSERT INTO concept(preferred_label) VALUES('C')");db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'TEST',2001)");db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)");db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')");db.execute("INSERT INTO collaborator(display_name) VALUES('Persona')");db.commit();db.close()
        self.app=Flask(__name__,template_folder=str(ROOT/"templates"));self.app.testing=True;self.app.jinja_env.filters.update(alternative_display_label=alternative_display_label,human_concept_label=human_concept_label,source_period=format_source_period);self.app.register_blueprint(occurrences_bp);self.app.register_blueprint(submissions_bp)
        @self.app.before_request
        def role():g.current_access_role=self.role
        self.client=FormClient(self.app.test_client());self.patches=[patch("routes.occurrences.conectar",side_effect=self.connect),patch("routes.submissions.conectar",side_effect=self.connect)]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in self.patches:item.stop()
        self.temp.cleanup()
    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db

    def test_roles_buttons_and_crafted_post(self):
        self.role="analyst";html=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True);self.assertIn("Mandar a revisión",html);self.assertNotIn("Aceptar inmediatamente",html)
        self.assertEqual(404,self.client.post("/ocurrencias/1/gramatica/aceptacion-inmediata/preview",data={"gender":"FEM-A","access_role":"master"}).status_code)
        for role in ("reviewer","master"):
            self.role=role;html=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True);self.assertIn("Aceptar inmediatamente",html);self.assertLess(html.index("Mandar a revisión"),html.index("Aceptar inmediatamente"))

    def test_grammar_preview_abandon_and_confirm_prg(self):
        self.role="reviewer";data={"gender":"FEM-A","gender_uncertain":"on","collaborator_id":"1"}
        preview=self.client.post("/ocurrencias/1/gramatica/aceptacion-inmediata/preview",data=data);self.assertEqual(200,preview.status_code);self.assertIn("Aceptar inmediatamente este análisis",preview.get_data(as_text=True))
        db=self.connect();self.assertEqual((0,0,0),tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("submission","occurrence_grammar","activity_event")));db.close()
        confirmed=dict(data,confirm_immediate="yes");response=self.client.post("/ocurrencias/1/gramatica/aceptacion-inmediata/confirmar",data=confirmed);self.assertEqual(302,response.status_code)
        db=self.connect();self.assertEqual(("resolved","accepted"),tuple(db.execute("SELECT status,resolution FROM submission").fetchone()));self.assertEqual(1,db.execute("SELECT created_from_submission_id FROM occurrence_grammar").fetchone()[0]);self.assertEqual((1,"Persona","reviewer"),tuple(db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM activity_event WHERE event_type='grammar_submission_accepted'").fetchone()));db.close()

    def test_existing_alternative_and_concept_immediate(self):
        self.role="master";data={"proposal_kind":"EXISTING","proposed_existing_alternative_id":"1","canonical_decision":"existing","canonical_alternative_id":"1","collaborator_id":"1"}
        self.assertEqual(200,self.client.post("/ocurrencias/1/clasificar/aceptacion-inmediata/preview",data=data).status_code)
        db=self.connect();self.assertEqual(0,db.execute("SELECT count(*) FROM submission").fetchone()[0]);db.close()
        self.assertEqual(302,self.client.post("/ocurrencias/1/clasificar/aceptacion-inmediata/confirmar",data=dict(data,confirm_immediate="yes")).status_code)
        concept={"source_id":"1","original_gloss":"NUEVO","occurrence_year":"2002","reference_kind":"new","proposed_label":"CONCEPTO-NUEVO","concept_immediate_action":"new","collaborator_id":"1"}
        self.assertEqual(200,self.client.post("/aportes/concepto/aceptacion-inmediata/preview",data=concept).status_code)
        response=self.client.post("/aportes/concepto/aceptacion-inmediata/confirmar",data=dict(concept,confirm_immediate="yes"));self.assertEqual(302,response.status_code)
        db=self.connect();self.assertEqual(("resolved","CONCEPTO-NUEVO"),tuple(db.execute("SELECT cp.status,c.preferred_label FROM concept_proposal cp JOIN concept c ON c.concept_id=cp.resolved_concept_id").fetchone()));self.assertEqual(0,db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=2").fetchone()[0]);db.close()
