"""Provenance and lexical choices, using disposable synthetic databases only."""
import importlib
import sqlite3
from html.parser import HTMLParser

import pytest

from tests.test_new_concept_metadata import http
from tests.form_client import hidden
from occurrence_registration import complete_registration, save_draft
from alternative_workflow import create_alternative_submission, AlternativeWorkflowError
from submission_concept_resolution import save_resolution, current_resolution


class Inputs(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.inputs = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            self.inputs.append(dict(attrs))

    def radio(self, name, value):
        return next(i for i in self.inputs if i.get('type') == 'radio'
                    and i.get('name') == name and i.get('value') == value)


def dump(http):
    return '\n'.join(http.db.iterdump())


def reference(http, oid):
    return http.db.execute('SELECT * FROM occurrence_concept_reference WHERE occurrence_id=? AND is_current=1', (oid,)).fetchone()


def register(http, **choice):
    return complete_registration(http.db, source_id=1, original_gloss='PRUEBA ORIGEN',
                                 access_role='analyst', **choice)


def metadata(http):
    return {'classifications': {http.sf: http.fields[:1]}}


def propose(http, oid):
    return create_alternative_submission(http.db, oid, 'NEW',
        phonological_relation_answer='NO', morphology={'component_count_not_applicable': True},
        concept_metadata=metadata(http), access_role='analyst')


def review_page(http, sid):
    response = http.client.get(f'/aportes/{sid}')
    assert response.status_code == 200
    return response.get_data(as_text=True)


def decide(http, sid, **data):
    data.setdefault('lexical_preview_token', hidden(review_page(http, sid), 'lexical_preview_token'))
    return http.client.post(f'/aportes/{sid}/decidir', data=data)


def resolve(http, sid, concept_id=None):
    if concept_id is None:
        save_resolution(http.db, sid, 'CREATE_NEW', label='CONCEPTO RESUELTO',
                        concept_metadata=metadata(http), note='Resolución de prueba', access_role='reviewer')
    else:
        save_resolution(http.db, sid, 'USE_EXISTING', concept_id=concept_id,
                        note='Corresponde a este concepto', access_role='reviewer')
    return current_resolution(http.db, sid)['concept_id']


def add_alternative(http, cid=1, retired=False):
    aid = http.db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(?,'1',?)",
                          (cid, '2026-01-01' if retired else None)).lastrowid
    http.db.commit()
    return aid


def test_registration_persists_choice_on_each_reference_and_retains_history(http):
    first = register(http, proposed_label='PROPUESTA COMPARTIDA')
    original = dict(reference(http, first))
    assert original['proposal_origin'] == 'NEW_PROPOSAL'
    second = register(http, concept_proposal_id=original['concept_proposal_id'])
    assert reference(http, second)['proposal_origin'] == 'SELECTED_PENDING'
    assert reference(http, second)['concept_proposal_id'] == original['concept_proposal_id']
    # Label deduplication does not reinterpret the user's explicit choice.
    third = register(http, proposed_label='PROPUESTA COMPARTIDA')
    assert reference(http, third)['proposal_origin'] == 'NEW_PROPOSAL'
    assert reference(http, third)['concept_proposal_id'] == original['concept_proposal_id']
    direct = register(http, concept_id=1)
    assert reference(http, direct)['proposal_origin'] is None
    sid = propose(http, first)
    resolve(http, sid, 1)
    old = dict(http.db.execute('SELECT * FROM occurrence_concept_reference WHERE occurrence_concept_reference_id=?',
                              (original['occurrence_concept_reference_id'],)).fetchone())
    assert old == dict(original, is_current=0)
    assert reference(http, first)['proposal_origin'] is None
    assert reference(http, first)['supersedes_occurrence_concept_reference_id'] == original['occurrence_concept_reference_id']
    assert reference(http, second)['proposal_origin'] == 'SELECTED_PENDING'


def test_pending_choice_from_draft_is_preserved(http):
    oid = register(http, proposed_label='BORRADOR')
    pid = reference(http, oid)['concept_proposal_id']
    draft = save_draft(http.db, source_id=1, original_gloss='DESDE BORRADOR', reference_concept_proposal_id=pid)
    completed = complete_registration(http.db, draft_id=draft)
    assert reference(http, completed)['proposal_origin'] == 'SELECTED_PENDING'


