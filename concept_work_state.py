"""Canonical Concept work and transactional completion, independent of workflows."""
from sqlite3 import Row

from activity import resolve_collaborator, record_activity

WORK_KINDS = ('morphology', 'relations', 'grammar', 'assignment')


def _concept_ids(values):
    if isinstance(values, (int, str)):
        values = [values]
    return sorted({int(value) for value in values if value is not None})


def canonical_tasks(db, concept_ids):
    identifiers = _concept_ids(concept_ids)
    result = {}
    for start in range(0, len(identifiers), 400):
        result.update(_canonical_tasks(db, identifiers[start:start + 400]))
    return result


def pending_counts(db, concept_ids):
    return {cid: {kind: len(tasks[kind]) for kind in WORK_KINDS}
            for cid, tasks in canonical_tasks(db, concept_ids).items()}


def has_pending_work(db, concept_id):
    return any(pending_counts(db, [concept_id])[int(concept_id)].values())


def _canonical_tasks(db, concept_ids):
    """Read canonical tasks only; proposals never affect completion."""
    result = {identifier: dict(alternative_count=0, morphology=[], relations=[], grammar=[], assignment=[])
              for identifier in concept_ids}
    if not result:
        return result
    cursor = db.cursor()
    cursor.row_factory = Row
    marks = ','.join('?' for _ in result)
    alternatives = cursor.execute(f'''SELECT a.concept_id,a.alternative_id,a.working_label,
        EXISTS(SELECT 1 FROM alternative_morphology m
               WHERE m.alternative_id=a.alternative_id AND m.is_current=1) AS has_morphology,
        EXISTS(SELECT 1 FROM alternative_relation r WHERE r.is_current=1 AND
               (r.alternative_low_id=a.alternative_id OR r.alternative_high_id=a.alternative_id)) AS has_relation
        FROM alternative a WHERE a.retired_at IS NULL AND a.concept_id IN ({marks})
        ORDER BY a.concept_id,a.working_label,a.alternative_id''', tuple(result))
    alternatives = list(alternatives)
    for row in alternatives:
        diagnostic = result[row['concept_id']]
        diagnostic['alternative_count'] += 1
        item = dict(row)
        if not row['has_morphology']:
            diagnostic['morphology'].append(item)
        suffix = (row['working_label'] or '').strip().lower()[-1:]
        if suffix and 'b' <= suffix <= 'z' and not row['has_relation']:
            diagnostic['relations'].append(item)
    for row in cursor.execute(f'''SELECT a.concept_id,o.*,
            a.alternative_id,a.working_label,src.source_name,src.legacy_source_code,src.source_type
        FROM assignment s JOIN alternative a ON a.alternative_id=s.alternative_id
        JOIN occurrence o ON o.occurrence_id=s.occurrence_id
        JOIN source src ON src.source_id=o.source_id
        WHERE s.is_current=1 AND a.retired_at IS NULL AND a.concept_id IN ({marks})
          AND NOT EXISTS(SELECT 1 FROM occurrence_grammar g
                         WHERE g.occurrence_id=o.occurrence_id AND g.is_current=1)
        ORDER BY a.concept_id,o.occurrence_id''', tuple(result)):
        result[row['concept_id']]['grammar'].append(dict(row))
    for row in cursor.execute(f'''SELECT ref.concept_id,o.*,src.source_name,src.legacy_source_code,src.source_type
        FROM occurrence_concept_reference ref JOIN occurrence o ON o.occurrence_id=ref.occurrence_id
        JOIN source src ON src.source_id=o.source_id
        WHERE ref.is_current=1 AND ref.concept_id IN ({marks})
          AND NOT EXISTS(SELECT 1 FROM assignment s
                         WHERE s.occurrence_id=o.occurrence_id AND s.is_current=1)
        ORDER BY ref.concept_id,o.occurrence_id''', tuple(result)):
        result[row['concept_id']]['assignment'].append(dict(row))
    cursor.close()
    return result


def reconcile_completed_work_assignments(db, concept_ids, collaborator_id=None, access_role=None):
    """Close completed assignments in the caller's transaction; never commit."""
    identifiers = _concept_ids(concept_ids)
    active = set()
    for start in range(0, len(identifiers), 400):
        batch = identifiers[start:start + 400]
        marks = ','.join('?' for _ in batch)
        active.update(row[0] for row in db.execute(
            f'SELECT DISTINCT concept_id FROM concept_work_assignment WHERE active=1 AND concept_id IN ({marks})', batch))
    completed = [cid for cid, counts in pending_counts(db, active).items() if not any(counts.values())]
    if not completed:
        return 0
    if access_role not in ('reviewer', 'master'):
        raise ValueError('El cierre del trabajo requiere Reviewer o Master.')
    if not db.in_transaction:
        raise ValueError('El cierre del trabajo requiere la transacción del llamador.')
    actor_id, name = resolve_collaborator(db, collaborator_id)
    closed = 0
    for cid in completed:
        changed = db.execute('''UPDATE concept_work_assignment SET active=0,
            removed_at=CURRENT_TIMESTAMP,removed_by_collaborator_id=?,
            removed_by_name_snapshot=?,removed_access_role=?
            WHERE concept_id=? AND active=1''', (actor_id, name, access_role, cid)).rowcount
        closed += changed
        if changed:
            record_activity(db, 'concept_work_assignment_completed', entity_type='concept', entity_id=cid,
                            collaborator_id=actor_id, access_role=access_role,
                            comment=f'Asignaciones completadas: {changed}.')
    return closed
