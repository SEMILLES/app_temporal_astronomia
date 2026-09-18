import re
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from occurrence_registration import complete_registration, save_draft
from source_details import (SOURCE_TYPES, source_type_labels, effective_detail_2_applicability,
                            normalize_applicability_override, normalize_occurrence_details,
                            catalog_source_reference)
from source_forms import source_form_values
from source_period import format_source_period
from tests import test_occurrence_grammar_routes as fixtures
from tests.form_client import hidden

ROOT = Path(__file__).resolve().parents[1]
MESA = 'MESA_DE_TRABAJO'
KEY = 'source_detail_2_applicability_override'
FIELDS = ('source_detail_1', 'source_detail_1_status', 'source_detail_2',
          'source_detail_2_status', KEY)
CANONICAL = (None, 'NA', None, 'NA', None)
LEGACY = [(None, 'UNKNOWN', None, 'UNKNOWN', None),
          ('  Legacy title  ', 'VALUE', None, 'UNKNOWN', None),
          ('Legacy title', 'VALUE', None, 'NA', None),
          ('Legacy title', 'VALUE', 'Legacy locator', 'VALUE', None)]


class MesaTests(unittest.TestCase):
    connect = fixtures.GrammarWorkflowRouteTests.connect
    tearDown = fixtures.GrammarWorkflowRouteTests.tearDown

    def setUp(self):
        fixtures.GrammarWorkflowRouteTests.setUp(self)
        self.client.application.jinja_env.filters['source_period'] = format_source_period
        db = self.connect()
        db.execute('UPDATE source SET source_type=? WHERE source_id=1', (MESA,))
        db.execute("INSERT INTO source(source_name,source_type) VALUES('Video','VIDEO_POR_SENA')")
        db.execute("INSERT INTO source(source_name,source_type) VALUES('Another Mesa',?)", (MESA,))
        db.commit()
        db.close()

    def state(self, table='occurrence', identifier=1):
        db = self.connect()
        try:
            pk = 'draft_id' if table == 'occurrence_draft' else 'occurrence_id'
            return tuple(db.execute(f'SELECT {",".join(FIELDS)} FROM {table} WHERE {pk}=?',
                                    (identifier,)).fetchone())
        finally:
            db.close()

    def legacy(self, values, source=1):
        db = self.connect()
        db.execute(f'UPDATE occurrence SET source_id=?,{",".join(f+"=?" for f in FIELDS)} WHERE occurrence_id=1',
                   (source, *values))
        db.commit()
        db.close()

    def test_type_labels_applicability_and_catalog(self):
        self.assertIn(MESA, SOURCE_TYPES)
        self.assertEqual(source_form_values({'source_name': 'Mesa', 'source_type': MESA})[1], MESA)
        self.assertEqual(source_type_labels(MESA), (None, None, False))
        for values in LEGACY:
            with self.subTest(values=values):
                item = dict(zip(FIELDS, values), source={'source_type': MESA})
                self.assertFalse(effective_detail_2_applicability(MESA, item['source_detail_2_status']))
                self.assertIsNone(catalog_source_reference(item))
                self.assertEqual(normalize_occurrence_details(
                    MESA, values[1], values[0], values[3], values[2]), ('NA', None, 'NA', None))
        for override in (0, 1, '0', '1'):
            with self.assertRaises(ValueError):
                normalize_applicability_override(MESA, override)
            with self.assertRaises(ValueError):
                normalize_occurrence_details(MESA, None, None, None, None, **{KEY: override})

    def test_new_registration_and_draft_are_canonical(self):
        db = self.connect()
        try:
            oid = complete_registration(db, source_id=1, original_gloss='NEW', concept_id=1)
            self.assertEqual(self.state(identifier=oid), CANONICAL)
            draft = save_draft(db, source_id=1, original_gloss='DRAFT', reference_concept_id=1)
            self.assertEqual(self.state('occurrence_draft', draft), CANONICAL)
            oid = complete_registration(db, draft_id=draft)
            self.assertEqual(self.state(identifier=oid), CANONICAL)
            oid = complete_registration(db, source_id=1, original_gloss='STALE INPUT', concept_id=1,
                                        source_detail_1='Old title', source_detail_1_status='VALUE',
                                        source_detail_2='Invalid time', source_detail_2_status='VALUE')
            self.assertEqual(self.state(identifier=oid), CANONICAL)
        finally:
            db.close()

    def test_same_source_edit_preserves_exact_legacy_and_revision(self):
        for values in LEGACY:
            with self.subTest(values=values):
                self.legacy(values)
                html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
                self.assertEqual(self.state(), values)
                data = {'source_id': '1', 'original_gloss': 'EDITED', 'edit_token': hidden(html, 'edit_token'),
                        'source_detail_1': 'Should be ignored', 'source_detail_1_status': 'NA',
                        'source_detail_2': 'Should be ignored', 'source_detail_2_status': 'NA', KEY: ''}
                self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data).status_code, 302)
                self.assertEqual(self.state(), values)
        db = self.connect()
        row = db.execute(f'SELECT {",".join(FIELDS)} FROM occurrence_revision ORDER BY occurrence_revision_id LIMIT 1').fetchone()
        self.assertEqual(tuple(row), LEGACY[0])
        db.close()

    def test_changing_source_to_mesa_canonicalizes_even_between_mesas(self):
        for previous_source in (2, 3):
            with self.subTest(previous_source=previous_source):
                values = ('Previous title', 'VALUE', '2:03', 'VALUE', 1 if previous_source == 2 else None)
                self.legacy(values, source=previous_source)
                html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
                data = dict(zip(FIELDS, values))
                data.update(source_id='1', original_gloss='CHANGED', edit_token=hidden(html, 'edit_token'))
                data[KEY] = ''
                self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data).status_code, 302)
                self.assertEqual(self.state(), CANONICAL)

    def test_browser_legacy_hidden_unchanged_cache_and_save(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            for values in LEGACY:
                with self.subTest(values=values):
                    self.legacy(values)
                    html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
                    page.set_content(re.sub(r'<script src="[^"]*source-details.js"></script>', '', html))
                    snapshot = lambda: [page.locator(f'[name={field}]').input_value() for field in FIELDS]
                    before = snapshot()
                    page.add_script_tag(content=(ROOT/'static/source-details.js').read_text(encoding='utf-8'))
                    self.assertTrue(page.locator('#source-details').is_hidden())
                    self.assertTrue(page.locator('#time-applicability').is_hidden())
                    self.assertEqual(snapshot(), before)
                    page.locator('[name=original_gloss]').dispatch_event('change')
                    self.assertEqual(snapshot(), before)
                    page.locator('[name=source_id]').select_option('2')
                    self.assertTrue(page.locator('#source-details').is_visible())
                    page.locator('#time-applicability-checkbox').set_checked(True)
                    page.locator('[name=source_detail_1_status]').select_option('VALUE')
                    page.locator('[name=source_detail_1]').fill('Video title')
                    page.locator('[name=source_detail_2]').fill('3:04')
                    video = snapshot()
                    page.locator('[name=source_id]').select_option('1')
                    self.assertEqual(snapshot(), before)
                    page.locator('[name=source_id]').select_option('2')
                    self.assertEqual(snapshot(), video)
                    page.locator('[name=source_id]').select_option('1')
                    data = page.locator('form').evaluate('form => {form.dispatchEvent(new Event("submit", {cancelable:true})); return Object.fromEntries(new FormData(form));}')
                    self.assertEqual(snapshot(), before)
                    self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data).status_code, 302)
                    self.assertEqual(self.state(), values)
            browser.close()

    def test_browser_new_and_changed_source_submit_canonical_mesa(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            self.legacy(('Video title', 'VALUE', '2:03', 'VALUE', 1), source=2)
            for path in ('/ocurrencias/1/editar', '/aportes/nuevo'):
                html = self.client.get(path).get_data(as_text=True)
                page.set_content(re.sub(r'<script src="[^"]*source-details.js"></script>', '', html))
                page.add_script_tag(content=(ROOT/'static/source-details.js').read_text(encoding='utf-8'))
                page.locator('[name=source_id]').select_option('1')
                self.assertTrue(page.locator('#source-details').is_hidden())
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '')
                for name in ('source_detail_1', 'source_detail_2'):
                    self.assertFalse(page.locator(f'[name={name}]').evaluate('input => input.required'))
                data = page.locator('form').evaluate('form => Object.fromEntries(new FormData(form))')
                if path.endswith('nuevo'):
                    data.update(original_gloss='NEW MESA', reference_kind='concept', reference_concept_id='1')
                    response = self.client.post('/aportes', data=data)
                    identifier = 2
                else:
                    response = self.client.post('/ocurrencias/1/actualizar', data=data)
                    identifier = 1
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.state(identifier=identifier), CANONICAL)
            browser.close()


if __name__ == '__main__':
    unittest.main()