@pytest.mark.parametrize('kind', ['EXISTING', 'UNSURE'])
def test_new_proposal_rejects_forged_service_and_http_choices(http, kind):
    oid = register(http, proposed_label='NUEVO')
    before = dump(http)
    with pytest.raises(AlternativeWorkflowError, match='debe proponer una alternativa nueva'):
        create_alternative_submission(http.db, oid, kind, analysis_note='Manipulado',
                                     concept_metadata=metadata(http), access_role='analyst')
    assert dump(http) == before
    response = http.client.post(f'/ocurrencias/{oid}/clasificar', data={
        'proposal_kind': kind, 'analysis_note': 'Manipulado',
        'proposal_origin': 'SELECTED_PENDING', **http.selections(http.fields[:1])})
    assert response.status_code == 400
    assert 'debe proponer una alternativa nueva' in response.get_data(as_text=True)
    assert dump(http) == before
    radios = Inputs(response.get_data(as_text=True))
    for value in ('NEW', 'EXISTING', 'UNSURE'):
        radio = radios.radio('proposal_kind', value)
        assert 'disabled' in radio
        assert ('checked' in radio) == (value == 'NEW')


def test_new_proposal_new_is_valid_and_controls_are_locked(http):
    oid = register(http, proposed_label='NUEVO')
    url = f'/ocurrencias/{oid}/clasificar'
    page = http.client.get(url).get_data(as_text=True)
    inputs = Inputs(page)
    assert all('disabled' in inputs.radio('proposal_kind', value) for value in ('NEW', 'EXISTING', 'UNSURE'))
    assert 'checked' in inputs.radio('proposal_kind', 'NEW')
    assert hidden(page, 'proposal_kind') == 'NEW'
    response = http.client.post(url, data={'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO',
        'morphology_component_count': 'N/A', **http.selections(http.fields[:1])})
    assert response.status_code == 302
    assert http.db.execute('SELECT proposal_kind FROM alternative_submission').fetchone()[0] == 'NEW'


@pytest.mark.parametrize('origin', [None, 'SELECTED_PENDING', 'DIRECT'])
@pytest.mark.parametrize('kind', ['NEW', 'UNSURE', 'EXISTING'])
def test_unknown_selected_and_direct_keep_existing_behavior(http, origin, kind):
    if origin == 'DIRECT':
        oid = register(http, concept_id=1)
        extra = {}
    else:
        first = register(http, proposed_label='PENDIENTE')
        oid = register(http, concept_proposal_id=reference(http, first)['concept_proposal_id'])
        if origin is None:
            http.db.execute('UPDATE occurrence_concept_reference SET proposal_origin=NULL WHERE occurrence_id=?', (oid,))
            http.db.commit()
        extra = {}
    aid = add_alternative(http)
    url = f'/ocurrencias/{oid}/clasificar'
    inputs = Inputs(http.client.get(url).get_data(as_text=True))
    assert all('disabled' not in inputs.radio('proposal_kind', value) for value in ('NEW', 'EXISTING', 'UNSURE'))
    data = {'proposal_kind': kind, 'analysis_note': 'Incertidumbre', **extra}
    if kind == 'NEW':
        data.update(phonological_relation_answer='NO', morphology_component_count='N/A')
    elif kind == 'EXISTING':
        data['proposed_existing_alternative_id'] = str(aid)
    before = dump(http)
    response = http.client.post(url, data=data)
    if kind == 'EXISTING' and origin != 'DIRECT':
        # Existing validation remains: an unresolved proposal has no valid target.
        assert response.status_code == 400
        assert dump(http) == before
    else:
        assert response.status_code == 302
        assert http.db.execute('SELECT proposal_kind FROM alternative_submission').fetchone()[0] == kind


@pytest.mark.parametrize('new_concept', [True, False])
@pytest.mark.parametrize('action', ['new', 'rejected', 'pending'])
def test_empty_resolved_concept_disables_existing_but_keeps_other_actions(http, new_concept, action):
    oid = register(http, proposed_label='NUEVO')
    sid = propose(http, oid)
    cid = resolve(http, sid, None if new_concept else 1)
    page = review_page(http, sid)
    inputs = Inputs(page)
    assert 'disabled' in inputs.radio('decision', 'existing')
    assert 'El concepto resuelto no tiene alternativas vigentes.' in page
    for value in ('new', 'rejected', 'pending'):
        assert 'disabled' not in inputs.radio('decision', value)
    assert 'Crear una nueva alternativa en el concepto resuelto' in page
    assert 'Aceptar la propuesta del analista' not in page
    before = dump(http)
    response = decide(http, sid, decision=action, morphology_resolution='ACCEPTED', review_note='Decisión léxica')
    assert response.status_code == 302, response.get_data(as_text=True)
    if action == 'pending':
        assert dump(http) == before
    elif action == 'new':
        assert http.db.execute('SELECT concept_id FROM alternative').fetchone()[0] == cid
    else:
        assert http.db.execute('SELECT decision_action FROM submission_lexical_decision').fetchone()[0] == 'REJECT_REST'
    assert current_resolution(http.db, sid)['concept_id'] == cid


