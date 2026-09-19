"""Transactional Concept ↔ collaborator administration."""
from activity import resolve_collaborator
from source_details import occurrence_presentation


def concept_diagnostics(db, concept_ids):
    """Read the four task types in three batched queries, never mutate them."""
    result = {identifier: dict(alternative_count=0, morphology=[], relations=[], grammar=[], assignment=[])
              for identifier in concept_ids}
    if not result:
        return result
    marks = ','.join('?' for _ in result)
    alternatives = db.execute(f'''SELECT a.concept_id,a.alternative_id,a.working_label,
        EXISTS(SELECT 1 FROM alternative_morphology m
               WHERE m.alternative_id=a.alternative_id AND m.is_current=1) AS has_morphology,
        EXISTS(SELECT 1 FROM alternative_relation r WHERE r.is_current=1 AND
               (r.alternative_low_id=a.alternative_id OR r.alternative_high_id=a.alternative_id)) AS has_relation
        FROM alternative a WHERE a.retired_at IS NULL AND a.concept_id IN ({marks})
        ORDER BY a.concept_id,a.working_label,a.alternative_id''', tuple(result))
    for row in alternatives:
        diagnostic = result[row['concept_id']]
        diagnostic['alternative_count'] += 1
        item = dict(row)
        if not row['has_morphology']:
            diagnostic['morphology'].append(item)
        suffix = (row['working_label'] or '').strip().lower()[-1:]
        if suffix and 'b' <= suffix <= 'z' and not row['has_relation']:
            diagnostic['relations'].append(item)
    for row in db.execute(f'''SELECT a.concept_id,o.*,
            a.alternative_id,a.working_label,src.source_name,src.legacy_source_code,src.source_type
        FROM assignment s JOIN alternative a ON a.alternative_id=s.alternative_id
        JOIN occurrence o ON o.occurrence_id=s.occurrence_id
        JOIN source src ON src.source_id=o.source_id
        WHERE s.is_current=1 AND a.retired_at IS NULL AND a.concept_id IN ({marks})
          AND NOT EXISTS(SELECT 1 FROM occurrence_grammar g
                         WHERE g.occurrence_id=o.occurrence_id AND g.is_current=1)
        ORDER BY a.concept_id,o.occurrence_id''', tuple(result)):
        result[row['concept_id']]['grammar'].append(occurrence_presentation(row))
    for row in db.execute(f'''SELECT ref.concept_id,o.*,src.source_name,src.legacy_source_code,src.source_type
        FROM occurrence_concept_reference ref JOIN occurrence o ON o.occurrence_id=ref.occurrence_id
        JOIN source src ON src.source_id=o.source_id
        WHERE ref.is_current=1 AND ref.concept_id IN ({marks})
          AND NOT EXISTS(SELECT 1 FROM assignment s
                         WHERE s.occurrence_id=o.occurrence_id AND s.is_current=1)
        ORDER BY ref.concept_id,o.occurrence_id''', tuple(result)):
        result[row['concept_id']]['assignment'].append(occurrence_presentation(row))
    return result


def _ids(values):
    try:
        result = sorted({int(value) for value in values})
    except (ValueError, TypeError):
        raise ValueError('Selección no válida.') from None
    if not result or result[0] < 1 or len(result) > 500:
        raise ValueError('Seleccione entre 1 y 500 elementos.')
    return result


def assigned_analysts(db, concept_ids):
    result = {identifier: [] for identifier in concept_ids}
    if not result:
        return result
    marks = ','.join('?' for _ in result)
    for row in db.execute(f'''SELECT w.*, c.display_name, c.active AS collaborator_active
            FROM concept_work_assignment w JOIN collaborator c ON c.collaborator_id=w.analyst_id
            WHERE w.active=1 AND w.concept_id IN ({marks})
            ORDER BY c.display_name,w.work_assignment_id''', tuple(result)):
        result[row['concept_id']].append(row)
    return result


