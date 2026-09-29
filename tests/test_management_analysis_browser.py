"""Real browser checks against Flask fixtures; no server or persistent DB."""
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.datastructures import MultiDict
from tests import test_management_analysis as management
from tests import test_alternative_routes as classification

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ui():
    fixture = management.ManagementAnalysisTests()
    fixture.setUp()
    errors = []
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())

            def respond(route):
                request = route.request
                path = request.url.removeprefix('http://lesico.test')
                if path.startswith('/static/'):
                    route.fulfill(path=str(ROOT / path.lstrip('/')))
                else:
                    result = (fixture.client.post(path, data=MultiDict(parse_qsl(request.post_data or '', keep_blank_values=True)))
                              if request.method == 'POST' else fixture.client.get(path))
                    route.fulfill(status=result.status_code, body=result.data, headers=dict(result.headers))
            page.route('http://lesico.test/**', respond)
            yield fixture, page
            browser.close()
            assert errors == []
    finally:
        fixture.tearDown()


def posted(page):
    return page.locator('#management-morphology').evaluate('form => Object.fromEntries(new FormData(form))')


def test_count_transitions_disable_hidden_values_and_bound_rows(ui):
    fixture, page = ui
    fixture.query('DELETE FROM alternative_morphology')
    page.goto('http://lesico.test/alternativas/1/gestionar')
    count = page.locator('[name=component_count]')
    assert count.input_value() == ''
    assert not page.locator('#permutation-field').is_visible()
    assert not page.locator('#component-registration-field').is_visible()
    count.select_option('4')
    assert page.locator('#permutation-field').is_visible()
    assert page.locator('[name=free_permutation]').input_value() == 'SIN INFORMACIÓN'
    page.locator('[name=record_components][value=yes]').check()
    assert page.locator('#components > fieldset').count() == 1
    page.locator('[name=component_0_type][value=unapproved]').check()
    assert page.locator('[name=component_0_note]').evaluate('x => x.required')
    page.locator('[name=component_0_note]').fill('Identificado parcialmente')
    page.locator('#add-component').click()
    page.locator('#add-component').click()
    count.select_option('2')
    assert page.locator('#add-component').is_disabled()
    assert not count.evaluate('x => x.checkValidity()')
    page.locator('#remove-last-component').click()
    assert count.evaluate('x => x.checkValidity()')
    count.select_option('1')
    assert not page.locator('#permutation-field').is_visible()
    assert not page.locator('#component-registration-field').is_visible()
    values = posted(page)
    assert 'free_permutation' not in values
    assert 'record_components' not in values
    assert 'component_row_id' not in values
    assert not any(key.startswith('component_0_') for key in values)
    page.locator('#management-morphology button[type=submit], #management-morphology > button').click()
    assert fixture.current()['free_permutation'] == 'N/A'
    assert fixture.components() == []


def test_na_components_notes_preload_and_partial_save(ui):
    fixture, page = ui
    page.goto('http://lesico.test/alternativas/1/gestionar')
    assert page.locator('[name=component_count]').input_value() == 'N/A'
    assert not page.locator('#permutation-field').is_visible()
    assert page.locator('#component-registration-field').is_visible()
    page.locator('[name=record_components][value=yes]').check()
    page.locator('[name=component_0_type][value=existing]').check()
    page.locator('[name=component_0_alternative_id]').select_option('2')
    page.locator('[name=component_0_note]').fill('Nota de componente')
    page.locator('[name=morphology_note]').fill('Observación de análisis')
    page.locator('#management-morphology > button').click()
    assert len(fixture.components()) == 1
    assert page.locator('[name=component_0_alternative_id]').input_value() == '2'
    assert page.locator('[name=component_0_note]').input_value() == 'Nota de componente'
    assert page.locator('[name=morphology_note]').input_value() == 'Observación de análisis'
    assert page.locator('[name=record_components][value=yes]').is_checked()
    page.locator('[name=component_count]').select_option('4')
    page.locator('[name=free_permutation]').select_option('NO')
    page.locator('#management-morphology > button').click()
    assert fixture.current()['component_count'] == 4
    assert len(fixture.components()) == 1
    page.locator('[name=component_0_type][value=unapproved]').check()
    assert posted(page)['component_0_alternative_id'] == ''
    page.locator('[name=record_components][value=no]').check()
    assert not page.locator('#component-controls').is_visible()
    assert 'component_row_id' not in posted(page)
    page.locator('#management-morphology > button').click()
    assert fixture.components() == []
    assert len(fixture.query('SELECT * FROM alternative_component')) == 2


def test_relations_current_duplicate_validation_and_mobile(ui):
    fixture, page = ui
    assert fixture.relation(1,2).status_code == 200
    page.set_viewport_size({'width':390, 'height':844})
    page.goto('http://lesico.test/alternativas/1/gestionar')
    form = page.locator('#management-relation')
    assert form.locator('[name=target_id] option[value="1"]').count() == 0
    form.locator('[name=target_id]').select_option('2')
    form.locator('[name=parameter]').select_option('CM_1')
    assert page.locator('#relation-duplicate').is_visible()
    assert not form.evaluate('form => form.checkValidity()')
    form.locator('[name=parameter]').select_option('CM_2')
    assert not page.locator('#relation-duplicate').is_visible()
    assert form.evaluate('form => form.checkValidity()')
    for selector in ('#management-morphology', '#management-relation'):
        assert page.locator(selector).evaluate('x => x.scrollWidth <= x.clientWidth + 1')
    form.get_by_role('button', name='Previsualizar', exact=True).click()
    confirmation = page.locator('form').filter(has=page.locator('[name=action][value=confirm_relation]'))
    confirmation.get_by_role('button', name='Confirmar', exact=True).click()
    assert len(fixture.query('SELECT * FROM alternative_relation WHERE is_current=1')) == 2


def test_classification_shared_template_and_parser_still_work():
    fixture = classification.AlternativeRouteTests()
    fixture.setUp()
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(fixture.client.get('/ocurrencias/2/clasificar').text)
            page.locator('[name=proposal_kind][value=NEW]').check()
            page.locator('[name=phonological_relation_answer]').select_option('NO')
            page.locator('[name=morphology_component_count]').select_option('N/A')
            page.locator('[name=record_components][value=yes]').check()
            page.locator('[name=component_0_type][value=unapproved]').check()
            page.locator('[name=component_0_note]').fill('Componente propuesto')
            values = page.locator('form').filter(has=page.locator('#morphology')).evaluate('form => Array.from(new FormData(form))')
            result = fixture.client.post('/ocurrencias/2/clasificar', data=MultiDict(values))
            assert result.status_code == 302
            db = fixture.connect()
            assert db.execute('SELECT note FROM alternative_submission_component').fetchone()[0] == 'Componente propuesto'
            db.close()
            browser.close()
    finally:
        fixture.tearDown()
