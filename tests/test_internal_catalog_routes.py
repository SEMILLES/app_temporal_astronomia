import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from access_control import install_access_context
from catalog_projection import build_catalog_projection
from concept_classification import apply_metadata
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
        self.assertIn('data-knowledge-areas="Astronomía||Lingüística"',html)
        self.assertNotIn('id="filtro-area"',html)
        self.assertIn('id="filtro-campos"',html)

    def academic_fixture(self):
        db = self.connect()
        db.execute("UPDATE concept SET semantic_field_1='Campo sintetico',knowledge_area_1='Area sintetica'")
        db.execute("INSERT INTO concept(preferred_label,knowledge_area_1) VALUES('EXTERNO','Area sintetica')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1b')")
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,3,'CM')")
        db.execute("INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation) VALUES(1,1,'N/A')")
        db.execute("INSERT INTO alternative_component(alternative_morphology_id,position,component_alternative_id) VALUES(1,1,2)")
        db.commit()
        collection = db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        apply_metadata(db, 1, {'collections': {collection: 'join'}}, access_role='master')
        db.close()

    def test_academic_scope_permissions_counts_filters_and_links(self):
        self.academic_fixture()
        before = self.digest()
        base = '/catalogo-interno/colecciones/academica'
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.get('/incorrecto' + base).status_code, 404)
        for role in ('ana', 'rev', 'mas'):
            academic = f'/{role}{base}'
            response = self.client.get(academic)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('COSMOS', html)
            self.assertNotIn('EXTERNO', html)
            self.assertIn('1 conceptos · 2 alternativas · 1 ocurrencias', html)
            self.assertIn('Vocabulario académico en LSC', html)
            self.assertIn('id="filtro-area"', html)
            self.assertNotIn('id="filtro-campos"', html)
            self.assertIn('<span>Area sintetica</span>', html)
            self.assertNotIn('<span>Campo sintetico</span>', html)
            analyzed = self.client.get(f'/{role}/catalogo-interno').get_data(as_text=True)
            self.assertIn('EXTERNO', analyzed)
            self.assertIn('Colección Analizada', analyzed)
            self.assertIn('<span>Campo sintetico</span>', analyzed)
            self.assertNotIn('id="filtro-area"', analyzed)
            for page in (html, analyzed):
                for control in ('buscador-catalogo', 'filtro-video', 'filtro-variacion'):
                    self.assertIn(f'id="{control}"', page)
            for entity in ('conceptos', 'alternativas'):
                self.assertEqual(self.client.get(f'{academic}/{entity}/1').status_code, 200)
                self.assertEqual(self.client.get(f'{academic}/{entity}/2').status_code, 404)
                self.assertEqual(self.client.get(f'/{role}/catalogo-interno/{entity}/2').status_code, 200)
            detail = self.client.get(academic + '/conceptos/1?q=cosmos&area=area&campo=ignorado&video=1&variacion=both').get_data(as_text=True)
            self.assertIn('EXTERNO-1a', detail)  # Frozen morphology name outside scope.
            self.assertIn(f'{academic}/alternativas/3?', detail)
            self.assertIn('class="nodo-red', detail)
            self.assertNotIn(f'href="/{role}/catalogo-interno/alternativas/', detail)
            self.assertIn('area=area', detail)
            self.assertNotIn('campo=ignorado', detail)
            self.assertIn('video=1', detail)
            self.assertIn('variacion=both', detail)
        analyzed = self.client.get('/ana/catalogo-interno/conceptos/1?campo=uno&campo=dos&area=ignorada').get_data(as_text=True)
        self.assertIn('campo=uno&amp;campo=dos', analyzed)
        self.assertNotIn('area=ignorada', analyzed)
        self.assertEqual(self.digest(), before)

    def test_classification_metadata_queries_are_batched(self):
        self.academic_fixture()
        statements = []
        def traced_connection():
            db = self.connect()
            db.execute('PRAGMA query_only=ON')
            db.set_trace_callback(statements.append)
            return db
        with patch('routes.catalog.conectar', side_effect=traced_connection):
            for path in ('/ana/catalogo-interno', '/ana/catalogo-interno/colecciones/academica'):
                statements.clear()
                self.assertEqual(self.client.get(path).status_code, 200)
                classification = [s for s in statements if 'concept_classification_revision' in s]
                memberships = [s for s in statements if 'collection_membership' in s]
                self.assertEqual(len(classification), 1)
                self.assertEqual(len(memberships), 1)
                self.assertNotIn('concept_id=', classification[0])


if __name__ == "__main__":
    unittest.main()