@pytest.mark.parametrize('target', [None, '', '9999', 'foreign', 'retired'])
def test_manual_existing_post_without_valid_destination_is_atomic(http, target):
    oid = register(http, proposed_label='NUEVO')
    sid = propose(http, oid)
    resolve(http, sid, 1)
    if target in ('foreign', 'retired'):
        target = str(add_alternative(http, cid=2 if target == 'foreign' else 1, retired=target == 'retired'))
    assert 'disabled' in Inputs(review_page(http, sid)).radio('decision', 'existing')
    before = dump(http)
    data = dict(decision='existing', morphology_resolution='REJECTED', review_note='Destino manipulado')
    if target is not None:
        data['alternative_id'] = target
    response = decide(http, sid, **data)
    assert response.status_code == 400, response.get_data(as_text=True)
    assert dump(http) == before


@pytest.mark.parametrize('action', ['new', 'existing'])
def test_new_proposal_resolved_to_existing_concept_supports_both_actions(http, action):
    oid = register(http, proposed_label='NUEVO X')
    sid = propose(http, oid)
    resolve(http, sid, 1)
    aid = add_alternative(http)
    inputs = Inputs(review_page(http, sid))
    assert 'disabled' not in inputs.radio('decision', 'existing')
    assert 'disabled' not in inputs.radio('decision', 'new')
    response = decide(http, sid, decision=action, alternative_id=str(aid),
        morphology_resolution='ACCEPTED' if action == 'new' else 'REJECTED',
        review_note='Nueva decisión en concepto Y')
    assert response.status_code == 302, response.get_data(as_text=True)
    result = http.db.execute('SELECT decision_action,resolved_alternative_id,concept_id_at_decision FROM submission_lexical_decision').fetchone()
    assert result['decision_action'] == ('CREATE_NEW' if action == 'new' else 'USE_EXISTING')
    assert result['concept_id_at_decision'] == 1
    assert (result['resolved_alternative_id'] == aid) == (action == 'existing')


def test_changed_resolution_rejects_old_page_and_old_target_with_fresh_token(http):
    oid = register(http, proposed_label='NUEVO X')
    sid = propose(http, oid)
    resolve(http, sid, 1)
    aid = add_alternative(http)
    old_token = hidden(review_page(http, sid), 'lexical_preview_token')
    resolve(http, sid, 2)
    page = review_page(http, sid)
    assert 'disabled' in Inputs(page).radio('decision', 'existing')
    assert f'<option value="{aid}">' not in page.split('name="alternative_id"')[1].split('</select>')[0]
    before = dump(http)
    data = dict(decision='existing', alternative_id=str(aid), morphology_resolution='REJECTED', review_note='Selección obsoleta')
    assert decide(http, sid, lexical_preview_token=old_token, **data).status_code == 409
    assert dump(http) == before
    assert decide(http, sid, **data).status_code == 400
    assert dump(http) == before
    # Availability follows real alternatives, even after the resolution was saved.
    new_target = add_alternative(http, cid=2)
    assert 'disabled' not in Inputs(review_page(http, sid)).radio('decision', 'existing')
    data['alternative_id'] = str(new_target)
    assert decide(http, sid, **data).status_code == 302
    assert http.db.execute('SELECT concept_id_at_decision FROM submission_lexical_decision').fetchone()[0] == 2


def test_migration_is_local_idempotent_and_leaves_historical_rows_unknown(http, tmp_path):
    migration = importlib.import_module('migrations.028_concept_reference_origin')
    register(http, proposed_label='HISTÓRICO')
    http.db.execute('ALTER TABLE occurrence_concept_reference DROP COLUMN proposal_origin')
    http.db.commit()
    historical = [tuple(r) for r in http.db.execute('SELECT * FROM occurrence_concept_reference ORDER BY occurrence_concept_reference_id')]
    before = dump(http)
    assert migration.migrate(http.path)['changes'] == 1
    assert dump(http) == before
    backup = tmp_path / 'before-028.db'
    assert migration.migrate(http.path, apply=True, backup_path=backup)['changes'] == 1
    assert backup.exists()
    with sqlite3.connect(backup) as db:
        assert 'proposal_origin' not in {r[1] for r in db.execute('PRAGMA table_info(occurrence_concept_reference)')}
    rows = [tuple(r) for r in http.db.execute('SELECT * FROM occurrence_concept_reference ORDER BY occurrence_concept_reference_id')]
    assert rows == [row + (None,) for row in historical]
    assert migration.migrate(http.path, apply=True)['changes'] == 0
    assert http.db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('origin', ['INVALID', 'NEW_PROPOSAL', 'SELECTED_PENDING'])
def test_schema_rejects_invalid_or_direct_concept_provenance(http, origin):
    with pytest.raises(sqlite3.IntegrityError):
        http.db.execute('UPDATE occurrence_concept_reference SET proposal_origin=? WHERE occurrence_id=1', (origin,))
    http.db.rollback()
