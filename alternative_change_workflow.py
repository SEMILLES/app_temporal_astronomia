"""Existing-alternative proposals in the normal submission lifecycle."""
import json

from activity import resolve_collaborator, record_activity
from alternative_admin import (_active_alternative, relation_preview,
                               _blocking_ids, _reject_new_blocking)
from alternative_morphology import (normalize_morphology, _validate_component_targets,
                                    create_or_replace_alternative_morphology)
from alternative_relations import create_current_relation, _transaction, _finish, _rollback
from alternative_nomenclature import apply_nomenclature
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
        preview = relation_preview(db, alternative_id, action='add',
            target_id=values.get('target_id'), parameter=values.get('parameter'))
        return {'target_id': preview['relation']['target_id'], 'parameter': preview['relation']['parameter']}
    raise ValueError('Tipo de propuesta no válido.')


def create_proposal(db, alternative_id, kind, values, *, collaborator_id, access_role):
    owns = _transaction(db, 'alternative_change_create')
    try:
        actor_id, name = _actor(db, collaborator_id, access_role)
        target = _active_alternative(db, alternative_id)
        normalized = _normalize(db, alternative_id, kind, values)
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


def review_proposal(db, submission_id, decision, *, collaborator_id, access_role, note=None):
    owns = _transaction(db, 'alternative_change_review')
    try:
        actor_id, name = _actor(db, collaborator_id, access_role, review=True)
        proposal = get_proposal(db, submission_id)
        if not proposal or proposal['status'] != 'pending' or decision not in ('accepted', 'rejected'):
            raise ValueError('Aporte o decisión no válidos; puede haber sido revisado.')
        result_id = None
        if decision == 'accepted':
            aid, kind = proposal['alternative_id'], proposal['change_kind']
            target = _active_alternative(db, aid)
            if target['concept_id'] != proposal['concept_id'] or baseline(db, aid, kind) != proposal['baseline']:
                raise ValueError('La información canónica cambió desde la propuesta. Rechácela y solicite un nuevo aporte.')
            values = _normalize(db, aid, kind, json.loads(proposal['payload']))
            before = _blocking_ids(db)
            if kind == 'MORPHOLOGY':
                result_id, _ = create_or_replace_alternative_morphology(db, aid,
                    created_by=name, created_from_submission_id=submission_id, **values)
            else:
                preview = relation_preview(db, aid, action='add', **values)
                result_id = create_current_relation(db, aid, values['target_id'], values['parameter'],
                    created_by=name, created_from_submission_id=submission_id)
                apply_nomenclature(db, target['concept_id'], preview['suggestions'], origin='automatic_assisted',
                    created_by=name, submission_id=submission_id)
            _reject_new_blocking(db, before)
        elif not (note or '').strip():
            raise ValueError('Indique el motivo del rechazo.')
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
