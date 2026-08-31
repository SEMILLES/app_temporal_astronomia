import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask, g
from database import crear_esquema
from routes.conflicts import conflicts_bp
from access_control import install_access_context
from conflict_rules import ConflictSubject
from conflicts import create_manual_conflict

class ConflictRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=__import__('pathlib').Path(self.temp.name)/"routes.db"
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;crear_esquema(db)
        db.execute("INSERT INTO source(source_name) VALUES('S')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'TEST')")
        db.execute("INSERT INTO collaborator(display_name) VALUES('Pepito Perez')")
        db.commit();db.close()
        self.app=Flask(__name__,template_folder=str(__import__('pathlib').Path(__file__).resolve().parents[1]/'templates'));self.app.register_blueprint(conflicts_bp)
        @self.app.before_request
        def role():g.current_access_role=self.role
    def tearDown(self):self.temp.cleanup()
    def connection(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db
    def test_reviewer_and_master_access_analyst_404_and_get_no_side_effects(self):
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            for role,expected in (("analyst",404),("reviewer",200),("master",200)):
                self.role=role;response=self.app.test_client().get("/conflictos");self.assertEqual(expected,response.status_code)
            db=self.connection();self.assertEqual(0,db.execute("SELECT count(*) FROM conflict").fetchone()[0]);db.close()

    def create_conflict(self,role="master"):
        db=self.connection();cid=create_manual_conflict(db,description="Revisar",subjects=[ConflictSubject("occurrence",1,"subject")],severity="non_blocking",justification="No bloquea",resolution_criteria="Confirmar",actor_context={"collaborator_id":1,"access_role":role});db.commit();db.close();return cid

    def test_resolve_prg_message_without_secret_key_and_actor_snapshot(self):
        cid=self.create_conflict();self.role="master";self.assertIsNone(self.app.secret_key)
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            response=self.app.test_client().post(f"/conflictos/{cid}/resolver",data={"comment":"Verificado","manual_confirmed":"yes","collaborator_id":"1"})
            self.assertEqual(302,response.status_code);self.assertIn("message=",response.headers["Location"])
            page=self.app.test_client().get(response.headers["Location"]);self.assertEqual(200,page.status_code);self.assertIn("Conflicto resuelto",page.get_data(as_text=True))
        db=self.connection();attempt=db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM conflict_resolution_attempt WHERE conflict_id=?",(cid,)).fetchone();event=db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM activity_event WHERE event_type='conflict_resolved' AND entity_id=?",(cid,)).fetchone();db.close()
        self.assertEqual((1,"Pepito Perez","master"),tuple(attempt));self.assertEqual(tuple(attempt),tuple(event))

    def test_reviewer_anonymous_and_invalid_collaborator_attribution(self):
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            for role,value,expected in (("reviewer","1",(1,"Pepito Perez","reviewer")),("master","",(None,None,"master")),("reviewer","999",(None,None,"reviewer"))):
                cid=self.create_conflict(role);self.role=role
                response=self.app.test_client().post(f"/conflictos/{cid}/resolver",data={"comment":"OK","manual_confirmed":"yes","collaborator_id":value})
                self.assertEqual(302,response.status_code)
                db=self.connection();row=db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM conflict_resolution_attempt WHERE conflict_id=?",(cid,)).fetchone();db.close();self.assertEqual(expected,tuple(row))

    def test_manual_form_is_human_readable_and_create_uses_prg(self):
        self.role="reviewer"
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            page=self.app.test_client().get("/conflictos/nuevo");html=page.get_data(as_text=True)
            self.assertIn("Elementos afectados",html);self.assertIn("Ocurrencia 1 — TEST",html);self.assertNotIn("tipo:id:rol",html);self.assertIn("class=\"field\"",html)
            response=self.app.test_client().post("/conflictos/nuevo",data={"description":"Caso manual","subject_type":"occurrence","subject_id":"1","severity":"blocking","justification":"Puede publicar mal","resolution_criteria":"Corregir","collaborator_id":"1"})
            self.assertEqual(302,response.status_code)
        db=self.connection();self.assertEqual(("occurrence",1,"subject"),tuple(db.execute("SELECT subject_type,subject_id,subject_role FROM conflict_subject ORDER BY conflict_subject_id DESC LIMIT 1").fetchone()));self.assertEqual((1,"Pepito Perez","reviewer"),tuple(db.execute("SELECT created_by_collaborator_id,created_by_name_snapshot,created_access_role FROM conflict ORDER BY conflict_id DESC LIMIT 1").fetchone()));db.close()

    def test_global_validation_ui_uses_selected_actor(self):
        db=self.connection();db.execute("INSERT INTO concept(preferred_label) VALUES('C')");db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,NULL)");db.commit();db.close();self.role="master"
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            response=self.app.test_client().post("/conflictos/validar",data={"collaborator_id":"1"});self.assertEqual(302,response.status_code)
        db=self.connection();conflict=db.execute("SELECT created_by_collaborator_id,created_by_name_snapshot,created_access_role FROM conflict WHERE rule_code='MISSING_WORKING_LABEL_ACTIVE_ALTERNATIVE'").fetchone();activity=db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM activity_event WHERE event_type='conflict_created'").fetchone();db.close();self.assertEqual((1,"Pepito Perez","master"),tuple(conflict));self.assertEqual(tuple(conflict),tuple(activity))

    def test_toolbar_binds_post_forms_after_dom_is_loaded(self):
        app=Flask("toolbar")
        @app.route("/form")
        def form():return "<body><form method=\"post\"></form></body>"
        install_access_context(app)
        app.wsgi_app.routes={"rev":"reviewer"}
        with patch("access_control.conectar",side_effect=self.connection):
            response=app.test_client().get("/rev/form")
        self.assertIn("DOMContentLoaded",response.get_data(as_text=True))
