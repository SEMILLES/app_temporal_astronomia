"""Analyst metadata scope and one reviewer destination, on synthetic data."""
import pytest

from tests.test_new_concept_metadata import http
from tests.test_concept_reference_origin import register, reference, add_alternative, dump
from tests.form_client import hidden
from alternative_workflow import create_alternative_submission
from concept_classification import ClassificationError
from submission_concept_resolution import current_resolution


def analyst_reference(http, origin):
    if origin == 'DIRECT':
        return register(http, concept_id=1)
    first = register(http, proposed_label='PROPUESTA ORIGINAL')
    if origin == 'NEW_PROPOSAL':
        return first
    oid = register(http, concept_proposal_id=reference(http, first)['concept_proposal_id'])
    if origin is None:
        http.db.execute('UPDATE occurrence_concept_reference SET proposal_origin=NULL WHERE occurrence_id=?', (oid,))
        http.db.commit()
    return oid


def selected_submission(http):
    oid = analyst_reference(http, 'SELECTED_PENDING')
    response = http.client.post(f'/ocurrencias/{oid}/clasificar', data={
        'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO', 'morphology_component_count': 'N/A'})
    assert response.status_code == 302, response.text
    return http.db.execute('SELECT submission_id FROM submission WHERE occurrence_id=?', (oid,)).fetchone()[0]


@pytest.mark.parametrize('origin', ['NEW_PROPOSAL', 'SELECTED_PENDING', 'DIRECT', None])
def test_analyst_editor_only_for_explicit_new_proposal(http, origin):
    http.role = 'analyst'
    oid = analyst_reference(http, origin)
    page = http.client.get(f'/ocurrencias/{oid}/clasificar').text
    for text in ('class="new-concept-metadata"', 'Campo semántico', 'Incluir en colecciones',
                 f'name="category_{http.ka}_1"'):
        assert (text in page) == (origin == 'NEW_PROPOSAL')


@pytest.mark.parametrize('origin', ['SELECTED_PENDING', 'DIRECT', None])
def test_analyst_forged_metadata_is_rejected_without_mutations(http, origin):
    http.role = 'analyst'
    oid = analyst_reference(http, origin)
    before = dump(http)
    payload = {'classifications': {http.sf: http.fields[:1], http.ka: http.areas[:1]},
               'collections': {http.collection: 'join'}}
    with pytest.raises(ClassificationError):
        create_alternative_submission(http.db, oid, 'NEW', phonological_relation_answer='NO',
            morphology={'component_count_not_applicable': True}, concept_metadata=payload, access_role='analyst')
    assert dump(http) == before
    response = http.client.post(f'/ocurrencias/{oid}/clasificar', data={
        'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO', 'morphology_component_count': 'N/A',
        **http.selections(http.fields[:1], http.areas[:1], 'join')})
    assert response.status_code == 400
    assert dump(http) == before


def test_reviewer_can_create_and_classify_concept_from_selected_pending(http):
    sid = selected_submission(http)
    assert http.db.execute('SELECT count(*) FROM submission_classification_proposal').fetchone()[0] == 0
    url = f'/aportes/{sid}'
    page = http.client.get(url).text
    assert 'Selección final del revisor para el concepto nuevo' in page
    form = {'concept_action': 'CREATE_NEW', 'concept_label': 'CREADO POR REVISOR',
            'concept_note': 'Resolución final', 'concept_edit_token': hidden(page, 'concept_edit_token')}
    before = dump(http)
    assert http.client.post(url + '/concepto', data=form).status_code == 400
    assert dump(http) == before
    form.update(http.selections(http.fields[:1], http.areas[:1], 'join'))
    response = http.client.post(url + '/concepto', data=form)
    assert response.status_code == 302, response.text
    cid = current_resolution(http.db, sid)['concept_id']
    assert http.db.execute('SELECT count(*) FROM concept_classification_revision WHERE concept_id=?', (cid,)).fetchone()[0] == 2


def test_target_a_token_rejects_b_and_consulting_b_allows_save(http):
    sid = selected_submission(http)
    add_alternative(http, cid=2)
    url = f'/aportes/{sid}'
    page_a = http.client.get(url + '?metadata_target=1').text
    before = dump(http)
    form = {'concept_action': 'USE_EXISTING', 'concept_id': '2', 'metadata_target': '1',
            'concept_note': 'Destino B', 'concept_edit_token': hidden(page_a, 'concept_edit_token')}
    assert http.client.post(url + '/concepto', data=form).status_code == 400
    form['metadata_target'] = '2'
    assert http.client.post(url + '/concepto', data=form).status_code == 400
    assert dump(http) == before
    page_b = http.client.get(url + '?metadata_target=2').text
    assert 'Estado consultado: <strong>DOS</strong>' in page_b
    assert '1 alternativa vigente' in page_b
    assert '<select name="metadata_target"' not in page_b
    form['concept_edit_token'] = hidden(page_b, 'concept_edit_token')
    response = http.client.post(url + '/concepto', data=form)
    assert response.status_code == 302, response.text
    assert current_resolution(http.db, sid)['concept_id'] == 2


def test_consulted_token_still_rejects_concurrent_changes(http):
    sid = selected_submission(http)
    url = f'/aportes/{sid}'
    page = http.client.get(url + '?metadata_target=2').text
    http.db.execute("UPDATE concept SET preferred_label='DOS MODIFICADO' WHERE concept_id=2")
    http.db.commit()
    before = dump(http)
    response = http.client.post(url + '/concepto', data={
        'concept_action': 'USE_EXISTING', 'concept_id': '2', 'metadata_target': '2',
        'concept_note': 'Destino B', 'concept_edit_token': hidden(page, 'concept_edit_token')})
    assert response.status_code == 409
    assert dump(http) == before
