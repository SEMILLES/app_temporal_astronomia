"""Registration catalog and shared proposal lifecycle on synthetic databases."""
from html.parser import HTMLParser
from unittest.mock import patch

import pytest

from tests.test_new_concept_metadata import http
from tests.test_concept_reference_origin import (
    register, reference, propose, resolve, add_alternative, dump,
)
from alternative_workflow import create_alternative_submission, reject_alternative_submission


class SelectOptions(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.options = {}
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.current = attrs.get('name')
            self.options.setdefault(self.current, set())
        elif tag == 'option' and self.current:
            self.options[self.current].add(attrs.get('value'))

    def handle_endtag(self, tag):
        if tag == 'select':
            self.current = None


def options(http):
    response = http.client.get('/aportes/nuevo')
    assert response.status_code == 200
    return SelectOptions(response.get_data(as_text=True)).options


@pytest.mark.parametrize('with_alternative', [False, True])
def test_create_new_immediately_available_and_lexical_catalog(http, with_alternative):
    oid = register(http, proposed_label='ORIGINAL')
    old = dict(reference(http, oid))
    sid = propose(http, oid)
    cid = resolve(http, sid)
    aid = add_alternative(http, cid) if with_alternative else None
    catalog = options(http)
    assert str(cid) in catalog['reference_concept_id']
    assert str(old['concept_proposal_id']) not in catalog['reference_concept_proposal_id']
    proposal = http.db.execute('SELECT * FROM concept_proposal WHERE concept_proposal_id=?',
                               (old['concept_proposal_id'],)).fetchone()
    assert proposal['status'] == 'resolved'
    assert proposal['resolved_concept_id'] == cid
    assert proposal['resolved_at']
    assert proposal['resolution_note'] == 'Resolución de prueba'
    history = dict(http.db.execute('SELECT * FROM occurrence_concept_reference WHERE occurrence_concept_reference_id=?',
                                   (old['occurrence_concept_reference_id'],)).fetchone())
    assert history == dict(old, is_current=0)
    assert reference(http, oid)['supersedes_occurrence_concept_reference_id'] == old['occurrence_concept_reference_id']
    http.role = 'analyst'
    response = http.client.post('/ocurrencias/guardar', data={
        'source_id': 1, 'original_gloss': 'FUTURA', 'reference_kind': 'concept',
        'reference_concept_id': cid})
    assert response.status_code == 302
    future = http.db.execute('SELECT max(occurrence_id) FROM occurrence').fetchone()[0]
    assert reference(http, future)['concept_id'] == cid
    page = http.client.get(f'/ocurrencias/{future}/clasificar').get_data(as_text=True)
    if aid:
        assert str(aid) in SelectOptions(page).options['proposed_existing_alternative_id']
    else:
        assert 'No hay alternativas vigentes para este concepto.' in page


@pytest.mark.parametrize('status', ['pending', 'resolved', 'rejected'])
def test_selector_and_manual_post_check_canonical_status(http, status):
    oid = register(http, proposed_label='PROPUESTA')
    pid = reference(http, oid)['concept_proposal_id']
    http.db.execute('UPDATE concept_proposal SET status=?,resolved_concept_id=? WHERE concept_proposal_id=?',
                    (status, 1 if status == 'resolved' else None, pid))
    http.db.commit()
    assert (str(pid) in options(http)['reference_concept_proposal_id']) == (status == 'pending')
    before = dump(http)
    response = http.client.post('/ocurrencias/guardar', data={
        'source_id': 1, 'original_gloss': 'POST', 'reference_kind': 'proposal',
        'reference_concept_proposal_id': pid})
    if status == 'pending':
        assert response.status_code == 302
        future = http.db.execute('SELECT max(occurrence_id) FROM occurrence').fetchone()[0]
        assert reference(http, future)['proposal_origin'] == 'SELECTED_PENDING'
    else:
        assert response.status_code == 400
        assert 'La propuesta conceptual no está pendiente.' in response.get_data(as_text=True)
        assert dump(http) == before


def test_loaded_pending_form_rejected_after_resolution(http):
    oid = register(http, proposed_label='CARRERA')
    pid = reference(http, oid)['concept_proposal_id']
    assert str(pid) in options(http)['reference_concept_proposal_id']
    resolve(http, propose(http, oid))
    before = dump(http)
    response = http.client.post('/ocurrencias/guardar', data={
        'source_id': 1, 'original_gloss': 'TARDIA', 'reference_kind': 'proposal',
        'reference_concept_proposal_id': pid})
    assert response.status_code == 400
    assert 'La propuesta conceptual no está pendiente.' in response.get_data(as_text=True)
    assert dump(http) == before


def test_shared_proposal_closes_once_without_rewriting_other_references(http):
    first = register(http, proposed_label='COMPARTIDA')
    pid = reference(http, first)['concept_proposal_id']
    second = register(http, concept_proposal_id=pid)
    second_reference = dict(reference(http, second))
    a = propose(http, first)
    b = create_alternative_submission(http.db, second, 'NEW',
        phonological_relation_answer='NO', morphology={'component_count_not_applicable': True},
        access_role='analyst')
    cid = resolve(http, a)
    closed = tuple(http.db.execute('SELECT * FROM concept_proposal WHERE concept_proposal_id=?', (pid,)).fetchone())
    assert dict(reference(http, second)) == second_reference
    resolve(http, b, 1)
    reject_alternative_submission(http.db, a, access_role='reviewer', review_note='Rechazo léxico')
    assert tuple(http.db.execute('SELECT * FROM concept_proposal WHERE concept_proposal_id=?', (pid,)).fetchone()) == closed
    assert str(cid) in options(http)['reference_concept_id']
    assert http.db.execute('SELECT reference_concept_proposal_id FROM alternative_submission WHERE submission_id=?', (b,)).fetchone()[0] == pid


def test_resolution_closure_rolls_back_with_rest_of_transaction(http):
    oid = register(http, proposed_label='ATOMICIDAD')
    sid = propose(http, oid)
    before = dump(http)
    with patch('submission_concept_resolution.record_activity', side_effect=RuntimeError('fallo')):
        with pytest.raises(RuntimeError, match='fallo'):
            resolve(http, sid)
    assert dump(http) == before


def test_unknown_concept_manual_post_has_no_mutation(http):
    before = dump(http)
    response = http.client.post('/ocurrencias/guardar', data={
        'source_id': 1, 'original_gloss': 'INVALIDA', 'reference_kind': 'concept',
        'reference_concept_id': 999999})
    assert response.status_code == 400
    assert dump(http) == before


def test_canonical_catalog_includes_concept_without_occurrences_or_metadata(http):
    cid = http.db.execute("INSERT INTO concept(preferred_label) VALUES('SIN-EVIDENCIA')").lastrowid
    http.db.commit()
    assert str(cid) in options(http)['reference_concept_id']
