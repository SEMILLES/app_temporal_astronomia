"""Pilot layout checks in a browser, using only a disposable synthetic database."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from access_control import install_access_context
from instance_presentation import install_instance_presentation
from tests import test_alternative_routes as fixtures


class VisualPilotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AlternativeRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.env = patch.dict(os.environ, {"LESICO_REVIEWER_ROUTE": "visual-review"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = self.fixture.client
        app = self.client.application
        app.static_folder = str(fixtures.ROOT / "static")
        app.add_url_rule('/trabajo', endpoint='main.trabajo', view_func=lambda: '')
        app.config['LESICO_INSTANCE_MODE'] = 'testing'
        install_access_context(app)
        install_instance_presentation(app)

    def test_pilots_fit_viewport_and_preserve_context_and_controls(self):
        db = self.fixture.connect()
        db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(1,'2a','2020-01-01')")
        db.commit()
        before = '\n'.join(db.iterdump())
        db.close()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def serve(route):
                response = self.client.get(urlsplit(route.request.url).path)
                route.fulfill(status=response.status_code, body=response.data,
                              content_type=response.content_type)
                response.close()

            page.route('http://visual.test/**', serve)
            for width in (1280, 390):
                page.set_viewport_size({'width': width, 'height': 900})
                for path in ('/ocurrencias', '/ocurrencias/2/clasificar', '/conceptos',
                             '/conceptos/1/alternativas', '/alternativas/1/gestionar'):
                    with self.subTest(width=width, path=path):
                        page.goto('http://visual.test/visual-review' + path)
                        self.assertTrue(page.locator('main h1').is_visible())
                        self.assertTrue(page.locator('#lesico-testing-banner').is_visible())
                        self.assertTrue(page.locator('#lesico-collaborator').is_visible())
                        self.assertEqual(page.locator('.access-context').inner_text(), 'Rol: Revisor')
                        self.assertEqual(page.locator('body').evaluate(
                            "e => getComputedStyle(e).fontFamily"), 'system-ui, sans-serif')
                        self.assertTrue(page.evaluate(
                            'document.documentElement.scrollWidth <= innerWidth + 1'))
                        self.assertEqual(page.locator('input:not([type=hidden]), select, textarea').evaluate_all(
                            "es => es.filter(e => !e.labels?.length && !e.getAttribute('aria-label')).map(e => e.name)"), [])
                        if path.endswith('/clasificar'):
                            self.assertIn('OCC-000002', page.locator('main').inner_text())
                            page.locator('[name=proposal_kind][value=NEW]').check()
                            self.assertTrue(page.locator('#morphology').is_visible())
                            page.locator('[name=proposal_kind][value=EXISTING]').check()
                            self.assertTrue(page.locator('#existing-alternative-field').is_visible())
                            self.assertFalse(page.locator('#morphology').is_visible())
                        if path.endswith('/alternativas'):
                            self.assertFalse(page.locator('#retired-alternatives details').evaluate('e => e.open'))
                        if path.endswith('/gestionar'):
                            self.assertEqual(page.locator('.history[open]').count(), 0)
                        if os.environ.get('VISUAL_SCREENSHOT_DIR'):
                            target = Path(os.environ['VISUAL_SCREENSHOT_DIR'])
                            target.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(target / (path.strip('/').replace('/', '-') + f'-{width}.png')), full_page=True)
            self.assertEqual(errors, [])
            browser.close()
        db = self.fixture.connect()
        self.assertEqual(before, '\n'.join(db.iterdump()))
        db.close()
