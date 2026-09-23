"""Complete NEW selections, immutable proposals and independent conceptual decisions."""
import json

import pytest
from werkzeug.datastructures import MultiDict

from tests import test_concept_classification as fixtures
from alternative_workflow import create_alternative_submission, reject_alternative_submission
from concept_classification import ClassificationError, concept_state, parse_new_form, proposal_payload, administer
from submission_concept_resolution import save_resolution, current_resolution


@pytest.fixture
def case():
    fixture = fixtures.ClassificationTests()
    fixture.setUp()
    fixture.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('NUEVO','pending')")
    fixture.db.execute("UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1,proposal_origin='NEW_PROPOSAL'")
    fixture.db.commit()
    yield fixture
    fixture.doCleanups()


def selection(case, fields=1, areas=0, join=False):
    return {'classifications': {
        case.sf: case.fields[:fields],
        **({case.ka: case.areas[:areas]} if areas else {}),
    }, 'collections': {case.collection: 'join'} if join else {}}


@pytest.mark.parametrize('fields,areas,join,valid', [
    (0, 0, False, False), (1, 0, False, True), (2, 0, False, True),
    (3, 0, False, False), (1, 0, True, False), (1, 1, True, True),
    (2, 2, True, True), (1, 3, True, False), (1, 1, False, False),
])
def test_required_and_maximum_selections(case, fields, areas, join, valid):
    payload = selection(case, fields, areas, join)
    before = case.dump()
    if valid:
        sid = case.propose(payload)
        assert proposal_payload(case.db, sid)['classifications'][case.sf][0] == case.fields[0]
        assert not concept_state(case.db, 3)['revisions']
    else:
        with pytest.raises(ClassificationError):
            case.propose(payload)
        assert case.dump() == before


@pytest.mark.parametrize('kind', ['missing', 'duplicate-field', 'duplicate-area', 'wrong-system',
                                  'unknown-system', 'unknown-collection', 'leave'])
def test_forged_payloads_are_atomic(case, kind):
    payload = selection(case, 1, 1, True)
    if kind == 'missing':
        payload = None
    elif kind == 'duplicate-field':
        payload['classifications'][case.sf] = [case.fields[0]] * 2
    elif kind == 'duplicate-area':
        payload['classifications'][case.ka] = [case.areas[0]] * 2
    elif kind == 'wrong-system':
        payload['classifications'][case.sf] = [case.areas[0]]
    elif kind == 'unknown-system':
        payload['classifications'][9999] = [case.fields[0]]
    elif kind == 'unknown-collection':
        payload['collections'][9999] = 'join'
    else:
        payload['collections'][case.collection] = 'leave'
    before = case.dump()
    with pytest.raises(ClassificationError):
        case.propose(payload)
    assert case.dump() == before


@pytest.mark.parametrize('table,key,identifier', [
    ('classification_category', 'category_id', 'field'),
    ('classification_category', 'category_id', 'area'),
    ('classification_system', 'system_id', 'sf'),
    ('classification_system', 'system_id', 'ka'),
    ('collection', 'collection_id', 'collection'),
])
@pytest.mark.parametrize('stage', ['proposal', 'resolution'])
def test_inactive_catalog_rejected(case, table, key, identifier, stage):
    payload = selection(case, 1, 1, True)
    sid = case.propose(payload) if stage == 'resolution' else None
    value = {'field': case.fields[0], 'area': case.areas[0],
             'sf': case.sf, 'ka': case.ka, 'collection': case.collection}[identifier]
    case.db.execute(f'UPDATE {table} SET active=0 WHERE {key}=?', (value,))
    case.db.commit()
    before = case.dump()
    with pytest.raises(ClassificationError):
        if sid:
            save_resolution(case.db, sid, 'ACCEPT_PROPOSAL', access_role='reviewer', concept_metadata=payload)
        else:
            case.propose(payload)
    assert case.dump() == before


