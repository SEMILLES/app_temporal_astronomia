"""Existing-alternative proposals in the normal submission lifecycle."""
import json

from activity import resolve_collaborator, record_activity
from alternative_admin import (_active_alternative, relation_preview,
                               _blocking_ids, _reject_new_blocking)
from alternative_morphology import (normalize_morphology, _validate_component_targets,
                                    create_or_replace_alternative_morphology)
from alternative_relations import create_current_relation, _transaction, _finish, _rollback
from alternative_nomenclature import apply_nomenclature, calculate_nomenclature_preview, validate_final_labels
from edit_concurrency import edit_state, fingerprint


def _actor(db, collaborator_id, role, review=False):
    if role not in (('reviewer', 'master') if review else ('analyst', 'reviewer', 'master')):
        raise ValueError('Rol sin permiso para esta operación.')
    identifier, name = resolve_collaborator(db, collaborator_id)
    if identifier is None:
        raise ValueError('Seleccione un colaborador activo en Trabajando como.')
    return identifier, name


def baseline(db, alternative_id, kind):
    if kind == 'MORPHOLOGY':
        return fingerprint(edit_state(db, 'morphology', alternative_id))
    _active_alternative(db, alternative_id)
    # Relations can renumber all alternatives in the concept. Review the same
    # graph/evidence that was displayed, not a concurrently changed graph.
    from alternative_preconditions import relevant_state
    return fingerprint(relevant_state(db, alternative_id))


def _relation_payload(values):
    if not isinstance(values, dict):
        raise ValueError('La propuesta de relación debe ser un objeto.')
    if values == {'relation_answer': 'NO'}:
        return values
    if set(values) != {'target_id', 'parameter'}:
        raise ValueError('La propuesta debe indicar una relación completa o solamente la respuesta No.')
    return values


def _validate_no_current_relations(db, alternative_id):
    if db.execute('''SELECT 1 FROM alternative_relation WHERE is_current=1
        AND (alternative_low_id=? OR alternative_high_id=?) LIMIT 1''',
        (alternative_id, alternative_id)).fetchone():
        raise ValueError('No se puede proponer ni confirmar No: la alternativa tiene relaciones fonológicas vigentes.')


def relation_review_preview(db, alternative_id, values, resolution):
    """Preview the existing concept, with only this proposal's edge when accepted."""
    values = _relation_payload(values)
    negative = values.get('relation_answer') == 'NO'
    allowed = ('NO_CONFIRMED',) if negative else ('ACCEPTED', 'REJECTED')
    if resolution not in allowed:
        raise ValueError('La resolución lingüística no corresponde a esta propuesta.')
    alternative = _active_alternative(db, alternative_id)
    if negative:
        _validate_no_current_relations(db, alternative_id)
    if resolution == 'ACCEPTED':
        preview = relation_preview(db, alternative_id, action='add', **values)
    else:
        preview = calculate_nomenclature_preview(db, alternative['concept_id'])
    if preview['conclusive']:
        try:
            edges = [(alternative_id, values['target_id'])] if resolution == 'ACCEPTED' else ()
            validate_final_labels(db, alternative['concept_id'], preview['suggestions'], required_edges=edges)
        except ValueError as exc:
            preview['conclusive'] = False
            preview['problems'].append(str(exc))
    return preview


def relation_review_history(db, submission_id):
    row = db.execute('''SELECT comment FROM activity_event
        WHERE entity_type='submission' AND entity_id=? AND event_type='alternative_relation_reviewed'
        ORDER BY activity_event_id DESC LIMIT 1''', (submission_id,)).fetchone()
    return json.loads(row['comment']) if row else None


def _normalize(db, alternative_id, kind, values):
    if kind == 'MORPHOLOGY':
        normalized = normalize_morphology(**values)
        if normalized['component_count'] is None and not normalized['component_count_not_applicable']:
            raise ValueError('Indique la cantidad de componentes o No aplica.')
        _validate_component_targets(db, normalized)
        if any(c['component_alternative_id'] == alternative_id for c in normalized['components']):
            raise ValueError('La alternativa no puede ser componente de sí misma.')
        return normalized
    if kind == 'RELATION':
        values = _relation_payload(values)
        if values.get('relation_answer') == 'NO':
            _validate_no_current_relations(db, alternative_id)
            return {'relation_answer': 'NO'}
        preview = relation_preview(db, alternative_id, action='add',
            target_id=values.get('target_id'), parameter=values.get('parameter'))
        return {'target_id': preview['relation']['target_id'], 'parameter': preview['relation']['parameter']}
    raise ValueError('Tipo de propuesta no válido.')


