import re
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests import test_occurrence_grammar_routes as grammar
from occurrence_grammar import create_or_replace_occurrence_grammar
from occurrence_registration import save_draft


class RegistrationFlowTests(unittest.TestCase):
    tearDown = grammar.GrammarWorkflowRouteTests.tearDown
    connect = grammar.GrammarWorkflowRouteTests.connect

    def setUp(self):
        grammar.GrammarWorkflowRouteTests.setUp(self)
        self.client.application.add_url_rule('/trabajo', endpoint='main.trabajo', view_func=lambda: '')

    def snapshot(self):
        db = self.connect()
        result = '\n'.join(db.iterdump())
        db.close()
        return result

    def test_registration_redirects_to_grammar(self):
        response = self.client.post('/aportes', data={
            'source_id': '1', 'original_gloss': 'NEW',
            'reference_kind': 'concept', 'reference_concept_id': '1',
            'source_detail_1_status': 'UNKNOWN', 'source_detail_2_status': 'NA',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/ocurrencias/2/gramatica?flow=registration')

    def test_skip_links_do_not_write_any_table(self):
        before = self.snapshot()
        for path, step, label, target in [
            ('gramatica?flow=registration', 'Paso 2 de 4 — Gramática',
             'Revisar gramática después', '/ocurrencias/1/clasificar?flow=registration'),
            ('clasificar?flow=registration', 'Paso 3 de 4 — Análisis léxico',
             'Revisar análisis después', '/ocurrencias/1/resumen'),
        ]:
            html = self.client.get('/ocurrencias/1/' + path).get_data(as_text=True)
            self.assertIn(step, html)
            self.assertRegex(
                html,
                rf'href="{re.escape(target)}">\s*{re.escape(label)}\s*</a>',
            )
            self.assertEqual(self.client.get(target).status_code, 200)
            self.assertEqual(self.snapshot(), before)

    def test_draft_keeps_explicit_states_and_completion_enters_flow(self):
        db = self.connect()
        draft_id = save_draft(db, source_id=1, original_gloss='DRAFT',
                              reference_concept_id=1, source_detail_1_status='UNKNOWN',
                              source_detail_2_status='NA')
        db.close()
        html = self.client.get(f'/borradores/{draft_id}/editar').get_data(as_text=True)
        self.assertIn('value="UNKNOWN" selected', html)
        self.assertIn('value="NA" selected', html)
        response = self.client.post(f'/borradores/{draft_id}/completar')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/ocurrencias/2/gramatica?flow=registration')

    def test_submissions_continue_without_canonical_writes(self):
        for path, data, target in [
            ('gramatica', {'gender': 'FEM-A'}, '/ocurrencias/1/clasificar?flow=registration'),
            ('clasificar', {'proposal_kind': 'EXISTING', 'proposed_existing_alternative_id': '1'},
             '/ocurrencias/1/resumen'),
        ]:
            data['flow'] = 'registration'
            response = self.client.post('/ocurrencias/1/' + path, data=data)
            self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
            self.assertEqual(response.location, target)
        db = self.connect()
        self.assertEqual([tuple(r) for r in db.execute('SELECT submission_type,status FROM submission ORDER BY submission_id')],
                         [('GRAMMAR', 'pending'), ('ALTERNATIVE', 'pending')])
        for table in ('occurrence_grammar', 'assignment'):
            self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        db.close()
        html = self.client.get('/ocurrencias/1/resumen').get_data(as_text=True)
        self.assertEqual(html.count('Pendiente de revisión'), 2)

    def test_summary_real_current_and_unanalysed_states(self):
        html = self.client.get('/ocurrencias/1/resumen').get_data(as_text=True)
        for text in ('OCC-000001', 'EVIDENCE', 'Synthetic source', 'ASTRONOMIA',
                     'Estado de Gramática: Sin analizar', 'Sin analizar / sin clasificación'):
            self.assertIn(text, html)
        db = self.connect()
        create_or_replace_occurrence_grammar(db, 1, gender='FEM-A')
        db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        db.commit()
        db.close()
        before = self.snapshot()
        html = self.client.get('/ocurrencias/1/resumen').get_data(as_text=True)
        self.assertIn('Con análisis vigente', html)
        self.assertIn('Clasificada: 1a', html)
        self.assertEqual(self.snapshot(), before)
        db = self.connect()
        db.execute('UPDATE alternative SET working_label=NULL WHERE alternative_id=1')
        db.commit()
        db.close()
        html = self.client.get('/ocurrencias/1/resumen').get_data(as_text=True)
        self.assertIn('Estado de Análisis léxico: Clasificada</p>', html)
        self.assertEqual(self.client.get('/ocurrencias/999/resumen').status_code, 404)

    def test_direct_access_and_invalid_grammar_keep_context(self):
        for path in ('gramatica', 'clasificar'):
            html = self.client.get('/ocurrencias/1/' + path).get_data(as_text=True)
            self.assertNotIn('Paso ', html)
            self.assertNotIn('Revisar análisis después', html)
            self.assertNotIn('Revisar gramática después', html)
            self.assertIn('<button>Mandar a revisión</button>', html)
        response = self.client.post('/ocurrencias/1/gramatica?flow=registration', data={'note': 'Only note'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Paso 2 de 4', response.get_data(as_text=True))
        response = self.client.post('/ocurrencias/1/gramatica', data={'gender': 'FEM-A'})
        self.assertEqual(response.location, '/ocurrencias/1/gramatica?result=submitted')
        response = self.client.post('/ocurrencias/1/clasificar', data={
            'proposal_kind': 'EXISTING', 'proposed_existing_alternative_id': '1'})
        self.assertEqual(response.location, '/ocurrencias/1/clasificar')

    def test_browser_detail_defaults_explicit_choices_and_source_switch(self):
        db = self.connect()
        db.execute("UPDATE source SET source_type='MATERIAL_IMPRESO' WHERE source_id=1")
        db.execute("INSERT INTO source(source_name,source_type) VALUES('Video','VIDEO_POR_SENA')")
        db.commit()
        db.close()
        html = self.client.get('/aportes/nuevo').get_data(as_text=True)
        html = re.sub(r'<script src="[^"]*source-details.js"></script>', '', html)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.set_content(html)
            page.add_script_tag(content=(Path(__file__).resolve().parents[1]/'static/source-details.js').read_text(encoding='utf-8'))
            page.locator('[name=source_id]').select_option('1')
            for number in (1, 2):
                state = page.locator(f'[name=source_detail_{number}_status]')
                value = page.locator(f'[name=source_detail_{number}]')
                self.assertEqual(state.input_value(), 'VALUE')
                self.assertTrue(value.evaluate('node => node.required'))
                for choice in ('NA', 'UNKNOWN'):
                    state.select_option(choice)
                    self.assertTrue(value.is_disabled())
                state.select_option('VALUE')
            page.locator('[name=source_id]').select_option('2')
            self.assertEqual(page.locator('[name=source_detail_1_status]').input_value(), 'VALUE')
            self.assertEqual(page.locator('[name=source_detail_2_status]').input_value(), 'NA')
            self.assertTrue(page.locator('[data-detail="2"]').is_hidden())
            page.locator('[name=source_detail_1_status]').select_option('UNKNOWN')
            page.locator('[name=original_gloss]').fill('VIDEO')
            page.locator('[name=original_gloss]').dispatch_event('change')
            self.assertEqual(page.locator('[name=source_detail_1_status]').input_value(), 'UNKNOWN')
            page.locator('[name=source_id]').select_option('1')
            self.assertEqual(page.locator('[name=source_detail_2_status]').input_value(), 'VALUE')
            browser.close()
