import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from access_control import install_access_context
from activity import InvalidActivity, record_activity
from concept_labels import alternative_display_label
from routes.collaborators import collaborators_bp
from routes.main import main_bp
from routes.sources import sources_bp
from routes.concepts import concepts_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from routes.alternatives import alternatives_bp

ROOT = Path(__file__).resolve().parents[1]


def load_migration():
    path = ROOT / "migrations" / "012_collaboration_activity.py"
    spec = importlib.util.spec_from_file_location("migration_012", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class Migration012Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "post011.db"
        db = sqlite3.connect(self.path); database.crear_esquema(db)
        db.execute("DROP TABLE activity_event"); db.execute("DROP TABLE collaborator")
        db.commit(); db.close()

    def tearDown(self): self.tmp.cleanup()

    def test_post_011_to_012_is_empty_and_idempotent(self):
        migration = load_migration()
        self.assertTrue(migration.migrate(self.path, None))
        self.assertFalse(migration.migrate(self.path, None))
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("SELECT count(*) FROM collaborator").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM activity_event").fetchone()[0], 0)
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO collaborator(display_name) VALUES('  ')")
        with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO collaborator(display_name,active) VALUES('X',2)")
        with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO activity_event(event_type,access_role) VALUES('x','owner')")
        db.close()


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:"); self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON"); database.crear_esquema(self.db)

    def tearDown(self): self.db.close()

    def test_snapshot_anonymous_invalid_and_rename(self):
        collaborator = self.db.execute("INSERT INTO collaborator(display_name) VALUES('Diana')").lastrowid
        record_activity(self.db,"one",collaborator_id=collaborator,access_role="analyst")
        self.db.execute("UPDATE collaborator SET display_name='Diana nueva' WHERE collaborator_id=?",(collaborator,))
        record_activity(self.db,"two",collaborator_id="missing",access_role="master")
        rows = self.db.execute("SELECT collaborator_id,collaborator_name_snapshot,access_role FROM activity_event ORDER BY activity_event_id").fetchall()
        self.assertEqual(tuple(rows[0]), (collaborator,"Diana","analyst"))
        self.assertEqual(tuple(rows[1]), (None,None,"master"))
        with self.assertRaises(InvalidActivity): record_activity(self.db,"bad",access_role="visitor")

    def test_rollback_removes_success_event(self):
        self.db.execute("BEGIN")
        record_activity(self.db,"temporary",access_role="reviewer")
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event").fetchone()[0],0)


class RoleAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)/"roles.db"
        db=sqlite3.connect(self.path); database.crear_esquema(db); db.execute("INSERT INTO collaborator(display_name) VALUES('Diana')"); db.commit(); db.close()
        self.old_path=database.BASE_DATOS; database.BASE_DATOS=self.path
        self.old_env={key:os.environ.get(key) for key in ("LESICO_ANALYST_ROUTE","LESICO_REVIEWER_ROUTE","LESICO_MASTER_ROUTE")}
        os.environ.update(LESICO_ANALYST_ROUTE="test-analyst",LESICO_REVIEWER_ROUTE="test-reviewer",LESICO_MASTER_ROUTE="test-master")
        app=Flask(__name__,template_folder=str(ROOT/"templates")); app.testing=True
        app.jinja_env.filters["alternative_display_label"]=alternative_display_label
        for blueprint in (main_bp,sources_bp,concepts_bp,occurrences_bp,
                          submissions_bp,alternatives_bp,collaborators_bp):
            app.register_blueprint(blueprint)
        install_access_context(app)
        self.client=app.test_client()

    def tearDown(self):
        database.BASE_DATOS=self.old_path
        for key,value in self.old_env.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        self.tmp.cleanup()

    def test_hierarchy_old_and_invalid_routes(self):
        self.assertEqual(self.client.get("/trabajo").status_code,404)
        self.assertEqual(self.client.get("/unknown/trabajo").status_code,404)
        for token in ("test-analyst","test-reviewer","test-master"):
            response=self.client.get(f"/{token}/trabajo")
            self.assertEqual(response.status_code,200)
            html=response.get_data(as_text=True)
            self.assertIn("Trabajando como",html); self.assertIn("Sin identificar",html)
            self.assertIn("Diana",html); self.assertNotIn("Otro",html)
        self.assertEqual(self.client.get("/test-analyst/colaboradores").status_code,404)
        self.assertEqual(self.client.get("/test-reviewer/colaboradores").status_code,404)
        self.assertEqual(self.client.get("/test-master/colaboradores").status_code,200)
        self.assertEqual(self.client.get("/test-analyst/aportes/pendientes").status_code,404)
        self.assertEqual(self.client.get("/test-reviewer/aportes/pendientes").status_code,200)
        self.assertEqual(self.client.get("/test-master/aportes/pendientes").status_code,200)
        self.assertEqual(self.client.get("/test-reviewer/ocurrencias").status_code,200)

    def test_master_create_and_rename_preserve_snapshot(self):
        response=self.client.post("/test-master/colaboradores",data={"display_name":"Julio","collaborator_id":"1","access_role":"analyst"})
        self.assertEqual(response.status_code,302)
        response=self.client.post("/test-master/colaboradores/1/editar",data={"display_name":"Diana nueva","collaborator_id":"2"})
        self.assertEqual(response.status_code,302)
        db=sqlite3.connect(self.path)
        rows=db.execute("SELECT event_type,collaborator_name_snapshot,access_role FROM activity_event ORDER BY activity_event_id").fetchall()
        self.assertEqual(rows,[("collaborator_created","Diana","master"),("collaborator_renamed","Julio","master")])
        db.close()

    def test_analyst_pending_message_has_no_reviewer_action(self):
        db=sqlite3.connect(self.path)
        db.execute("INSERT INTO source(source_name) VALUES('Fuente')");db.execute("INSERT INTO concept(preferred_label) VALUES('COSMOS')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'ESTRELLA')");db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)")
        db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'ALTERNATIVE','pending')")
        db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id) VALUES(1,'NEW',1)");db.commit();db.close()
        analyst=self.client.get("/test-analyst/ocurrencias/1/clasificar").get_data(as_text=True)
        reviewer=self.client.get("/test-reviewer/ocurrencias/1/clasificar").get_data(as_text=True)
        self.assertIn("Ya existe un análisis pendiente de revisión",analyst);self.assertNotIn("Ir a revisión",analyst)
        self.assertIn("Ir a revisión",reviewer)
        self.assertEqual(self.client.post("/test-analyst/aportes/1/decidir",data={"decision":"rejected"}).status_code,404)


if __name__ == "__main__": unittest.main()