def list_concepts(db, *, search='', status='all', analyst_id=None, page=1, per_page=50):
    if status not in ('all', 'unassigned', 'assigned'):
        raise ValueError('Filtro de asignación no válido.')
    page = max(1, int(page))
    per_page = max(1, min(100, int(per_page)))
    conditions, params = [], []
    if search.strip():
        conditions.append("(c.preferred_label LIKE ? ESCAPE '\\' OR CAST(c.concept_id AS TEXT)=?)")
        literal = search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        params.append('%' + literal + '%')
        params.append(search.strip())
    exists = 'EXISTS (SELECT 1 FROM concept_work_assignment w WHERE w.concept_id=c.concept_id AND w.active=1'
    if status != 'all':
        conditions.append(('NOT ' if status == 'unassigned' else '') + exists + ')')
    if analyst_id not in (None, ''):
        conditions.append(exists + ' AND w.analyst_id=?)')
        params.append(_ids([analyst_id])[0])
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = db.execute('SELECT count(*) FROM concept c' + where, params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = db.execute('SELECT c.concept_id,c.preferred_label FROM concept c' + where +
                      ' ORDER BY c.preferred_label,c.concept_id LIMIT ? OFFSET ?',
                      (*params, per_page, (page - 1) * per_page)).fetchall()
    identifiers = [r['concept_id'] for r in rows]
    return dict(concepts=rows, assignments=assigned_analysts(db, identifiers),
                diagnostics=concept_diagnostics(db, identifiers),
                total=total, page=page, pages=pages)


def my_work(db, collaborator_id, *, search='', page=1, per_page=50):
    """Resolve the declared active collaborator; never fall back to all concepts."""
    identifier, name = resolve_collaborator(db, collaborator_id)
    if identifier is None:
        return dict(collaborator_id=None, collaborator_name=None, concepts=[],
                    assignments={}, diagnostics={}, total=0, page=1, pages=1)
    return dict(list_concepts(db, analyst_id=identifier, search=search, page=page,
                              per_page=per_page), collaborator_id=identifier,
                collaborator_name=name)


def assign(db, concept_ids, analyst_ids, *, actor_id=None, access_role):
    if access_role not in ('reviewer', 'master'):
        raise PermissionError('Acceso restringido a administración.')
    concepts, analysts = _ids(concept_ids), _ids(analyst_ids)
    db.execute('BEGIN IMMEDIATE')
    try:
        for identifier in concepts:
            if not db.execute('SELECT 1 FROM concept WHERE concept_id=?', (identifier,)).fetchone():
                raise ValueError('Un concepto seleccionado ya no existe.')
        names = {}
        for identifier in analysts:
            row = db.execute('SELECT display_name FROM collaborator WHERE collaborator_id=? AND active=1', (identifier,)).fetchone()
            if not row:
                raise ValueError('Seleccione colaboradores activos existentes.')
            names[identifier] = row[0]
        actor, snapshot = resolve_collaborator(db, actor_id)
        added = 0
        for concept in concepts:
            for analyst in analysts:
                added += db.execute('''INSERT INTO concept_work_assignment
                    (concept_id,analyst_id,analyst_name_snapshot,created_by_collaborator_id,
                     created_by_name_snapshot,created_access_role) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(concept_id,analyst_id) WHERE active=1 DO NOTHING''',
                    (concept, analyst, names[analyst], actor, snapshot, access_role)).rowcount
        db.commit()
        return added
    except Exception:
        db.rollback()
        raise


def remove(db, assignment_id, *, actor_id=None, access_role):
    if access_role not in ('reviewer', 'master'):
        raise PermissionError('Acceso restringido a administración.')
    db.execute('BEGIN IMMEDIATE')
    try:
        actor, snapshot = resolve_collaborator(db, actor_id)
        changed = db.execute('''UPDATE concept_work_assignment SET active=0,
            removed_at=CURRENT_TIMESTAMP,removed_by_collaborator_id=?,
            removed_by_name_snapshot=?,removed_access_role=?
            WHERE work_assignment_id=? AND active=1''',
            (actor, snapshot, access_role, assignment_id)).rowcount
        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