@pytest.mark.parametrize('join_before,join_after', [(False, True), (True, False), (True, True)])
def test_reviewer_final_selection_preserves_proposal(case, join_before, join_after):
    original = selection(case, 2, 2 if join_before else 0, join_before)
    sid = case.propose(original)
    stored = proposal_payload(case.db, sid)
    final = selection(case, 1, 1 if join_after else 0, join_after)
    final['classifications'][case.sf] = case.fields[2:3]
    if join_after:
        final['classifications'][case.ka] = case.areas[2:3]
    rid = save_resolution(case.db, sid, 'ACCEPT_PROPOSAL', access_role='reviewer', concept_metadata=final)
    resolution = current_resolution(case.db, sid)
    assert resolution['resolution_action'] == 'CREATE_NEW'
    assert resolution['submission_concept_resolution_id'] == rid
    decision = json.loads(resolution['classification_decision_json'])
    assert decision['classifications'][0]['categories'] == [case.fields[2], None]
    state = concept_state(case.db, resolution['concept_id'])
    assert len(state['memberships']) == int(join_after)
    assert {r['system_id']: r['category_1_id'] for r in state['revisions']} == {
        case.sf: case.fields[2], **({case.ka: case.areas[2]} if join_after else {})}
    assert proposal_payload(case.db, sid) == stored
    assert all(r['resolution_id'] == rid for r in state['memberships'] + state['revisions'])
    reject_alternative_submission(case.db, sid, access_role='reviewer', review_note='Decisión léxica independiente')
    assert concept_state(case.db, resolution['concept_id']) == state
    assert current_resolution(case.db, sid)['classification_decision_json'] == resolution['classification_decision_json']


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('failure', ['membership', 'classification', 'decision', 'activity'])
def test_failure_rolls_back_all_new_concept_state(case, nested, failure):
    payload = selection(case, 2, 2, True)
    sid = case.propose(payload)
    table, operation, condition = {
        'membership': ('collection_membership', 'INSERT', ''),
        'classification': ('concept_classification_revision', 'INSERT', f'WHEN NEW.system_id={case.ka}'),
        'decision': ('submission_concept_resolution', 'UPDATE', 'WHEN NEW.classification_decision_json IS NOT NULL'),
        'activity': ('activity_event', 'INSERT', "WHEN NEW.event_type='submission_concept_resolved'"),
    }[failure]
    case.db.execute(f"CREATE TRIGGER fail_new_metadata BEFORE {operation} ON {table} {condition} BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    case.db.commit()
    if nested:
        case.db.execute('BEGIN')
        case.db.execute("UPDATE source SET source_name='Outer transaction' WHERE source_id=1")
    before = case.dump()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        save_resolution(case.db, sid, 'ACCEPT_PROPOSAL', access_role='reviewer', concept_metadata=payload)
    assert case.dump() == before
    assert case.db.in_transaction == nested


@pytest.mark.parametrize('action', ['USE_EXISTING', 'ACCEPT_PROPOSAL', 'CONFIRM_REFERENCE'])
def test_existing_resolution_rejects_metadata(case, action):
    payload = selection(case, 1, 1, True)
    sid = case.propose(payload)
    if action == 'ACCEPT_PROPOSAL':
        case.db.execute("UPDATE concept_proposal SET proposed_label='UNO'")
    if action == 'CONFIRM_REFERENCE':
        case.db.execute('UPDATE alternative_submission SET reference_concept_id=1,reference_concept_proposal_id=NULL WHERE submission_id=?', (sid,))
    case.db.commit()
    before = case.dump()
    with pytest.raises(ClassificationError):
        save_resolution(case.db, sid, action, concept_id=1, note='Existente', access_role='reviewer', concept_metadata=payload)
    assert case.dump() == before
    save_resolution(case.db, sid, action, concept_id=1, note='Existente', access_role='reviewer')
    reject_alternative_submission(case.db, sid, access_role='reviewer', review_note='Rechazo léxico')
    assert not concept_state(case.db, 1)['revisions']
    assert not concept_state(case.db, 1)['memberships']


def test_existing_analyst_cannot_send_metadata(case):
    case.db.execute('UPDATE occurrence_concept_reference SET concept_id=1,concept_proposal_id=NULL,proposal_origin=NULL')
    case.db.commit()
    before = case.dump()
    for payload in (selection(case), {}):
        with pytest.raises(ClassificationError):
            create_alternative_submission(case.db, 1, 'NEW', phonological_relation_answer='NO',
                morphology={'component_count_not_applicable': True}, access_role='analyst', concept_metadata=payload)
        assert case.dump() == before


def test_multiple_future_collections_and_systems(case):
    cid = administer(case.db, 'collection', code='future', name='Otra colección', access_role='master')
    systems = [administer(case.db, 'system', code=f'future-{i}', name=f'Clasificación {i}',
                         parent_id=cid, access_role='master') for i in range(2)]
    categories = [administer(case.db, 'category', code=f'cat-{sid}', name='Categoría',
                            parent_id=sid, access_role='master') for sid in systems]
    payload = selection(case, 1, 1, True)
    payload['collections'][cid] = 'join'
    payload['classifications'][systems[0]] = [categories[0]]
    with pytest.raises(ClassificationError):
        case.propose(payload)
    payload['classifications'][systems[1]] = [categories[1]]
    sid = case.propose(payload)
    save_resolution(case.db, sid, 'ACCEPT_PROPOSAL', access_role='reviewer', concept_metadata=payload)
    state = concept_state(case.db, current_resolution(case.db, sid)['concept_id'])
    assert len(state['memberships']) == 2
    assert len(state['revisions']) == 4


@pytest.mark.parametrize('payload', [None, {}, {'classifications': {}}])
def test_reviewer_cannot_create_without_required_metadata(case, payload):
    sid = case.propose(selection(case))
    before = case.dump()
    with pytest.raises(ClassificationError):
        save_resolution(case.db, sid, 'CREATE_NEW', label='NUEVO', access_role='reviewer', concept_metadata=payload)
    assert case.dump() == before


def test_reviewer_invalid_final_selection_rolls_back(case):
    sid = case.propose(selection(case, 1, 1, True))
    final = selection(case, 1, 1, False)
    before = case.dump()
    with pytest.raises(ClassificationError):
        save_resolution(case.db, sid, 'ACCEPT_PROPOSAL', access_role='reviewer', concept_metadata=final)
    assert case.dump() == before


@pytest.mark.parametrize('form', [
    [('category_1_1', '1')], [('classification_apply_bad', 'yes')],
    [('classification_apply_1', 'yes'), ('category_1_3', '3')],
    [('classification_apply_1', 'yes'), ('category_1_1', '1'), ('category_1_1', '2')],
    [('collection_action_1', 'join'), ('collection_action_1', 'leave')],
])
def test_ambiguous_or_forged_post_fields(form):
    with pytest.raises(ClassificationError):
        parse_new_form(MultiDict(form))


@pytest.fixture
def http():
    fixture = fixtures.ClassificationRouteTests()
    fixture.setUp()
    yield fixture
    fixture.doCleanups()


def test_http_existing_controls_absent_and_forged_post_rejected(http):
    http.role = 'analyst'
    page = http.client.get('/ocurrencias/1/clasificar').get_data(as_text=True)
    assert 'name="classification_apply_' not in page
    assert 'name="collection_action_' not in page
    for metadata in (http.selections(http.fields[:1]), {'category_1_1': ''}):
        response = http.client.post('/ocurrencias/1/clasificar', data={
            'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO',
            'morphology_component_count': 'N/A', **metadata})
        assert response.status_code == 400
    assert http.db.execute('SELECT count(*) FROM submission').fetchone()[0] == 0


def test_http_new_required_and_error_preserves_selections(http):
    http.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('NUEVO','pending')")
    http.db.execute("UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1,proposal_origin='NEW_PROPOSAL'")
    http.db.commit()
    http.role = 'analyst'
    url = '/ocurrencias/1/clasificar'
    page = http.client.get(url).get_data(as_text=True)
    assert 'Campo semántico *' in page
    assert 'Incluir en colecciones' in page
    assert '>Otro</option>' in page
    data = {'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO', 'morphology_component_count': 'N/A'}
    assert http.client.post(url, data=data).status_code == 400
    data.update(http.selections(http.fields[:1], action='join'))
    response = http.client.post(url, data=data)
    assert response.status_code == 400
    assert f'value="{http.fields[0]}" selected' in response.get_data(as_text=True)
    data.update(http.selections(areas=http.areas[:1]))
    assert http.client.post(url, data=data).status_code == 302