def pending_changes(db, alternative_ids):
    """One batched read; a pending relation concerns both endpoints."""
    result = {aid: {'MORPHOLOGY': [], 'RELATION': []} for aid in alternative_ids}
    if not result:
        return result
    marks = ','.join('?' for _ in result)
    for row in db.execute(f'''SELECT s.submission_id,s.alternative_id,p.change_kind,
            json_extract(p.payload,'$.target_id') AS target_id,
            json_extract(p.payload,'$.parameter') AS parameter,
            json_extract(p.payload,'$.relation_answer') AS relation_answer
        FROM submission s JOIN alternative_change_submission p USING(submission_id)
        WHERE s.status='pending' AND (s.alternative_id IN ({marks})
            OR (p.change_kind='RELATION' AND json_extract(p.payload,'$.target_id') IN ({marks})))
        ORDER BY s.submission_id''', tuple(result) * 2):
        for aid in {row['alternative_id'], row['target_id']} & result.keys():
            result[aid][row['change_kind']].append(dict(row))
    return result


def _check_pending_duplicate(db, aid, kind, values):
    ids = [aid, values['target_id']] if kind == 'RELATION' and 'target_id' in values else [aid]
    by_alternative = pending_changes(db, ids)
    if kind == 'RELATION':
        negative = values.get('relation_answer') == 'NO'
        for endpoint in ids:
            for row in by_alternative[endpoint]['RELATION']:
                if negative != (row['relation_answer'] == 'NO'):
                    raise ValueError(f"La propuesta contradice un aporte de relación en revisión: Aporte #{row['submission_id']}.")
    pending = by_alternative[aid][kind]
    for row in pending:
        if kind == 'MORPHOLOGY' or (
                values.get('relation_answer') == 'NO' and row['relation_answer'] == 'NO'
                and aid == row['alternative_id']) or (
                values.get('target_id') is not None
                and {aid, values['target_id']} == {row['alternative_id'], row['target_id']}
                and values['parameter'] == row['parameter']):
            raise ValueError(f"Ya existe una propuesta equivalente en revisión: Aporte #{row['submission_id']}.")


def create_proposal(db, alternative_id, kind, values, *, collaborator_id, access_role, expected_baseline=None):
    owns = _transaction(db, 'alternative_change_create')
    try:
        actor_id, name = _actor(db, collaborator_id, access_role)
        target = _active_alternative(db, alternative_id)
        if expected_baseline is not None and baseline(db,alternative_id,kind) != expected_baseline:
            raise ValueError('La alternativa cambió; recargue la página antes de proponer.')
        normalized = _normalize(db, alternative_id, kind, values)
        _check_pending_duplicate(db, alternative_id, kind, normalized)
        concept = db.execute('SELECT preferred_label FROM concept WHERE concept_id=?', (target['concept_id'],)).fetchone()[0]
        sid = db.execute("""INSERT INTO submission(alternative_id,submission_type,status,submitted_by)
            VALUES(?,'ALTERNATIVE_CHANGE','pending',?)""", (alternative_id, name)).lastrowid
        db.execute("""INSERT INTO alternative_change_submission(submission_id,concept_id,concept_label_snapshot,
            alternative_label_snapshot,change_kind,payload,baseline,submitted_by_collaborator_id,submitted_access_role)
            VALUES(?,?,?,?,?,?,?,?,?)""", (sid, target['concept_id'], concept, target['working_label'], kind,
            json.dumps(normalized, ensure_ascii=False), baseline(db, alternative_id, kind), actor_id, access_role))
        record_activity(db, 'alternative_change_submitted', entity_type='submission', entity_id=sid,
                        collaborator_id=actor_id, access_role=access_role)
        _finish(db, 'alternative_change_create', owns)
        return sid
    except Exception:
        _rollback(db, 'alternative_change_create', owns)
        raise


def get_proposal(db, submission_id):
    row = db.execute('''SELECT s.*,p.* FROM submission s JOIN alternative_change_submission p USING(submission_id)
                        WHERE s.submission_id=?''', (submission_id,)).fetchone()
    return dict(row) if row else None


