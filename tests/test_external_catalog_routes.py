import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from access_control import install_access_context
from catalog_publication import publish_catalog
from conflict_presentation import local_timestamp
from database import crear_esquema
from routes.catalog import catalog_bp

ROOT=Path(__file__).resolve().parents[1]
class ExternalCatalogRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"db.sqlite"; db=self.connect(); crear_esquema(db); db.commit(); db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates")); app.testing=True; app.jinja_env.filters["local_timestamp"]=local_timestamp; app.register_blueprint(catalog_bp); install_access_context(app); app.wsgi_app.routes={"mas":"master","rev":"reviewer","ana":"analyst"}; self.client=app.test_client()
        self.patches=[patch("routes.catalog.conectar",side_effect=self.connect),patch("access_control.conectar",side_effect=self.connect)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def connect(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
    def publish(self,comment):
        db=self.connect(); row=publish_catalog(db,publication_comment=comment,actor_context={"access_role":"master"}); db.close(); return row
    def test_public_empty_latest_historical_and_live_independence(self):
        self.assertIn("Aún no hay",self.client.get("/catalogo").get_data(as_text=True))
        self.publish("v1 vacía")
        db=self.connect(); db.execute("INSERT INTO concept(preferred_label) VALUES('VIVO')"); db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')"); db.commit(); db.close()
        self.assertNotIn("VIVO",self.client.get("/catalogo").get_data(as_text=True))
        self.publish("v2")
        self.assertIn("VIVO",self.client.get("/catalogo").get_data(as_text=True))
        old=self.client.get("/catalogo/v1").get_data(as_text=True); self.assertIn("versión histórica: v1",old); self.assertNotIn("VIVO",old)
        self.assertEqual(self.client.get("/catalogo/v99").status_code,404)
    def test_master_admin_only_and_crafted_role_ignored(self):
        self.assertEqual(self.client.get("/mas/actualizar-catalogo").status_code,200)
        self.assertEqual(self.client.get("/rev/actualizar-catalogo").status_code,404)
        response=self.client.post("/ana/actualizar-catalogo",data={"access_role":"master","publication_comment":"x"}); self.assertEqual(response.status_code,404)
if __name__=="__main__": unittest.main()
