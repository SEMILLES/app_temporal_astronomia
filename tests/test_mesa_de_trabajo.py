import re
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from occurrence_registration import complete_registration, save_draft, RegistrationError
from source_details import (
    SOURCE_TYPES, source_type_labels, effective_detail_2_applicability,
    normalize_applicability_override, normalize_occurrence_details,
    catalog_source_reference,
)
from source_forms import source_form_values
from source_period import format_source_period
from tests import test_occurrence_grammar_routes as fixtures
from tests.form_client import hidden


ROOT = Path(__file__).resolve().parents[1]
MESA = 'MESA_DE_TRABAJO'
KEY = 'source_detail_2_applicability_override'
FIELDS = (
    'source_detail_1', 'source_detail_1_status',
    'source_detail_2', 'source_detail_2_status', KEY,
)
EMPTY = (None, 'UNKNOWN', None, 'UNKNOWN', None)


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
            return tuple(db.execute(
                f'SELECT {",".join(FIELDS)} FROM {table} WHERE {pk}=?',
                (identifier,),
            ).fetchone())
        finally:
            db.close()

    def set_occurrence(self, values, source=1):
        db = self.connect()
        db.execute(
            f'UPDATE occurrence SET source_id=?,{",".join(f+"=?" for f in FIELDS)} '
            'WHERE occurrence_id=1',
            (source, *values),
        )
        db.commit()
        db.close()

    def test_type_labels_normalization_and_catalog(self):
        self.assertIn(MESA, SOURCE_TYPES)
        self.assertEqual(
            source_form_values({'source_name': 'Mesa', 'source_type': MESA})[1],
            MESA,
        )
        self.assertEqual(
            source_type_labels(MESA),
            ('Fecha', 'Participantes', True),
        )
        self.assertTrue(effective_detail_2_applicability(MESA, 'UNKNOWN'))

        self.assertEqual(
            normalize_occurrence_details(
                MESA,
                'VALUE', '2026-09-27',
                'VALUE', 'Ana,  Carlos ,, Diana',
            ),
            ('VALUE', '2026-09-27', 'VALUE', 'Ana, Carlos, Diana'),
        )

        self.assertEqual(
            normalize_occurrence_details(
                MESA, 'UNKNOWN', None, 'UNKNOWN', None,
            ),
            ('UNKNOWN', None, 'UNKNOWN', None),
        )

        with self.assertRaises(ValueError):
            normalize_occurrence_details(
                MESA, 'VALUE', '27/09/2026',
                'UNKNOWN', None,
            )

        for override in (0, 1, '0', '1'):
            with self.assertRaises(ValueError):
                normalize_applicability_override(MESA, override)

        reference = catalog_source_reference({
            'source': {'source_type': MESA},
            'source_detail_1': '2026-09-27',
            'source_detail_2': 'Ana, Carlos, Diana',
        })
        self.assertEqual(
            reference,
            'Fecha: 2026-09-27 \u00b7 Participantes: Ana, Carlos, Diana',
        )

    def test_registration_normalizes_meeting_data_and_allows_empty(self):
        db = self.connect()
        try:
            oid = complete_registration(
                db,
                source_id=1,
                original_gloss='NEW',
                concept_id=1,
                source_detail_1='2026-09-27',
                source_detail_2='Ana,  Carlos ,, Diana',
            )
            self.assertEqual(
                self.state(identifier=oid),
                ('2026-09-27', 'VALUE',
                 'Ana, Carlos, Diana', 'VALUE', None),
            )

            empty = complete_registration(
                db,
                source_id=1,
                original_gloss='EMPTY',
                concept_id=1,
            )
            self.assertEqual(self.state(identifier=empty), EMPTY)

            with self.assertRaises(RegistrationError):
                complete_registration(
                    db,
                    source_id=1,
                    original_gloss='BAD DATE',
                    concept_id=1,
                    source_detail_1='27/09/2026',
                )
        finally:
            db.close()

    def test_draft_completion_normalizes_meeting_data(self):
        db = self.connect()
        try:
            draft = save_draft(
                db,
                source_id=1,
                original_gloss='DRAFT',
                reference_concept_id=1,
                source_detail_1='2026-09-27',
                source_detail_1_status='VALUE',
                source_detail_2='Ana,  Carlos ,, Diana',
                source_detail_2_status='VALUE',
            )
            oid = complete_registration(db, draft_id=draft)
            self.assertEqual(
                self.state(identifier=oid),
                ('2026-09-27', 'VALUE',
                 'Ana, Carlos, Diana', 'VALUE', None),
            )
        finally:
            db.close()

    def test_edit_mesa_updates_values_and_preserves_revision(self):
        previous = (
            '2026-09-20', 'VALUE',
            'Ana, Carlos', 'VALUE', None,
        )
        self.set_occurrence(previous)

        html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
        response = self.client.post(
            '/ocurrencias/1/actualizar',
            data={
                'source_id': '1',
                'original_gloss': 'EDITED',
                'edit_token': hidden(html, 'edit_token'),
                'source_detail_1': '2026-09-27',
                'source_detail_1_status': 'VALUE',
                'source_detail_2': 'Ana,  Carlos ,, Diana',
                'source_detail_2_status': 'VALUE',
                KEY: '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.state(),
            ('2026-09-27', 'VALUE',
             'Ana, Carlos, Diana', 'VALUE', None),
        )

        db = self.connect()
        try:
            row = db.execute(
                f'SELECT {",".join(FIELDS)} FROM occurrence_revision '
                'ORDER BY occurrence_revision_id DESC LIMIT 1'
            ).fetchone()
            self.assertEqual(tuple(row), previous)
        finally:
            db.close()

    def test_changing_source_to_mesa_accepts_meeting_data(self):
        self.set_occurrence(
            ('Video title', 'VALUE', '2:03', 'VALUE', 1),
            source=2,
        )

        html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
        response = self.client.post(
            '/ocurrencias/1/actualizar',
            data={
                'source_id': '1',
                'original_gloss': 'CHANGED',
                'edit_token': hidden(html, 'edit_token'),
                'source_detail_1': '2026-09-27',
                'source_detail_1_status': 'VALUE',
                'source_detail_2': 'Ana, Carlos',
                'source_detail_2_status': 'VALUE',
                KEY: '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.state(),
            ('2026-09-27', 'VALUE',
             'Ana, Carlos', 'VALUE', None),
        )

    def test_browser_new_mesa_has_specific_meeting_interface(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()

            html = self.client.get('/aportes/nuevo').get_data(as_text=True)
            page.set_content(
                re.sub(
                    r'<script src="[^"]*source-details.js"></script>',
                    '',
                    html,
                )
            )
            page.add_script_tag(
                content=(ROOT / 'static/source-details.js').read_text(
                    encoding='utf-8'
                )
            )

            page.locator('[name=source_id]').select_option('1')

            self.assertTrue(page.locator('#source-details').is_visible())
            self.assertEqual(
                page.locator('[name=source_detail_1]').get_attribute('type'),
                'date',
            )
            self.assertFalse(
                page.locator('[name=source_detail_1]').evaluate(
                    'input => input.required'
                )
            )
            self.assertFalse(
                page.locator('[name=source_detail_2]').evaluate(
                    'input => input.required'
                )
            )
            self.assertTrue(
                page.locator('#mesa-meeting-actions').is_visible()
            )

            page.locator('[name=source_detail_1]').fill('2026-09-27')
            page.locator('[name=source_detail_2]').fill(
                'Ana,  Carlos ,, Diana'
            )

            data = page.locator('form').evaluate(
                "form => { form.dispatchEvent(new Event('submit', {cancelable:true})); return Object.fromEntries(new FormData(form)); }"
            )

            self.assertEqual(data['source_detail_1_status'], 'VALUE')
            self.assertEqual(data['source_detail_2_status'], 'VALUE')
            self.assertEqual(
                data['source_detail_2'],
                'Ana, Carlos, Diana',
            )

            data.update(
                original_gloss='NEW MESA',
                reference_kind='concept',
                reference_concept_id='1',
            )
            response = self.client.post('/aportes', data=data)
            self.assertEqual(response.status_code, 302)

            db = self.connect()
            try:
                row = db.execute(
                    'SELECT source_detail_1,source_detail_1_status,'
                    'source_detail_2,source_detail_2_status '
                    'FROM occurrence WHERE original_gloss=?',
                    ('NEW MESA',),
                ).fetchone()
                self.assertEqual(
                    tuple(row),
                    ('2026-09-27', 'VALUE',
                     'Ana, Carlos, Diana', 'VALUE'),
                )
            finally:
                db.close()

            browser.close()

    def test_browser_edit_does_not_offer_meeting_reuse(self):
        self.set_occurrence((
            '2026-09-20', 'VALUE',
            'Ana, Carlos', 'VALUE', None,
        ))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()

            html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
            page.set_content(
                re.sub(
                    r'<script src="[^"]*source-details.js"></script>',
                    '',
                    html,
                )
            )
            page.add_script_tag(
                content=(ROOT / 'static/source-details.js').read_text(
                    encoding='utf-8'
                )
            )

            self.assertTrue(page.locator('#source-details').is_visible())
            self.assertTrue(
                page.locator('#mesa-meeting-actions').is_hidden()
            )
            self.assertEqual(
                page.locator('[name=source_detail_1]').input_value(),
                '2026-09-20',
            )
            self.assertEqual(
                page.locator('[name=source_detail_2]').input_value(),
                'Ana, Carlos',
            )

            browser.close()


    def test_browser_reuses_meeting_data_and_new_meeting_clears_it(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()

            page.route(
                "http://lesico.test/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body></body></html>",
                ),
            )
            page.goto("http://lesico.test/")

            def load_new_form():
                html = self.client.get('/aportes/nuevo').get_data(as_text=True)
                page.set_content(
                    re.sub(
                        r'<script src="[^"]*source-details.js"></script>',
                        '',
                        html,
                    )
                )
                page.add_script_tag(
                    content=(ROOT / 'static/source-details.js').read_text(
                        encoding='utf-8'
                    )
                )
                page.locator('[name=source_id]').select_option('1')

            load_new_form()

            page.locator('[name=source_detail_1]').fill('2026-09-27')
            page.locator('[name=source_detail_2]').fill(
                'Ana,  Carlos ,, Diana'
            )

            page.locator('form').evaluate(
                "form => form.dispatchEvent(new Event('submit', {cancelable:true}))"
            )

            self.assertEqual(
                page.evaluate(
                    "JSON.parse(sessionStorage.getItem('lesico:mesa-reunion:1'))"
                ),
                {
                    'date': '2026-09-27',
                    'participants': 'Ana, Carlos, Diana',
                },
            )

            load_new_form()

            self.assertEqual(
                page.locator('[name=source_detail_1]').input_value(),
                '2026-09-27',
            )
            self.assertEqual(
                page.locator('[name=source_detail_2]').input_value(),
                'Ana, Carlos, Diana',
            )

            page.locator('#mesa-new-meeting').click()

            self.assertEqual(
                page.locator('[name=source_detail_1]').input_value(),
                '',
            )
            self.assertEqual(
                page.locator('[name=source_detail_2]').input_value(),
                '',
            )
            self.assertIsNone(
                page.evaluate(
                    "sessionStorage.getItem('lesico:mesa-reunion:1')"
                )
            )

            browser.close()


if __name__ == '__main__':
    unittest.main()
