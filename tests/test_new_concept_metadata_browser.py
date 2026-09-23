"""Browser checks for successful form controls; uses only synthetic fixture data."""
from pathlib import Path

import pytest

from tests.test_new_concept_metadata import http
from alternative_workflow import create_alternative_submission

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page(http):
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page()

        def respond(route):
            request = route.request
            path = request.url.removeprefix('http://lesico.test')
            if path == '/static/new-concept-metadata.js':
                route.fulfill(path=str(ROOT / 'static/new-concept-metadata.js'), content_type='text/javascript')
            else:
                result = http.client.get(path)
                route.fulfill(status=result.status_code, body=result.data,
                              content_type=result.content_type)

        page.route('http://lesico.test/**', respond)
        yield page
        browser.close()


def submitted_metadata(page):
    return page.locator('form').filter(has=page.locator('.new-concept-metadata')).evaluate(
        "form => Object.fromEntries(Array.from(new FormData(form)).filter(([key]) => /^(category_|classification_apply_|collection_action_)/.test(key)))")


def test_collection_toggle_does_not_send_orphan_classifications(http, page):
    http.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('NUEVO','pending')")
    http.db.execute('UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1')
    http.db.commit()
    http.role = 'analyst'
    page.goto('http://lesico.test/ocurrencias/1/clasificar')
    area = page.locator(f'[name="category_{http.ka}_1"]')
    assert not area.is_visible()
    collection = page.locator(f'[name="collection_action_{http.collection}"]')
    collection.check()
    assert area.is_visible()
    assert area.get_attribute('required') is not None
    area.select_option(str(http.areas[0]))
    page.locator(f'[name="category_{http.sf}_1"]').select_option(str(http.fields[0]))
    assert page.locator(f'[name="category_{http.sf}_2"] option[value="{http.fields[0]}"]').evaluate('option => option.disabled')
    assert submitted_metadata(page)[f'category_{http.ka}_1'] == str(http.areas[0])
    collection.uncheck()
    assert not area.is_visible()
    data = submitted_metadata(page)
    assert f'category_{http.ka}_1' not in data
    assert f'classification_apply_{http.ka}' not in data
    assert f'collection_action_{http.collection}' not in data
    response = http.client.post('/ocurrencias/1/clasificar', data={
        'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO',
        'morphology_component_count': 'N/A', **data})
    assert response.status_code == 302
    assert http.db.execute('SELECT count(*) FROM submission_collection_proposal').fetchone()[0] == 0
    assert http.db.execute('SELECT count(*) FROM submission_classification_proposal WHERE system_id=?', (http.ka,)).fetchone()[0] == 0


def test_reviewer_existing_target_hides_and_disables_editor(http, page):
    http.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('NUEVO','pending')")
    http.db.execute('UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1')
    http.db.commit()
    sid = create_alternative_submission(http.db, 1, 'NEW', phonological_relation_answer='NO',
        morphology={'component_count_not_applicable': True}, access_role='analyst',
        concept_metadata={'classifications': {http.sf: http.fields[:1], http.ka: http.areas[:1]},
                          'collections': {http.collection: 'join'}})
    page.goto(f'http://lesico.test/aportes/{sid}')
    editor = page.locator('.new-concept-metadata')
    assert editor.is_visible()
    assert page.locator(f'[name="category_{http.sf}_1"]').input_value() == str(http.fields[0])
    assert page.locator(f'[name="collection_action_{http.collection}"]').is_checked()
    page.locator('[name="concept_action"]').select_option('USE_EXISTING')
    assert not editor.is_visible()
    assert submitted_metadata(page) == {}
    page.locator('[name="concept_action"]').select_option('CREATE_NEW')
    assert editor.is_visible()
    assert f'classification_apply_{http.sf}' in submitted_metadata(page)
    http.db.execute("UPDATE concept_proposal SET proposed_label='UNO'")
    http.db.commit()
    page.reload()
    assert page.locator('[name="concept_action"]').input_value() == 'ACCEPT_PROPOSAL'
    assert not editor.is_visible()
    assert submitted_metadata(page) == {}
