import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from access_control import install_access_context
from catalog_projection import build_catalog_projection
from concept_labels import alternative_display_label, human_concept_label
from database import crear_esquema
from routes.catalog import catalog_bp
from source_period import format_source_period

ROOT = Path(__file__).resolve().parents[1]


class InternalCatalogRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.db"
        db = self.connect()
        crear_esquema(db)
        db.execute("INSERT INTO source(source_name) VALUES('Fuente')")
        db.execute("INSERT INTO concept(preferred_label) VALUES('COSMOS')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'ESTRELLA')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        db.execute("INSERT INTO collaborator(display_name) VALUES('Persona')")
        db.commit()
        db.close()

        self.app = Flask(__name__, template_folder=str(ROOT / "templates"))
        self.app.testing = True
        self.app.jinja_env.filters.update(
            alternative_display_label=alternative_display_label,
            human_concept_label=human_concept_label,
            source_period=format_source_period,
        )
        self.app.register_blueprint(catalog_bp)
        self.app.add_url_rule(
            "/conflictos", endpoint="conflicts.conflicts_list",
            view_func=lambda: "conflictos",
        )
        install_access_context(self.app)
        self.app.wsgi_app.routes = {
            "ana": "analyst", "rev": "reviewer", "mas": "master",
        }
        self.patches = [
            patch("routes.catalog.conectar", side_effect=self.connect),
            patch("access_control.conectar", side_effect=self.connect),
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

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_all_internal_roles_search_and_navigation(self):
        for prefix in ("ana", "rev", "mas"):
            response = self.client.get(f"/{prefix}/catalogo-interno?q=estrella")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("COSMOS", html)
            self.assertIn("Versión de trabajo · No publicada", html)
            self.assertEqual(
                self.client.get(f"/{prefix}/catalogo-interno/conceptos/1").status_code,
                200,
            )
            self.assertEqual(
                self.client.get(f"/{prefix}/catalogo-interno/alternativas/1").status_code,
                200,
            )
        self.assertIn(
            "No hay resultados",
            self.client.get("/ana/catalogo-interno?q=inexistente").get_data(as_text=True),
        )

    def test_no_or_invalid_token_is_hidden_and_get_does_not_write(self):
        before = self.digest()
        self.assertEqual(self.client.get("/catalogo-interno").status_code, 404)
        self.assertEqual(self.client.get("/incorrecto/catalogo-interno").status_code, 404)
        self.assertEqual(self.client.get("/ana/catalogo-interno").status_code, 200)
        self.assertEqual(self.digest(), before)

    def test_conflict_banner_is_outside_projection_and_role_aware(self):
        db = self.connect()
        db.execute("""
            INSERT INTO conflict(origin_kind,rule_code,severity,description,
              subject_signature,detection_source)
            VALUES('automatic','TEST','blocking','Bloqueo','x','workflow')
        """)
        db.commit()
        projection = build_catalog_projection(db)
        db.close()
        self.assertNotIn("conflict", str(projection).casefold())
        analyst = self.client.get("/ana/catalogo-interno").get_data(as_text=True)
        reviewer = self.client.get("/rev/catalogo-interno").get_data(as_text=True)
        self.assertIn("impedirían publicar", analyst)
        self.assertNotIn("Ver conflictos", analyst)
        self.assertIn("Ver conflictos", reviewer)

    def test_area_filter_is_prepared_from_projected_concept_metadata(self):
        db=self.connect();db.execute("UPDATE concept SET knowledge_area_1='Astronomía',knowledge_area_2='Lingüística'");db.commit();db.close()
        html=self.client.get("/ana/catalogo-interno/conceptos/1").get_data(as_text=True)
        self.assertIn("Área de conocimiento",html);self.assertIn('data-knowledge-areas="Astronomía||Lingüística"',html)
        self.assertIn('id="filtro-area"',html);self.assertIn("dataset.knowledgeAreas",html if "dataset.knowledgeAreas" in html else (ROOT/"static/catalogo/catalogo.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