def create_relation_proposals(db, alternative_id, relations, **actor):
    """Submit the prepared list atomically, with one independent ID per edge."""
    if not relations:
        raise ValueError('Prepare al menos una relación.')
    if any(_relation_payload(values).get('relation_answer') == 'NO' for values in relations):
        raise ValueError('La respuesta No debe enviarse como una única propuesta sin relaciones.')
    owns = _transaction(db, 'alternative_relation_batch')
    try:
        identifiers = [create_proposal(db,alternative_id,'RELATION',values,**actor) for values in relations]
        _finish(db,'alternative_relation_batch',owns)
        return identifiers
    except Exception:
        _rollback(db,'alternative_relation_batch',owns)
        raise


def review_proposal(db, submission_id, decision, *, collaborator_id, access_role, note=None, expected_baseline=None,
                    relations_resolution=None):
    owns = _transaction(db, 'alternative_change_review')
    try:
        actor_id, name = _actor(db, collaborator_id, access_role, review=True)
        proposal = get_proposal(db, submission_id)
        if not proposal or proposal['status'] != 'pending' or decision not in ('accepted', 'rejected', 'pending'):
            raise ValueError('Aporte o decisión no válidos; puede haber sido revisado.')
        if decision == 'pending':
            if proposal['change_kind'] != 'RELATION':
                raise ValueError('Decisión no válida para este tipo de aporte.')
            _finish(db, 'alternative_change_review', owns)
            return None
        result_id = None
        renumber_event_id = None
        linguistic_resolution = None
        if decision == 'accepted':
            aid, kind = proposal['alternative_id'], proposal['change_kind']
            target = _active_alternative(db, aid)
            # Relations are independently reviewed against the graph shown on
            # the review page, which may include previously approved siblings.
            # Morphology must still match the version the analyst proposed over.
            expected = expected_baseline if kind=='RELATION' and expected_baseline is not None else proposal['baseline']
            if target['concept_id'] != proposal['concept_id'] or baseline(db, aid, kind) != expected:
                if kind=='RELATION' and expected_baseline is not None:
                    raise ValueError('La información cambió después de abrir la revisión. Vuelva a abrir el aporte antes de aprobar.')
                raise ValueError('La información canónica cambió desde la propuesta. Rechácela y solicite un nuevo aporte.')
            before = _blocking_ids(db)
            if kind == 'MORPHOLOGY':
                values = _normalize(db, aid, kind, json.loads(proposal['payload']))
                result_id, _ = create_or_replace_alternative_morphology(db, aid,
                    created_by=name, created_from_submission_id=submission_id, **values)
            else:
                values = _relation_payload(json.loads(proposal['payload']))
                linguistic_resolution = relations_resolution
                preview = relation_review_preview(db, aid, values, linguistic_resolution)
                if not preview['conclusive']:
                    raise ValueError('No se puede aplicar la nomenclatura: ' + '; '.join(preview['problems']))
                if linguistic_resolution == 'ACCEPTED':
                    result_id = create_current_relation(db, aid, values['target_id'], values['parameter'],
                        created_by=name, created_from_submission_id=submission_id)
                renumber_event_id = apply_nomenclature(db, target['concept_id'], preview['suggestions'], origin='automatic_assisted',
                    created_by=name, submission_id=submission_id)
            _reject_new_blocking(db, before)
        elif not (note or '').strip():
            raise ValueError('Indique el motivo del rechazo.')
        if proposal['change_kind'] == 'RELATION':
            record_activity(db, 'alternative_relation_reviewed', entity_type='submission', entity_id=submission_id,
                collaborator_id=actor_id, access_role=access_role,
                comment=json.dumps({'relations_resolution': linguistic_resolution if decision == 'accepted' else 'PROPOSAL_REJECTED',
                                    'renumber_event_id': renumber_event_id}, ensure_ascii=False))
        db.execute('''UPDATE alternative_change_submission SET reviewed_by_collaborator_id=?,
            reviewed_access_role=?,result_id=? WHERE submission_id=?''', (actor_id, access_role, result_id, submission_id))
        db.execute("""UPDATE submission SET status='resolved',resolution=?,resolved_at=CURRENT_TIMESTAMP,
            reviewed_by=?,review_note=? WHERE submission_id=?""", (decision, name, note, submission_id))
        record_activity(db, 'alternative_change_' + decision, entity_type='submission', entity_id=submission_id,
                        collaborator_id=actor_id, access_role=access_role, comment=note)
        _finish(db, 'alternative_change_review', owns)
        return result_id
    except Exception:
        _rollback(db, 'alternative_change_review', owns)
        raise
