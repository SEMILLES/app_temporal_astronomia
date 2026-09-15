"""Shared internal presentation against synthetic data; no application startup."""
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from flask import Response
from playwright.sync_api import sync_playwright

from access_control import install_access_context
from concept_labels import human_concept_label
from conflict_presentation import local_timestamp
from instance_presentation import install_instance_presentation
from source_period import format_source_period
from routes.main import main_bp
from routes.sources import sources_bp
from routes.collaborators import collaborators_bp
from routes.conflicts import conflicts_bp
from routes.catalog import catalog_bp
from tests import test_alternative_routes as fixtures


class InternalVisualTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AlternativeRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        env = patch.dict(os.environ, {f'LESICO_{role.upper()}_ROUTE': f'visual-{role}'
                                      for role in ('analyst', 'reviewer', 'master')})
        env.start()
        self.addCleanup(env.stop)
        self.client = self.fixture.client
        app = self.client.application
        app.static_folder = str(fixtures.ROOT / 'static')
        for bp in (main_bp, sources_bp, collaborators_bp, conflicts_bp, catalog_bp):
            app.register_blueprint(bp)
        app.jinja_env.filters.update(human_concept_label=human_concept_label,
                                     source_period=format_source_period,
                                     local_timestamp=local_timestamp)
        app.config['LESICO_INSTANCE_MODE'] = 'testing'
        install_instance_presentation(app)
        install_access_context(app)
        for status in (400, 403, 404, 409, 500):
            app.add_url_rule(f'/visual-error-{status}', endpoint=f'visual_error_{status}',
                             view_func=lambda status=status: Response(
                                 '<html><body><p>Error de prueba</p></body></html>', status=status))
        db = self.fixture.connect()
        db.execute("INSERT INTO collaborator(display_name) VALUES('Nombre <seguro> & uno')")
        db.execute("INSERT INTO collaborator(display_name,active) VALUES('Inactivo',0)")
        db.execute("UPDATE source SET source_type='OTRO'")
        db.commit()
        db.close()

    def snapshot(self):
        db = self.fixture.connect()
        result = '\n'.join(db.iterdump())
        db.close()
        return result

    def test_navigation_exact_links_for_each_role_and_restricted_routes(self):
        shared = ['/trabajo', '/ocurrencias', '/borradores', '/fuentes', '/conceptos', '/aportes', '/catalogo-interno']
        review = ['/aportes/pendientes', '/conflictos']
        admin = ['/colaboradores', '/actualizar-catalogo', '/publicaciones']
        before = self.snapshot()
        for role in ('analyst', 'reviewer', 'master'):
            prefix = f'/visual-{role}'
            response = self.client.get(prefix + '/trabajo')
            self.assertEqual(response.status_code, 200)
            header = re.search(r'<aside id="lesico-internal-context".*?</aside>', response.text, re.S)[0]
            expected = shared + (review if role != 'analyst' else []) + (admin if role == 'master' else [])
            self.assertEqual(re.findall(r'href="([^"]+)"', header), [prefix + path for path in expected])
            self.assertEqual(re.findall(r'<strong>(.*?)</strong>', header),
                             ['ANÁLISIS', 'CATÁLOGO'] + (['REVISIÓN'] if role != 'analyst' else []) +
                             (['ADMINISTRACIÓN'] if role == 'master' else []))
            self.assertIn('Nombre &lt;seguro&gt; &amp; uno', header)
            self.assertNotIn('Inactivo', header)
            for path in review + admin:
                allowed = role == 'master' or (role == 'reviewer' and path in review)
                self.assertEqual(self.client.get(prefix + path).status_code, 200 if allowed else 404)
            if role != 'master':
                self.assertEqual(self.client.post(prefix + '/colaboradores', data={'display_name': 'No'}).status_code, 404)
                self.assertEqual(self.client.post(prefix + '/actualizar-catalogo').status_code, 404)
            if role == 'analyst':
                self.assertEqual(self.client.post(prefix + '/aportes/1/decidir', data={'decision': 'accepted'}).status_code, 404)
        for path in ('/trabajo', '/invalid/trabajo'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(before, self.snapshot())

    def test_error_responses_keep_status_and_header_exclusion(self):
        for status in (400, 403, 404, 409, 500):
            with self.subTest(status=status), patch('access_control.conectar', side_effect=AssertionError('Unexpected query')):
                response = self.client.get(f'/visual-reviewer/visual-error-{status}')
                self.assertEqual(response.status_code, status)
                self.assertIn('Error de prueba', response.text)
                self.assertNotIn('lesico-internal-context', response.text)
                self.assertNotIn('lesico-collaborator', response.text)
                self.assertIn('ENTORNO DE PRUEBAS', response.text)

    def test_catalog_keeps_navigation_exclusion_and_public_markup(self):
        before = self.snapshot()
        for role in ('analyst', 'reviewer', 'master'):
            response = self.client.get(f'/visual-{role}/catalogo-interno')
            self.assertEqual(response.status_code, 200)
            self.assertIn('visual-base.css', response.text)
            self.assertIn('catalogo/catalogo.js', response.text)
            self.assertIn('buscador-catalogo', response.text)
            self.assertNotIn('lesico-internal-context', response.text)
            self.assertNotIn('lesico-collaborator', response.text)
        public = self.client.get('/catalogo')
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('visual-base.css', public.text)
        self.assertNotIn('catalog-internal-context', public.text)
        self.assertNotIn('access-context', public.text)
        self.assertEqual(before, self.snapshot())

    def test_all_internal_documents_use_shared_base(self):
        for path in (fixtures.ROOT / 'templates').glob('*.html'):
            if path.name.startswith('_') or path.name == 'catalogo_lesico.html':
                continue
            with self.subTest(template=path.name):
                self.assertIn('{% extends "_visual_base.html" %}', path.read_text(encoding='utf-8'))

    def test_browser_internal_pages_desktop_and_narrow(self):
        # Create a pending analysis through the existing workflow, in this disposable fixture only.
        response = self.client.post('/visual-reviewer/ocurrencias/2/clasificar', data={
            'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO', 'morphology_component_count': 'N/A'})
        self.assertEqual(response.status_code, 302)
        before = self.snapshot()
        paths = ['/trabajo', '/aportes/nuevo', '/ocurrencias/1/editar', '/ocurrencias/1/gramatica?flow=registration',
                 '/ocurrencias/1/resumen', '/fuentes', '/fuentes/1/editar', '/borradores', '/aportes',
                 '/aportes/pendientes', '/conceptos/1/editar', '/colaboradores', '/actualizar-catalogo',
                 '/publicaciones', '/conflictos', '/conflictos/nuevo']
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def serve(route):
                url = urlsplit(route.request.url)
                response = self.client.get(url.path + ('?' + url.query if url.query else ''))
                route.fulfill(status=response.status_code, body=response.data, content_type=response.content_type)
                response.close()
            page.route('http://visual.test/**', serve)
            for width in (1280, 390):
                page.set_viewport_size({'width': width, 'height': 900})
                for path in paths:
                    with self.subTest(width=width, path=path):
                        result = page.goto('http://visual.test/visual-master' + path)
                        self.assertEqual(result.status, 200)
                        self.assertEqual(page.locator('main').count(), 1)
                        self.assertTrue(page.locator('main h1').is_visible())
                        self.assertTrue(page.locator('#lesico-internal-context').is_visible())
                        self.assertTrue(page.locator('#lesico-testing-banner').is_visible())
                        self.assertEqual(page.locator('body').evaluate('e => getComputedStyle(e).fontFamily'), 'system-ui, sans-serif')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                        missing = page.locator('input:not([type=hidden]),select,textarea').evaluate_all(
                            "es => es.filter(e => !e.labels?.length && !e.getAttribute('aria-label')).map(e => e.name)")
                        self.assertEqual(missing, [])
                        if path.endswith('/resumen'):
                            self.assertNotRegex(page.locator('main').inner_text(), r'\boccurrences?\b')
                        if path == '/fuentes':
                            self.assertEqual(page.locator('.source-field-group [name]').count(), 13)
                            self.assertGreater(page.locator('.source-table th').nth(1).bounding_box()['width'], 100)
                        if path == '/aportes/pendientes':
                            page.locator('main details').first.locator(':scope > summary').click()
                            self.assertTrue(page.locator('main details').first.evaluate('e => e.open'))
                        if os.environ.get('VISUAL_SCREENSHOT_DIR'):
                            target = Path(os.environ['VISUAL_SCREENSHOT_DIR'])
                            target.mkdir(parents=True, exist_ok=True)
                            name = path.strip('/').replace('/', '-').replace('?', '-')
                            page.screenshot(path=str(target / f'{name}-{width}.png'), full_page=True)
            page.goto('http://visual.test/visual-master/trabajo')
            page.locator('#lesico-collaborator').select_option('1')
            page.goto('http://visual.test/visual-master/colaboradores')
            self.assertEqual(page.locator('#lesico-collaborator').input_value(), '1')
            self.assertEqual(page.locator('main form input[name=collaborator_id]').first.input_value(), '1')
            self.assertEqual(errors, [])
            browser.close()
        self.assertEqual(before, self.snapshot())
