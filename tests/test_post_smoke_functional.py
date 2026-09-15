"""Post-smoke regressions against a disposable synthetic database."""
import json
import re
import unittest
from flask import g, render_template
from playwright.sync_api import sync_playwright
from werkzeug.datastructures import MultiDict

from tests import test_alternative_routes as fixtures
from tests.form_client import hidden
from functional_presentation import preview_rows, has_label_changes


class PostSmokeFunctionalTests(unittest.TestCase):
    def setUp(self):
        fixtures.AlternativeRouteTests.setUp(self)
        self.client.application.add_url_rule('/trabajo', endpoint='main.trabajo', view_func=lambda: '')
    tearDown = fixtures.AlternativeRouteTests.tearDown
    connect = fixtures.AlternativeRouteTests.connect

    def snapshot(self):
        db = self.connect()
        result = '\n'.join(db.iterdump())
        db.close()
        return result

    def test_morphology_errors_preserve_form_and_write_nothing(self):
        base = '/ocurrencias/2/clasificar'
        for suffix in ('', '/aceptacion-inmediata/preview', '/aceptacion-inmediata/confirmar'):
            for count in ('', '2'):
                with self.subTest(suffix=suffix, count=count):
                    data = dict(proposal_kind='NEW', phonological_relation_answer='NO',
                                morphology_component_count=count, free_permutation='SIN INFORMACIÓN',
                                analysis_note='Análisis conservado <literal>', morphology_note='Observación conservada',
                                record_components='yes', component_row_id='7', component_7_position='1',
                                component_7_type='unapproved', component_7_alternative_id='', component_7_note='',
                                review_note='Motivo conservado', confirm_immediate='yes')
                    before = self.snapshot()
                    response = self.client.post(base + suffix, data=data)
                    self.assertEqual(response.status_code, 400)
                    page = response.text
                    self.assertIn('role="alert"', page)
                    self.assertIn('requiere una nota', page)
                    self.assertIn('Análisis conservado &lt;literal&gt;', page)
                    self.assertIn('Observación conservada', page)
                    self.assertIn('Motivo conservado', page)
                    self.assertIn('action="/ocurrencias/2/clasificar"', page)
                    saved = json.loads(re.search(r'const savedValues = (.*);', page)[1])
                    for key, value in data.items():
                        self.assertEqual(saved[key], [value])
                    self.assertEqual(before, self.snapshot())

    def test_canonical_morphology_error_is_visible_in_both_submission_paths(self):
        for suffix in ('', '/aceptacion-inmediata/preview'):
            for data, message in (({'morphology_component_count':'', 'free_permutation':'SIN INFORMACIÓN'}, 'seleccione la cantidad de componentes o N/A'),
                                  ({'morphology_component_count':'2', 'free_permutation':''}, 'Seleccione SÍ, NO o SIN INFORMACIÓN')):
                with self.subTest(suffix=suffix, data=data):
                    before = self.snapshot()
                    response = self.client.post('/ocurrencias/2/clasificar' + suffix, data=dict(
                        proposal_kind='NEW', phonological_relation_answer='NO', analysis_note='Persistente', **data))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(message, response.text)
                    self.assertIn('Persistente', response.text)
                    self.assertEqual(before, self.snapshot())

    def test_concepts_and_retired_alternatives_are_separate_and_selectable_correctly(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('EMPTY')")
        db.execute("INSERT INTO concept(preferred_label) VALUES('RETIRED-ONLY')")
        db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(1,'RETIRED-LABEL','2020-01-01')")
        db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(3,'HISTORICAL','2020-01-01')")
        db.commit(); db.close()
        before = self.snapshot()
        page = self.client.get('/conceptos/1/alternativas').text
        active, retired = page.split('<section id="retired-alternatives">')
        self.assertIn('<details><summary>Alternativas retiradas (1)</summary>', retired)
        self.assertNotRegex(retired, r'<details[^>]*\bopen\b')
        self.assertNotIn('RETIRED-LABEL', active)
        self.assertIn('Última denominación:', retired)
        self.assertIn('TEST-RETIRED-LABEL', retired)
        self.assertIn('Estado: Retirada', retired)
        self.assertIn('ID de alternativa: 2', retired)
        page = self.client.get('/conceptos').text
        active, empty = page.split('<section id="empty-concepts">')
        self.assertNotIn('EMPTY', active)
        self.assertNotIn('RETIRED ONLY', active)
        self.assertIn('EMPTY', empty)
        self.assertIn('RETIRED ONLY', empty)
        page = self.client.get('/alternativas/1/gestionar').text
        selector = re.search(r'<select name="destination_concept_id".*?</select>', page, re.S)[0]
        self.assertIn('<option value="">— Selecciona un concepto destino —</option>', selector)
        self.assertNotIn('value="1"', selector)
        self.assertIn('EMPTY — sin alternativas vigentes', selector)
        self.assertIn('RETIRED-ONLY — sin alternativas vigentes', selector)
        for select in re.findall(r'<select.*?</select>', page, re.S):
            self.assertNotIn('RETIRED-LABEL', select)
        page = self.client.get('/ocurrencias/2/clasificar').text
        for select in re.findall(r'<select.*?</select>', page, re.S):
            self.assertNotIn('RETIRED-LABEL', select)
            self.assertNotIn('HISTORICAL', select)
        self.assertEqual(before, self.snapshot())

    def test_empty_and_current_destinations_are_blocked_for_individual_and_group(self):
        for action in ('preview_move', 'preview_component_move', 'confirm_move', 'confirm_component_move'):
            for destination in (None, '', '1'):
                with self.subTest(action=action, destination=destination):
                    data = {'action':action, 'confirm':'yes', 'reason':'Motivo'}
                    if destination is not None:
                        data['destination_concept_id'] = destination
                    before = self.snapshot()
                    response = self.client.post('/alternativas/1/gestionar', data=data)
                    self.assertIn(response.status_code, (400, 409))
                    if not destination:
                        self.assertIn('Seleccione un concepto destino.', response.text)
                    self.assertEqual(before, self.snapshot())

    def test_group_destination_starts_empty_and_empty_concept_can_be_resolved(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('EMPTY')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2a')")
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        db.commit(); db.close()
        page = self.client.get('/alternativas/1/gestionar').text
        self.assertIn('value="preview_component_move"', page)
        selector = re.search(r'<select name="destination_concept_id".*?</select>', page, re.S)[0]
        self.assertIn('<option value="">— Selecciona un concepto destino —</option>', selector)
        self.assertNotIn('value="1"', selector)
        self.assertIn('EMPTY — sin alternativas vigentes', selector)
        response = self.client.post('/ocurrencias/2/clasificar', data=dict(proposal_kind='NEW',
            phonological_relation_answer='NO', morphology_component_count='N/A'))
        self.assertEqual(response.status_code, 302)
        page = self.client.get('/aportes/1').text
        selector = re.search(r'<select name="concept_id".*?</select>', page, re.S)[0]
        self.assertIn('EMPTY — sin alternativas vigentes', selector)
        response = self.client.post('/aportes/1/concepto', data=dict(concept_action='USE_EXISTING',
            concept_id='2', concept_note='Corresponde al concepto sin alternativas vigentes',
            concept_edit_token=hidden(page, 'concept_edit_token')))
        self.assertEqual(response.status_code, 302, response.text)
        db = self.connect()
        self.assertEqual(db.execute('SELECT concept_id FROM submission_concept_resolution WHERE is_current=1').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT count(*) FROM concept').fetchone()[0], 2)
        db.close()

    def test_reference_basis_is_translated_only_in_presentation(self):
        db = self.connect()
        db.execute('UPDATE occurrence SET occurrence_year=NULL WHERE occurrence_id=1')
        db.commit(); db.close()
        before = self.snapshot()
        page = self.client.get('/alternativas/1/gestionar').text
        self.assertIn('año único de la fuente', page)
        self.assertNotIn('source_single_year', page)
        self.assertEqual(before, self.snapshot())

    def test_move_preview_and_confirmation_preserve_validation(self):
        db = self.connect()
        db.execute("UPDATE alternative SET working_label='1a'")
        db.execute("INSERT INTO concept(preferred_label) VALUES('EMPTY')")
        db.commit(); db.close()
        before = self.snapshot()
        response = self.client.post('/alternativas/1/gestionar', data={'action':'preview_move', 'destination_concept_id':'2'})
        self.assertEqual(response.status_code, 200)
        page = response.text
        for value in ('VISTA PREVIA DEL TRASLADO', 'Origen: TEST', 'Destino: EMPTY',
                      'Alternativa trasladada: TEST-1a · ID 1', 'Ocurrencias conservadas: 1',
                      'Relaciones afectadas: Ninguna', 'Morfología/media: se conservan',
                      'VISTA PREVIA DE CAMBIOS — ORIGEN', 'VISTA PREVIA DE CAMBIOS — DESTINO',
                      'Sin cambios de nomenclatura.', 'Confirmar traslado', 'for="structural-reason">Motivo'):
            self.assertIn(value, page)
        self.assertNotIn('>Alternative<', page)
        self.assertEqual(before, self.snapshot())
        form = page.split('value="confirm_move"')[1].split('</form>')[0]
        self.assertNotIn('type="hidden" name="confirm"', form)
        self.assertIn('type="checkbox" name="confirm" value="yes" required', form)
        self.assertIn('</label></p><p><label', form)
        data = dict(action='confirm_move', destination_concept_id='2', preview_token=hidden(page, 'preview_token'), reason='Traslado')
        self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 400)
        data['confirm'] = 'yes'
        self.assertEqual(self.client.post('/alternativas/1/gestionar', data=dict(data, reason='')).status_code, 400)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 302)
        self.assertIn('Traslado de alternativa', self.client.get('/alternativas/1/gestionar').text)

    def test_preview_sort_is_natural_stable_and_does_not_mutate_calculation(self):
        rows = [dict(alternative_id=i, current_label=label, proposed_label='1a')
                for i, label in enumerate((None, '10a', '2a', '1b', '1a', '1a', 'legacy'), 1)]
        original = json.dumps(rows)
        self.assertEqual([r['alternative_id'] for r in preview_rows(rows)], [5,6,4,3,2,7,1])
        self.assertEqual(json.dumps(rows), original)
        self.assertTrue(has_label_changes(rows))
        self.assertFalse(has_label_changes([dict(current_label='1a', proposed_label='1a')]))

    def test_all_structural_tables_sort_by_current_label_and_translate_titles(self):
        from routes.alternatives import _management_context
        rows = [dict(alternative_id=i, current_label=label, proposed_label='9a')
                for i, label in ((71, None), (72, '10a'), (73, '2a'), (74, '1b'), (75, '1a'))]
        rows[-1]['proposed_label'] = '1a'
        rows[-2]['proposed_label'] = '1c'
        with self.client.application.test_request_context():
            g.current_access_role = 'reviewer'
            db = self.connect()
            context = _management_context(db, 1)
            db.close()
            for kind, title in (('merge', 'Vista previa de la fusión'), ('retire', 'Vista previa del retiro'),
                                ('split', 'Vista previa de la división'), ('move', 'VISTA PREVIA DEL TRASLADO'),
                                ('component_move', 'TRASLADO DE GRUPO')):
                with self.subTest(kind=kind):
                    context['structural_result'] = dict(kind=kind, source=dict(concept_label='TEST', working_label='1a', alternative_id=1),
                        destination=dict(preferred_label='DESTINO', concept_id=2), labels=rows,
                        origin_labels=rows, destination_labels=rows, conflicts=dict(blocking=[], non_blocking=[]),
                        occurrences=[], relations=[], alternatives=[], token='preview')
                    page = render_template('gestionar_alternativa.html', **context)
                    self.assertIn(title, page)
                    structural = page.split('<section id="estructurales"')[1]
                    tables = re.findall(r'<table>.*?</table>', structural, re.S)
                    self.assertEqual(len(tables), 2 if kind in ('move','component_move') else 1)
                    for table in tables:
                        for status in ('= Sin cambio', '↻ Cambia', '↻ Cambia de grupo', '+ Nueva', '<th>Estado</th>'):
                            self.assertIn(status, table)
                        ids = [int(value) for value in re.findall(r'<tr><td>(?:ID )?(\d+)</td>', table)]
                        self.assertEqual(ids, [75,74,73,72,71])
                    self.assertNotIn('Sin cambios de nomenclatura.', structural)

    def test_classification_reference_and_current_assignment_are_independent(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('ACTUAL')")
        db.execute("UPDATE alternative SET concept_id=2, working_label='6b' WHERE alternative_id=1")
        db.commit(); db.close()
        before = self.snapshot()
        page = self.client.get('/ocurrencias/1/clasificar')
        self.assertEqual(page.status_code, 200)
        self.assertIn('<h2>Concepto de referencia</h2><p>TEST</p>', page.text)
        self.assertIn('<h2>Clasificación vigente</h2><p>ACTUAL-6b · ID 1</p>', page.text)
        self.assertIn('Alternativas vigentes del concepto de referencia', page.text)
        self.assertNotIn('No fue posible guardar el análisis', page.text)
        self.assertIn('Sin clasificación vigente', self.client.get('/ocurrencias/2/clasificar').text)
        self.assertEqual(before, self.snapshot())

    def test_occurrence_terminology_and_no_empty_retired_section(self):
        page = self.client.get('/ocurrencias').text
        self.assertIn('Registrar nueva ocurrencia', page)
        self.assertNotIn('Registrar nueva evidencia', page)
        self.assertNotIn('retired-alternatives', self.client.get('/conceptos/1/alternativas').text)

    def test_new_preview_rows_follow_proposed_label_without_mutation(self):
        rows = [dict(alternative_id=i, current_label=None, proposed_label=label)
                for i, label in ((1, '10a'), (2, '2b'), (3, '2a'))]
        before = json.dumps(rows)
        self.assertEqual([r['alternative_id'] for r in preview_rows(rows)], [3, 2, 1])
        self.assertEqual(before, json.dumps(rows))

    def test_retired_proposed_relation_is_explicit_and_cannot_be_accepted(self):
        response = self.client.post('/ocurrencias/2/clasificar', data=dict(
            proposal_kind='NEW', phonological_relation_answer='YES',
            relation_target_type_0='alternative', relation_alternative_id='1',
            relation_parameter='CM_1', morphology_component_count='N/A'))
        self.assertEqual(response.status_code, 302, response.text)
        db = self.connect()
        from submission_concept_resolution import save_resolution
        save_resolution(db, 1, 'CONFIRM_REFERENCE', access_role='reviewer')
        db.execute("UPDATE assignment SET is_current=0 WHERE alternative_id=1")
        db.execute("UPDATE alternative SET retired_at='2026-01-01' WHERE alternative_id=1")
        db.commit(); db.close()
        before = self.snapshot()
        page = self.client.get('/aportes/1').text
        message = 'No se puede aceptar esta relación porque la Alternativa destino ID 1 fue retirada.'
        self.assertIn(message, page)
        self.assertIn('value="ACCEPTED" data-blocked="true" disabled', page)
        response = self.client.post('/aportes/1/decidir', data=dict(
            decision='new', relations_resolution='ACCEPTED', morphology_resolution='ACCEPTED',
            lexical_preview_token=hidden(page, 'lexical_preview_token')))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn(message, response.text)
        self.assertEqual(before, self.snapshot())

    def test_invalid_relation_messages_distinguish_missing_and_other_concept(self):
        from functional_presentation import relation_target_error
        db = self.connect()
        self.assertIn('ID 999 no existe', relation_target_error(db, 999, 1))
        self.assertIn('ID 1 pertenece a otro concepto', relation_target_error(db, 1, 2))
        db.close()

    def test_browser_morphology_error_keeps_dynamic_inputs_and_can_be_corrected(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for suffix in ('', '/aceptacion-inmediata/preview'):
                with self.subTest(suffix=suffix):
                    base = '/ocurrencias/2/clasificar'
                    page.set_content(self.client.get(base).text)
                    page.locator('[name=proposal_kind][value=NEW]').check()
                    page.locator('[name=phonological_relation_answer]').select_option('YES')
                    page.locator('[name=relation_alternative_id]').select_option('1')
                    page.locator('[name=relation_parameter]').select_option('CM_1')
                    page.locator('[name=relation_uncertain]').check()
                    page.locator('[name=morphology_component_count]').select_option('2')
                    page.locator('[name=free_permutation]').select_option('SÍ')
                    page.locator('[name=record_components][value=yes]').check()
                    page.locator('[name=component_0_type][value=existing]').check()
                    page.locator('[name=component_0_alternative_id]').select_option('1')
                    page.locator('[name=component_0_note]').fill('Nota del primero')
                    page.locator('#add-component').click()
                    page.locator('[name=component_1_type][value=unapproved]').check()
                    page.locator('[name=analysis_note]').fill('Análisis íntegro')
                    page.locator('[name=morphology_note]').fill('Observación íntegra')
                    page.locator('[name=review_note]').fill('Revisión íntegra')
                    data = MultiDict(page.locator('form').evaluate('(form)=>[...new FormData(form)]'))
                    before = self.snapshot()
                    response = self.client.post(base + suffix, data=data)
                    self.assertEqual(response.status_code, 400)
                    page.set_content(response.text)
                    self.assertTrue(page.locator('section[role=alert]').is_visible())
                    self.assertIn('requiere una nota', page.locator('section[role=alert]').inner_text())
                    restored = MultiDict(page.locator('form').evaluate('(form)=>[...new FormData(form)]'))
                    self.assertEqual(list(data.lists()), list(restored.lists()))
                    self.assertEqual(before, self.snapshot())
                    page.locator('[name=component_1_note]').fill('Componente con dudas')
                    repaired = MultiDict(page.locator('form').evaluate('(form)=>[...new FormData(form)]'))
                    # Preview exercises canonical validation and still rolls back every write.
                    response = self.client.post(base + '/aceptacion-inmediata/preview', data=repaired)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(before, self.snapshot())
            self.assertEqual(errors, [])
            browser.close()

    def test_browser_missing_morphology_shows_server_error_for_both_buttons(self):
        from urllib.parse import urlsplit
        from werkzeug.urls import uri_to_iri
        from urllib.parse import parse_qsl
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            def serve(route):
                request = route.request
                path = uri_to_iri(urlsplit(request.url).path)
                if request.method == 'POST':
                    response = self.client.post(path, data=MultiDict(parse_qsl(request.post_data or '', keep_blank_values=True)))
                else:
                    response = self.client.get(path)
                route.fulfill(status=response.status_code, content_type='text/html; charset=utf-8', body=response.data)
            page.route('http://post-smoke.test/**', serve)
            for immediate in (False, True):
                with self.subTest(immediate=immediate):
                    before = self.snapshot()
                    page.goto('http://post-smoke.test/ocurrencias/2/clasificar')
                    page.locator('[name=proposal_kind][value=NEW]').check()
                    page.locator('[name=phonological_relation_answer]').select_option('NO')
                    page.locator('[name=analysis_note]').fill('No perder')
                    button = page.locator('#immediate-confirm') if immediate else page.get_by_role('button', name='Mandar a revisión', exact=True)
                    button.click()
                    page.wait_for_selector('section[role=alert]')
                    self.assertIn('seleccione la cantidad de componentes o N/A', page.locator('section[role=alert]').inner_text())
                    self.assertEqual(page.locator('[name=analysis_note]').input_value(), 'No perder')
                    self.assertTrue(page.locator('[name=proposal_kind][value=NEW]').is_checked())
                    self.assertEqual(before, self.snapshot())
            browser.close()


if __name__ == '__main__':
    unittest.main()
