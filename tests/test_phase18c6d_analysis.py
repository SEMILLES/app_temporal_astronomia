"""Single analysis form, group relations and role-protected read-only detail."""
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.datastructures import MultiDict

from access_control import install_access_context
from tests import test_immediate_acceptance_routes as fixtures


class AnalysisFlowTests(unittest.TestCase):
    setUp = fixtures.ImmediateAcceptanceRouteTests.setUp
    tearDown = fixtures.ImmediateAcceptanceRouteTests.tearDown
    connect = fixtures.ImmediateAcceptanceRouteTests.connect
    base = '/ocurrencias/1/clasificar/aceptacion-inmediata/'

    @contextmanager
    def database(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def payload(self, **changes):
        data = dict(proposal_kind='NEW', phonological_relation_answer='YES',
                    relation_target_type=['alternative', 'alternative'],
                    relation_target_id=['1', '1'], relation_parameter=['CM_1', 'N_MANOS'],
                    relation_uncertain=['1'], morphology_component_count='N/A',
                    immediate_mode='as_proposed', confirm_immediate='yes', collaborator_id='1')
        data.update(changes)
        return data

    def dump(self):
        with self.database() as db:
            return '\n'.join(db.iterdump())

    def test_existing_inherits_without_note_and_preserves_proposal(self):
        self.role = 'reviewer'
        data = dict(proposal_kind='EXISTING', proposed_existing_alternative_id='1',
                    immediate_mode='as_proposed', confirm_immediate='yes')
        preview = self.client.post(self.base+'preview', data=data)
        self.assertEqual(200, preview.status_code)
        self.assertIn('Se acepta el análisis tal como fue registrado', preview.text)
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=data).status_code)
        with self.database() as db:
            self.assertEqual(('USE_EXISTING', 1, 'NOT_PROPOSED', 'NOT_PROPOSED'), tuple(db.execute(
                'SELECT decision_action,resolved_alternative_id,relations_resolution,morphology_resolution FROM submission_lexical_decision').fetchone()))
            self.assertEqual(('EXISTING', 1), tuple(db.execute(
                'SELECT proposal_kind,proposed_existing_alternative_id FROM alternative_submission').fetchone()))

    def test_new_inherits_all_relations_morphology_and_preview(self):
        self.role = 'master'
        before = self.dump()
        preview = self.client.post(self.base+'preview', data=self.payload())
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertIn('CM_1', preview.text)
        self.assertIn('N_MANOS', preview.text)
        self.assertIn('Vista previa de solo lectura', preview.text)
        self.assertEqual(before, self.dump())
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=self.payload()).status_code)
        with self.database() as db:
            self.assertEqual(('CREATE_NEW', 'ACCEPTED', 'ACCEPTED'), tuple(db.execute(
                'SELECT decision_action,relations_resolution,morphology_resolution FROM submission_lexical_decision').fetchone()))
            self.assertEqual([('CM_1', 0), ('N_MANOS', 1)], [tuple(row) for row in db.execute(
                'SELECT phonological_parameter,uncertain FROM alternative_submission_relation ORDER BY alternative_submission_relation_id')])
            self.assertEqual(2, db.execute('SELECT count(*) FROM alternative_relation WHERE is_current=1').fetchone()[0])
        detail = self.client.get('/aportes/1').text
        self.assertIn('CM_1', detail)
        self.assertIn('N_MANOS', detail)
        self.assertIn('Con duda', detail)

    def test_new_without_relations_and_unsure_requires_explicit_resolution(self):
        self.role = 'reviewer'
        data = self.payload(phonological_relation_answer='NO')
        response = self.client.post(self.base+'preview', data=data)
        self.assertEqual(200, response.status_code, response.text)
        for endpoint in ('preview', 'confirmar'):
            data = dict(proposal_kind='UNSURE', analysis_note='Duda original',
                        immediate_mode='as_proposed', confirm_immediate='yes')
            response = self.client.post(self.base+endpoint, data=data)
            self.assertEqual(400, response.status_code)
            self.assertIn('es necesario resolver la clasificación', response.text)
            data.update(immediate_mode='modify', canonical_decision='existing', canonical_alternative_id='1')
            self.assertEqual(400, self.client.post(self.base+endpoint, data=data).status_code)
        data['review_note'] = 'Resuelvo la duda'
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=data).status_code)

    def test_preview_passes_every_target_to_existing_nomenclature_algorithm(self):
        from alternative_workflow import calculate_nomenclature_preview
        self.role = 'reviewer'
        with self.database() as db:
            db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')")
        data = self.payload(relation_target_id=['1', '2'])
        before = self.dump()
        with patch('alternative_workflow.calculate_nomenclature_preview', wraps=calculate_nomenclature_preview) as calculate:
            response = self.client.post(self.base+'preview', data=data)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([1, 2], [target for _, target in calculate.call_args.kwargs['extra_edges']])
        self.assertEqual(before, self.dump())
        data.update(immediate_mode='modify', canonical_decision='new', relations_resolution='REJECTED',
                    morphology_resolution='ACCEPTED', review_note='Descarto las relaciones')
        with patch('alternative_workflow.calculate_nomenclature_preview', wraps=calculate_nomenclature_preview) as calculate:
            response = self.client.post(self.base+'preview', data=data)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([], calculate.call_args.kwargs['extra_edges'])
        self.assertEqual(before, self.dump())

    def test_modify_existing_destination_requires_note(self):
        self.role = 'reviewer'
        with self.database() as db:
            db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')")
        data = dict(proposal_kind='EXISTING', proposed_existing_alternative_id='1',
                    immediate_mode='modify', canonical_decision='existing', canonical_alternative_id='2', confirm_immediate='yes')
        before = self.dump()
        self.assertEqual(400, self.client.post(self.base+'confirmar', data=data).status_code)
        self.assertEqual(before, self.dump())
        data['review_note'] = 'Elijo la otra alternativa'
        response = self.client.post(self.base+'preview', data=data)
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('El Revisor modificó la decisión antes de aceptar', response.text)
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=data).status_code)
        with self.database() as db:
            self.assertEqual(1, db.execute('SELECT proposed_existing_alternative_id FROM alternative_submission').fetchone()[0])
            self.assertEqual(2, db.execute('SELECT resolved_alternative_id FROM submission_lexical_decision').fetchone()[0])

    def test_group_pending_or_omitted_has_no_effects(self):
        self.role = 'reviewer'
        before = self.dump()
        for resolution in (None, 'pending', 'PENDING'):
            data = self.payload(immediate_mode='modify', canonical_decision='new', morphology_resolution='ACCEPTED')
            if resolution is not None:
                data['relations_resolution'] = resolution
            for endpoint in ('preview', 'confirmar'):
                self.assertEqual(400, self.client.post(self.base+endpoint, data=data).status_code)
                self.assertEqual(before, self.dump())

    def test_rejected_group_does_not_materialize(self):
        self.role = 'reviewer'
        data = self.payload(immediate_mode='modify', canonical_decision='new',
                            relations_resolution='REJECTED', morphology_resolution='ACCEPTED')
        self.assertEqual(400, self.client.post(self.base+'confirmar', data=data).status_code)
        data['review_note'] = 'No hay relación'
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=data).status_code)
        with self.database() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])
            self.assertEqual(2, db.execute('SELECT count(*) FROM alternative_submission_relation').fetchone()[0])

    def test_modify_new_to_existing_rejects_both_groups(self):
        self.role = 'master'
        data = self.payload(immediate_mode='modify', canonical_decision='existing', canonical_alternative_id='1',
                            relations_resolution='REJECTED', morphology_resolution='REJECTED')
        self.assertEqual(400, self.client.post(self.base+'confirmar', data=data).status_code)
        data['review_note'] = 'Uso la alternativa existente'
        self.assertEqual(302, self.client.post(self.base+'confirmar', data=data).status_code)
        with self.database() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT count(*) FROM alternative_morphology').fetchone()[0])

    def test_incomplete_duplicate_or_misaligned_relations_are_rejected(self):
        self.role = 'reviewer'
        before = self.dump()
        for changes, message in [
            (dict(relation_target_type=[], relation_target_id=[], relation_parameter=[]), 'al menos una relación completa'),
            (dict(relation_parameter=['CM_1', '']), 'destino y un parámetro'),
            (dict(relation_target_id=['1', '']), 'destino y un parámetro'),
            (dict(relation_parameter=['CM_1']), 'al menos una relación completa'),
            (dict(relation_parameter=['CM_1', 'CM_1']), 'duplicada'),
        ]:
            for url in ('/ocurrencias/1/clasificar', self.base+'preview', self.base+'confirmar'):
                with self.subTest(url=url, changes=changes):
                    response = self.client.post(url, data=self.payload(**changes))
                    self.assertEqual(400, response.status_code)
                    self.assertIn(message, response.text)
                    self.assertEqual(before, self.dump())

    def test_normal_submission_one_and_multiple_relations(self):
        self.role = 'analyst'
        data = self.payload(relation_target_type=['alternative'], relation_target_id=['1'], relation_parameter=['CM_1'])
        self.assertEqual(302, self.client.post('/ocurrencias/1/clasificar', data=data).status_code)
        with self.database() as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM alternative_submission_relation').fetchone()[0])

    def test_analyst_prefixed_detail_history_and_mutations(self):
        self.role = 'reviewer'
        install_access_context(self.app)
        with patch.dict(os.environ, LESICO_ANALYST_ROUTE='analysis', LESICO_REVIEWER_ROUTE='review', LESICO_MASTER_ROUTE='master'), patch('access_control.conectar', side_effect=self.connect):
            client = self.app.test_client()
            self.assertEqual(302, client.post('/analysis/ocurrencias/1/clasificar', data=self.payload()).status_code)
            listing = client.get('/analysis/aportes')
            self.assertIn('href="/analysis/aportes/1"', listing.text)
            before = self.dump()
            for role in ('analysis', 'review', 'master'):
                detail = client.get('/'+role+'/aportes/1')
                self.assertEqual(200, detail.status_code)
                self.assertIn('CM_1', detail.text)
                self.assertIn('N_MANOS', detail.text)
                self.assertEqual(role != 'analysis', 'class="review-decision"' in detail.text)
                self.assertEqual(role != 'analysis', 'class="concept-resolution-form"' in detail.text)
            for path in ('/aportes/1/decidir', '/aportes/1/concepto', self.base+'confirmar'):
                self.assertEqual(404, client.post('/analysis'+path, data=self.payload()).status_code)
            self.assertEqual(before, self.dump())

            # Resolve through the same backend used for immediate acceptance.
            from submission_concept_resolution import save_resolution
            from alternative_workflow import review_as_new
            with self.database() as db:
                save_resolution(db, 1, 'CONFIRM_REFERENCE', access_role='reviewer')
                review_as_new(db, 1, relations_resolution='ACCEPTED', morphology_resolution='ACCEPTED', access_role='reviewer')
            before = self.dump()
            detail = client.get('/analysis/aportes/1')
            self.assertEqual(200, detail.status_code)
            self.assertIn('RESULTADO HISTÓRICO AL DECIDIR', detail.text)
            self.assertIn('Aceptado', detail.text)
            self.assertNotRegex(detail.text, r'<form\b')
            self.assertEqual(before, self.dump())

    def test_analyst_grammar_detail_has_no_review_form(self):
        self.role = 'analyst'
        install_access_context(self.app)
        with patch.dict(os.environ, LESICO_ANALYST_ROUTE='analysis'), patch('access_control.conectar', side_effect=self.connect):
            client = self.app.test_client()
            from grammar_workflow import create_grammar_submission
            with self.database() as db:
                create_grammar_submission(db, 1, {'gender':'FEM-A'})
            before = self.dump()
            response = client.get('/analysis/aportes/1')
            self.assertEqual(200, response.status_code)
            self.assertIn('FEM-A', response.text)
            self.assertNotRegex(response.text, r'<form\b')
            self.assertEqual(404, client.post('/analysis/aportes/1/decidir', data={'decision':'accepted'}).status_code)
            self.assertEqual(before, self.dump())

    def test_browser_form_inheritance_relations_validation_and_modify(self):
        self.role = 'reviewer'
        html = self.client.get('/ocurrencias/1/clasificar').text
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(html)
            page.locator('#immediate-confirm').click()
            self.assertIn('Seleccione la decisión principal', page.locator('form > p[role=alert]').inner_text())
            page.locator('[name=proposal_kind][value=NEW]').check()
            page.locator('[name=morphology_component_count]').select_option('N/A')
            page.locator('[name=phonological_relation_answer]').select_option('YES')
            page.locator('#immediate-confirm').click()
            self.assertIn('destino y un parámetro', page.locator('form > p[role=alert]').inner_text())
            page.locator('[name=relation_alternative_id]').select_option('1')
            page.locator('[name=relation_parameter]').select_option('CM_1')
            page.locator('#add-relation').click()
            page.locator('[name=relation_alternative_id]').nth(1).select_option('1')
            page.locator('[name=relation_parameter]').nth(1).select_option('N_MANOS')
            page.locator('[name=relation_uncertain]').nth(1).check()
            data = MultiDict(page.locator('form').evaluate('(form)=>[...new FormData(form)]'))
            self.assertEqual(['1'], data.getlist('relation_uncertain'))
            self.assertEqual('as_proposed', data['immediate_mode'])
            self.assertNotIn('canonical_decision', data)
            response = self.client.post(self.base+'preview', data=data)
            self.assertEqual(200, response.status_code, response.text)
            page.locator('#modify-immediate summary').click()
            page.locator('#canonical-decision').select_option('existing')
            page.locator('[name=canonical_alternative_id]').select_option('1')
            self.assertTrue(page.locator('#immediate-review-note').evaluate('(e)=>e.required'))
            page.locator('#modify-immediate summary').click()
            self.assertFalse(page.locator('#immediate-review-note').evaluate('(e)=>e.required'))
            page.locator('[name=phonological_relation_answer]').select_option('NO')
            data = MultiDict(page.locator('form').evaluate('(form)=>[...new FormData(form)]'))
            self.assertNotIn('relation_parameter', data)
            page.locator('[name=phonological_relation_answer]').select_option('YES')
            page.locator('.remove-relation').nth(1).click()
            self.assertEqual(1, page.locator('.relation').count())
            page.locator('[name=proposal_kind][value=UNSURE]').check()
            self.assertTrue(page.locator('#modify-immediate').evaluate('(e)=>e.open'))
            self.assertEqual('', page.locator('#canonical-decision').input_value())
            self.assertEqual([], errors)
