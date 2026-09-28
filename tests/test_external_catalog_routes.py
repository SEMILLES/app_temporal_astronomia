import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from access_control import install_access_context
from catalog_publication import publish_catalog
from catalog_projection import build_catalog_projection
from concept_classification import apply_metadata
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
        old=self.client.get("/catalogo/v1").get_data(as_text=True); self.assertIn("Versión histórica del catálogo: v1.",old); self.assertNotIn("VIVO",old)
        self.assertEqual(self.client.get("/catalogo/v99").status_code,404)
    def test_master_admin_only_and_crafted_role_ignored(self):
        self.assertEqual(self.client.get("/mas/actualizar-catalogo").status_code,200)
        self.assertEqual(self.client.get("/rev/actualizar-catalogo").status_code,404)
        response=self.client.post("/ana/actualizar-catalogo",data={"access_role":"master","publication_comment":"x"}); self.assertEqual(response.status_code,404)

    def test_shared_snapshot_academic_latest_history_and_scope_security(self):
        base = '/colecciones/academica'
        self.assertIn('Aún no hay una versión publicada', self.client.get(base).get_data(as_text=True))
        self.assertEqual(self.client.get(base + '/v99').status_code, 404)
        db = self.connect()
        db.executemany('INSERT INTO concept(preferred_label,semantic_field_1,knowledge_area_1) VALUES(?,?,?)',
                       [('ACADEMICO', 'Campo', 'Area'), ('GENERAL', 'Campo', 'Area')])
        db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)', [(1,'1a'),(2,'1a'),(1,'1b')])
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,3,'CM')")
        db.commit()
        collection = db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        apply_metadata(db, 1, {'collections': {collection: 'join'}}, access_role='master')
        db.close()
        first = self.publish('Snapshot compartido sintetico')
        publications = self.client.get('/mas/publicaciones').get_data(as_text=True)
        self.assertIn('/mas/catalogo/v1', publications)
        self.assertIn('/mas/colecciones/academica/v1', publications)
        self.assertIn('Colección Analizada v1', publications)
        self.assertIn('Vocabulario Académico v1', publications)
        for suffix in ('', '/v1'):
            analyzed = self.client.get('/catalogo' + suffix).get_data(as_text=True)
            academic = self.client.get(base + suffix).get_data(as_text=True)
            self.assertIn('GENERAL', analyzed)
            self.assertIn('ACADEMICO', analyzed)
            self.assertIn('ACADEMICO', academic)
            self.assertIn(f'href="{base}{suffix}" aria-current="page"', academic)
            self.assertIn(f'href="/catalogo{suffix}"', academic)
            outsider = self.client.get('/catalogo' + suffix + '/conceptos/2').get_data(as_text=True)
            self.assertIn(f'href="{base}{suffix}"', outsider)
            self.assertNotIn(f'href="{base}{suffix}/conceptos/2"', outsider)
            self.assertNotIn('GENERAL', academic)
            self.assertIn('1 conceptos · 2 alternativas · 0 ocurrencias', academic)
            self.assertIn('data-filter-param="area"', academic)
            self.assertNotIn('data-filter-param="campo"', academic)
            self.assertIn('data-filter-param="campo"', analyzed)
            self.assertNotIn('data-filter-param="area"', analyzed)
            for page in (academic, analyzed):
                for control in ('buscador-catalogo', 'filtro-video', 'filtro-variacion'):
                    self.assertIn(f'id="{control}"', page)
            for entity in ('conceptos', 'alternativas'):
                self.assertEqual(self.client.get(base + suffix + f'/{entity}/1').status_code, 200)
                self.assertEqual(self.client.get(base + suffix + f'/{entity}/2').status_code, 404)
                self.assertEqual(self.client.get('/catalogo' + suffix + f'/{entity}/2').status_code, 200)
            detail = self.client.get(base + suffix + '/conceptos/1?area=area&campo=omitido&q=acad&video=1').get_data(as_text=True)
            self.assertIn(f'{base}{suffix}/alternativas/3?', detail)
            self.assertIn('href="/catalogo' + suffix + '/conceptos/1"', detail)
            self.assertIn('area=area', detail)
            self.assertNotIn('campo=omitido', detail)
        # Live membership changes cannot change either view of the publication.
        db = self.connect()
        apply_metadata(db, 1, {'collections': {collection: 'leave'}}, access_role='master')
        self.assertIn('ACADEMICO', self.client.get(base).get_data(as_text=True))
        self.assertEqual(db.execute('SELECT count(*) FROM catalog_publication').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT concept_count FROM catalog_publication').fetchone()[0], 2)
        db.close()
        self.publish('Retiro sintetico')
        self.assertNotIn('ACADEMICO', self.client.get(base).get_data(as_text=True))
        historical = self.client.get(base + '/v1').get_data(as_text=True)
        self.assertIn('ACADEMICO', historical)
        self.assertIn('Versión histórica', historical)
        self.assertIn(f'href="{base}"', historical)
        self.assertIn(base + '/v1/conceptos/1', historical)
        db = self.connect()
        self.assertEqual(db.execute('SELECT snapshot_json FROM catalog_publication WHERE publication_id=?',
                                   (first['publication_id'],)).fetchone()[0], first['snapshot_json'])
        db.close()

    def test_old_snapshot_without_memberships_is_only_analyzed(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label,knowledge_area_1) VALUES('ANTIGUO','Area')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        projection = build_catalog_projection(db)
        db.close()
        for concept in projection['concepts']:
            concept.pop('collections')
            concept.pop('classifications')
        # Synthetic historical publication, never a real database.
        publication = {'snapshot_json': json.dumps(projection), 'version_number': 1,
                       'published_at_display': 'Fecha sintetica', 'snapshot_sha256': 'synthetic'}
        with patch('routes.catalog._publication', return_value=(publication, 2)):
            for suffix in ('', '/v1'):
                self.assertIn('ANTIGUO', self.client.get('/catalogo' + suffix).get_data(as_text=True))
                response = self.client.get('/colecciones/academica' + suffix)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('ANTIGUO', response.get_data(as_text=True))
                for entity in ('conceptos', 'alternativas'):
                    self.assertEqual(self.client.get(f'/colecciones/academica{suffix}/{entity}/1').status_code, 404)
if __name__=="__main__": unittest.main()
