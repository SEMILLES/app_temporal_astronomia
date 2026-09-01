import json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from access_control import install_access_context
from catalog_diff import build_catalog_diff
from catalog_presentation import relation_edges, variation_groups
from catalog_projection import build_catalog_projection
from catalog_publication import publish_catalog, verify_publication_hash
from database import crear_esquema
from routes.catalog import catalog_bp

ROOT=Path(__file__).resolve().parents[1]


class CatalogMediaNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"media.db"; db=self.connect(); crear_esquema(db)
        db.execute("INSERT INTO source(source_name) VALUES('Fuente')")
        db.executemany("INSERT INTO concept(preferred_label) VALUES(?)",(("UNO",),("DOS",)))
        db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(?,?)",((1,"1a"),(1,"1b"),(1,"1c"),(1,"2a"),(2,"1a")))
        db.execute("INSERT INTO occurrence(source_id,original_gloss,hyperlink) VALUES(1,'GLOSA','https://example.test/no-es-video')")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,2)")
        db.executemany("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,is_current) VALUES(?,?,?,?)",((1,2,"CM_1",1),(1,2,"UB_2",1),(2,3,"MOV_1",1),(1,3,"HISTORICA",0)))
        db.executemany("INSERT INTO media_asset(storage_backend,storage_key,mime_type,origin_kind) VALUES(?,?,?,?)",(("external","https://media.test/uno.mp4","video/mp4","external_reference"),("external","https://media.test/captura.mp4","video/mp4","external_reference"),("external","https://media.test/foto.jpg","image/jpeg","external_reference")))
        db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id) VALUES(1,1)")
        db.execute("INSERT INTO occurrence_media(occurrence_id,media_asset_id) VALUES(1,2)")
        db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id) VALUES(2,3)")
        db.commit(); db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates"),static_folder=str(ROOT/"static")); app.testing=True; app.register_blueprint(catalog_bp); install_access_context(app); app.wsgi_app.routes={"ana":"analyst","rev":"reviewer","mas":"master"}
        app.add_url_rule("/trabajo",endpoint="test_work",view_func=lambda:"<body>Trabajo</body>")
        self.patches=[patch("routes.catalog.conectar",side_effect=self.connect),patch("access_control.conectar",side_effect=self.connect)]
        for item in self.patches:item.start()
        self.client=app.test_client()
    def tearDown(self):
        for item in self.patches:item.stop()
        self.temp.cleanup()
    def connect(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
    def test_projection_uses_only_alternative_media_deterministically(self):
        db=self.connect(); one=build_catalog_projection(db); two=build_catalog_projection(db); db.close()
        self.assertEqual(one,two); alternatives=next(c for c in one["concepts"] if c["preferred_label"]=="UNO")["alternatives"]
        self.assertEqual([m["storage_key"] for m in alternatives[0]["media"]],["https://media.test/uno.mp4"])
        self.assertEqual(alternatives[1]["media"][0]["mime_type"],"image/jpeg")
        self.assertNotIn("captura.mp4",json.dumps(one)); self.assertNotIn("occurrence_media",json.dumps(one))
    def test_snapshot_freezes_media_and_new_publication_detects_change(self):
        db=self.connect(); first=publish_catalog(db,publication_comment="v1",actor_context={"access_role":"master"}); frozen=first["snapshot_json"]
        db.execute("INSERT INTO media_asset(storage_backend,storage_key,mime_type) VALUES('external','https://media.test/nuevo.mp4','video/mp4')"); db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id) VALUES(1,4)"); db.commit()
        current=build_catalog_projection(db); self.assertNotEqual(json.loads(frozen),current)
        second=publish_catalog(db,publication_comment="v2 media",actor_context={"access_role":"master"}); db.close()
        self.assertEqual(first["version_number"],1); self.assertEqual(second["version_number"],2); self.assertTrue(verify_publication_hash(first)); self.assertEqual(first["snapshot_json"],frozen)
        summary=json.loads(second["change_summary_json"]); self.assertEqual(summary["alternatives_changed"][0]["alternative_id"],1)
        self.assertTrue(build_catalog_diff(json.loads(frozen),current)["alternatives_changed"])
    def test_groups_and_network_use_explicit_current_connected_components(self):
        db=self.connect(); concept=next(c for c in build_catalog_projection(db)["concepts"] if c["preferred_label"]=="UNO"); db.close()
        self.assertEqual([[a["alternative_id"] for a in group] for group in variation_groups(concept)],[[1,2,3],[4]])
        edges=relation_edges(concept); self.assertEqual(len(edges),2); self.assertEqual(edges[0]["parameters"],["CM_1","UB_2"])
        self.assertFalse(any(edge["low_id"]==1 and edge["high_id"]==3 for edge in edges))
    def test_video_filter_markup_detail_network_and_standalone(self):
        html=self.client.get("/ana/catalogo-interno/conceptos/1").get_data(as_text=True)
        self.assertIn("Solo video",html); self.assertIn('data-has-video="true"',html); self.assertIn('data-has-video="false"',html)
        self.assertIn("Grupo de variación 1",html); self.assertIn("Grupo de variación 2",html); self.assertEqual(html.count('class="linea-red"'),2)
        self.assertIn("CM_1 · UB_2",html); self.assertNotIn("HISTORICA",html); self.assertIn("data-alternative-select",html)
        self.assertIn("<video controls",html); self.assertIn("https://media.test/uno.mp4",html); self.assertNotIn("Video regrabado no disponible",html)
        self.assertNotIn("lesico-internal-context",html); self.assertNotIn("Trabajando como",html)
        self.assertEqual(self.client.get("/catalogo-interno").status_code,404)
        work=self.client.get("/ana/trabajo").get_data(as_text=True); self.assertIn('target="_blank"',work); self.assertIn('rel="noopener"',work)
        db=self.connect(); publish_catalog(db,publication_comment="temporal",actor_context={"access_role":"master"}); db.close()
        for path in ("/catalogo","/catalogo/v1"):
            public=self.client.get(path).get_data(as_text=True); self.assertNotIn("lesico-internal-context",public); self.assertIn('data-has-video="true"',public)

if __name__=="__main__": unittest.main()
