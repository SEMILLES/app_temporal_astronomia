"""Signed lexical page/decision preconditions, without persisted preview state."""
from alternative_preconditions import relevant_state
from edit_concurrency import rows, fingerprint, sign, unsign, StaleEdit, STALE_PREVIEW


def lexical_state(db, occurrence_id, submission_id=None, proposal=None, decision=None):
    state = {}
    concepts, alternatives = set(), set()
    pending = [submission_id] if submission_id is not None else []
    proposal = proposal or {}
    decision = decision or {}
    if submission_id is None:
        pending.extend(row[0] for row in db.execute(
            "SELECT submission_id FROM submission WHERE occurrence_id=? AND submission_type='ALTERNATIVE' AND status='pending' ORDER BY submission_id", (occurrence_id,)))
        state['pending_submissions'] = sorted(pending)
    if decision.get('alternative_id'):
        alternatives.add(int(decision['alternative_id']))
    resolution = decision.get('concept_resolution') or {}
    if resolution.get('concept_id'):
        concepts.add(int(resolution['concept_id']))
    if resolution.get('action') in ('new', 'CREATE_NEW'):
        state['concept_labels'] = rows(db, 'SELECT concept_id,preferred_label FROM concept ORDER BY concept_id')
    if proposal.get('proposed_existing_alternative_id'):
        alternatives.add(int(proposal['proposed_existing_alternative_id']))
    for relation in proposal.get('relations', ()):
        if relation.get('target_alternative_id'):
            alternatives.add(int(relation['target_alternative_id']))
        if relation.get('target_submission_id'):
            pending.append(int(relation['target_submission_id']))
    for component in (proposal.get('morphology') or {}).get('components', ()):
        if component.get('component_alternative_id'):
            alternatives.add(int(component['component_alternative_id']))
    seen = set()
    while pending:
        sid = pending.pop()
        if sid in seen:
            continue
        seen.add(sid)
        projections = {
            'submission': 'submission_id,occurrence_id,submission_type,status,resolution',
            'alternative_submission': 'submission_id,proposal_kind,reference_concept_id,reference_concept_proposal_id,proposed_existing_alternative_id,phonological_relation_answer,resolved_alternative_id',
            'alternative_submission_relation': 'alternative_submission_relation_id,target_alternative_id,target_submission_id,phonological_parameter,uncertain',
            'alternative_submission_morphology': 'submission_id,component_count,component_count_not_applicable,free_permutation,note',
            'alternative_submission_component': 'submission_id,position,component_alternative_id,component_label,note',
            'submission_concept_resolution': 'submission_concept_resolution_id,concept_id,resolution_action',
            'submission_lexical_decision': 'submission_id,decision_action,resolved_alternative_id',
        }
        for table, fields in projections.items():
            current = ' AND is_current=1' if table == 'submission_concept_resolution' else ''
            data = rows(db, f'SELECT {fields} FROM {table} WHERE submission_id=?{current} ORDER BY rowid', (sid,))
            state[f'{table}:{sid}'] = data
            for row in data:
                for key in ('concept_id', 'reference_concept_id'):
                    if row.get(key) is not None:
                        concepts.add(row[key])
                for key in ('proposed_existing_alternative_id', 'resolved_alternative_id',
                            'target_alternative_id', 'component_alternative_id'):
                    if row.get(key) is not None:
                        alternatives.add(row[key])
                if row.get('target_submission_id') is not None:
                    pending.append(row['target_submission_id'])
    state['reference'] = rows(db, 'SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=? AND is_current=1', (occurrence_id,))
    concepts.update(r['concept_id'] for r in state['reference'] if r['concept_id'] is not None)
    state['assignment'] = rows(db, 'SELECT assignment_id,alternative_id FROM assignment WHERE occurrence_id=? AND is_current=1', (occurrence_id,))
    alternatives.update(r['alternative_id'] for r in state['assignment'])
    state['occurrence'] = rows(db, '''SELECT o.occurrence_id,o.source_id,o.occurrence_year,
        s.start_year,s.end_year,s.end_year_status FROM occurrence o JOIN source s USING(source_id)
        WHERE occurrence_id=?''', (occurrence_id,))
    state['endpoints'] = []
    for aid in sorted(alternatives):
        data = rows(db, 'SELECT alternative_id,concept_id,retired_at IS NULL AS active FROM alternative WHERE alternative_id=?', (aid,))
        state['endpoints'].extend(data)
        concepts.update(r['concept_id'] for r in data)
    state['concepts'] = [relevant_state(db, -1, cid) for cid in sorted(concepts)]
    if proposal.get('concept_metadata'):
        from concept_classification import catalog_state, concept_state
        state['controlled_catalogs'] = catalog_state(db)
        state['concept_metadata'] = [concept_state(db,cid) for cid in sorted(concepts)]
    return state


def lexical_token(db, occurrence_id, submission_id=None, proposal=None, decision=None):
    return sign({'purpose': 'lexical-preview', 'occurrence_id': occurrence_id,
                 'submission_id': submission_id, 'proposal': proposal, 'decision': decision,
                 'fingerprint': fingerprint(lexical_state(db, occurrence_id, submission_id, proposal, decision))})


def check_lexical(db, token, occurrence_id, submission_id=None, proposal=None, decision=None):
    expected = unsign(token, STALE_PREVIEW)
    actual = {'purpose': 'lexical-preview', 'occurrence_id': occurrence_id,
              'submission_id': submission_id, 'proposal': proposal, 'decision': decision,
              'fingerprint': fingerprint(lexical_state(db, occurrence_id, submission_id, proposal, decision))}
    if fingerprint(actual) != fingerprint(expected):
        raise StaleEdit(STALE_PREVIEW)


def check_submission_preview(db, submission_id, token):
    row = db.execute('SELECT occurrence_id FROM submission WHERE submission_id=?', (submission_id,)).fetchone()
    if row is None:
        raise StaleEdit(STALE_PREVIEW)
    check_lexical(db, token, row[0], submission_id)
