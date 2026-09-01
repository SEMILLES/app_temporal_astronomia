import hashlib, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from access_control import install_access_context
from catalog_publication import publish_catalog
from database import crear_esquema
from routes.catalog import catalog_bp

ROOT = Path(__file__).resolve().parents[1]


class LegacyCatalogUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "ui.db"
        db = self.connect(); crear_esquema(db)
        db.execute("INSERT INTO source(source_name,start_year,end_year) VALUES('Fuente humana',2007,2009)")
        db.execute("INSERT INTO concept(preferred_label) VALUES(?)", ('ASTRO<script>alert("x")</script>',))
        db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)", (("1a",),("1b",)))
        db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(1,'SECRETO-RETIRADO',CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO occurrence(source_id,original_gloss,source_locator,provenance_note,hyperlink) VALUES(1,'LUNA','p. 4','Nota <b>no HTML</b>','https://example.test/recurso')")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        db.execute("INSERT INTO occurrence_grammar(occurrence_id,gender,plural,plural_uncertain,grammar_note) VALUES(1,'femenino',NULL,0,'Gramática <script>privada</script>')")
        db.execute("INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation,note) VALUES(1,1,'N/A','Morfología visible')")
        db.execute("INSERT INTO alternative_component(alternative_morphology_id,position,component_label) VALUES(1,1,'raíz')")
        db.executemany("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,?)", (("CM_M1",),("LOC_1",)))
        db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'GRAMMAR','pending')")
        db.execute("INSERT INTO grammar_submission(submission_id,note) VALUES(1,'SECRETO-WORKFLOW')")
        db.commit(); db.close()
        app = Flask(__name__, template_folder=str(ROOT/"templates"), static_folder=str(ROOT/"static")); app.testing=True
        app.register_blueprint(catalog_bp); install_access_context(app); app.wsgi_app.routes={"ana":"analyst","rev":"reviewer","mas":"master"}
        app.add_url_rule("/conflictos",endpoint="conflicts.conflicts_list",view_func=lambda:"conflictos")
        self.patches=[patch("routes.catalog.conectar",side_effect=self.connect),patch("access_control.conectar",side_effect=self.connect)]
        for item in self.patches: item.start()
        self.client=app.test_client()
    def tearDown(self):
        for item in self.patches:item.stop()
        self.temp.cleanup()
    def connect(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
    def digest(self): return hashlib.sha256(self.path.read_bytes()).hexdigest()
    def test_shared_identity_search_security_roles_and_read_only(self):
        before=self.digest()
        for role in ("ana","rev","mas"):
            response=self.client.get(f"/{role}/catalogo-interno"); self.assertEqual(response.status_code,200)
            html=response.get_data(as_text=True); self.assertIn("Vocabulario académico en LSC",html); self.assertIn("Universidad Nacional de Colombia",html); self.assertIn("Versión de trabajo · No publicada",html)
            self.assertIn("1 conceptos · 2 alternativas · 1 ocurrencias",html)
            self.assertIn("CATÁLOGO INTERNO",html); self.assertNotIn("LeSiCo: base de datos léxica de la LSC",html)
            self.assertNotIn("v0",html); self.assertIn("LUNA",html); self.assertIn("ASTRO&lt;script&gt;",html)
            self.assertNotIn("SECRETO-RETIRADO",html); self.assertNotIn("SECRETO-WORKFLOW",html)
        self.assertEqual(self.client.get("/catalogo-interno").status_code,404)
        static_response=self.client.get("/static/catalogo/catalogo.css"); self.assertEqual(static_response.status_code,200); static_response.close()
        self.assertEqual(self.digest(),before)
    def test_detail_grammar_morphology_relations_links_and_external_snapshot(self):
        internal=self.client.get("/ana/catalogo-interno/alternativas/1").get_data(as_text=True)
        for text in ("ASTRO", "1a", "LUNA", "Fuente humana", "2007–2009", "Abrir recurso externo", "femenino", "Sin analizar", "Morfología visible", "CM_M1", "LOC_1"):
            self.assertIn(text,internal)
        self.assertNotIn("<script>alert",internal); self.assertNotIn("<iframe",internal)
        db=self.connect(); publication=publish_catalog(db,publication_comment="UI temporal",actor_context={"access_role":"master"}); db.execute("UPDATE occurrence SET original_gloss='CAMBIO VIVO'"); db.commit(); db.close()
        external=self.client.get("/catalogo/alternativas/1").get_data(as_text=True)
        self.assertIn(f"VERSIÓN v{publication['version_number']}",external); self.assertIn("LUNA",external); self.assertNotIn("CAMBIO VIVO",external)
        self.assertIn("LUNA",self.client.get("/catalogo/v1/alternativas/1").get_data(as_text=True))
    def test_provenance_document_records_exact_source(self):
        text=(ROOT/"docs/legacy_catalog_ui.md").read_text(encoding="utf-8")
        self.assertIn("ab5a06d5f3a9cf2e2da82dc664571f7a46996bcd",text)
        self.assertIn("catalogo.json",text); self.assertIn("nunca se copia ni se consulta",text)

if __name__ == "__main__": unittest.main()
