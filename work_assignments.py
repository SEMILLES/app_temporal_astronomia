"""Transactional Concept ↔ collaborator administration."""
from concept_work_state import canonical_tasks, pending_counts
from activity import resolve_collaborator
from source_details import occurrence_presentation
from alternative_change_workflow import pending_changes

WORK_TYPES = {
    'morphology': 'Morfología',
    'relations': 'Relación fonológica',
    'grammar': 'Gramática',
    'assignment': 'Asignación a alternativa',
}


def concept_diagnostics(db, concept_ids):
    """Decorate shared canonical tasks with evidence and pending proposals."""
    result = canonical_tasks(db, concept_ids)
    items = {item['alternative_id']: item for tasks in result.values()
             for kind in ('morphology', 'relations') for item in tasks[kind]}
    pending = pending_changes(db, list(items))

    grammar_occurrences = sorted({
        item['occurrence_id']
        for tasks in result.values()
        for item in tasks['grammar']
    })
    pending_grammar = {identifier: [] for identifier in grammar_occurrences}
    if grammar_occurrences:
        marks = ','.join('?' for _ in grammar_occurrences)
        rows = db.execute(
            f'''SELECT submission_id, occurrence_id
                FROM submission
                WHERE submission_type='GRAMMAR'
                  AND status='pending'
                  AND occurrence_id IN ({marks})
                ORDER BY submission_id''',
            tuple(grammar_occurrences),
        ).fetchall()
        for row in rows:
            pending_grammar[row['occurrence_id']].append(row)

    for tasks in result.values():
        for kind in ('morphology', 'relations'):
            for item in tasks[kind]:
                item['pending_changes'] = pending[item['alternative_id']]
        for kind in ('grammar', 'assignment'):
            tasks[kind] = [occurrence_presentation(row) for row in tasks[kind]]
        for item in tasks['grammar']:
            item['pending_submissions'] = pending_grammar.get(
                item['occurrence_id'], []
            )
    return result


def _ids(values):
    try:
        result = sorted({int(value) for value in values})
    except (ValueError, TypeError):
        raise ValueError('Selección no válida.') from None
    if not result or result[0] < 1 or result[-1] > 9223372036854775807 or len(result) > 500:
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


def list_concepts(db, *, search='', status='all', analyst_id=None, page=1, per_page=50,
                  pending_only=False, concept_id=None, work_type=''):
    if status not in ('all', 'unassigned', 'assigned'):
        raise ValueError('Filtro de asignación no válido.')
    page = max(1, int(page))
    per_page = max(1, min(100, int(per_page)))
    if work_type not in ('', *WORK_TYPES):
        raise ValueError('Tipo de trabajo no válido.')
    selected_concept = None if concept_id in (None, '') else _ids([concept_id])[0]
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
    if pending_only:
        # Aggregate canonical diagnostics before filtering/pagination, with
        # bounded Concept IN lists. Preserve the personal-work listing below.
        candidates = db.execute('SELECT c.concept_id,c.preferred_label FROM concept c' +
                                where + ' ORDER BY c.preferred_label,c.concept_id', params).fetchall()
        diagnostics = {}
        for start in range(0, len(candidates), 400):
            diagnostics.update(concept_diagnostics(
                db, [row['concept_id'] for row in candidates[start:start + 400]]))
        summaries = []
        for row in candidates:
            counts = {kind: len(diagnostics[row['concept_id']][kind]) for kind in WORK_TYPES}
            if sum(counts.values()):
                summaries.append(dict(row, counts=counts, total=sum(counts.values())))
        summaries.sort(key=lambda row: (-row['total'], row['preferred_label'] or '', row['concept_id']))
        summary = [row for row in summaries
                   if selected_concept is None or row['concept_id'] == selected_concept]
        rows = [row for row in summary if not work_type or row['counts'][work_type]]
        task_total = sum(row['counts'][work_type] if work_type else row['total'] for row in rows)
        total = len(rows)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = rows[(page - 1) * per_page:page * per_page]
        identifiers = [row['concept_id'] for row in rows]
        detail = {identifier: dict(diagnostics[identifier]) for identifier in identifiers}
        if work_type:
            for diagnostic in detail.values():
                for kind in WORK_TYPES:
                    if kind != work_type:
                        diagnostic[kind] = []
        return dict(concepts=rows, assignments=assigned_analysts(db, identifiers),
                    diagnostics=detail, summary=summary, concept_options=summaries,
                    work_types=WORK_TYPES, task_total=task_total,
                    total=total, page=page, pages=pages)
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
    data = list_concepts(db, analyst_id=identifier, search=search, page=page,
                         per_page=per_page, pending_only=True)
    # Personal diagnostics keep their dimension totals; work_types is admin UI context.
    data.pop('work_types', None)
    return dict(data, collaborator_id=identifier, collaborator_name=name)


def assign(db, concept_ids, analyst_ids, *, actor_id=None, access_role):
    if access_role not in ('reviewer', 'master'):
        raise PermissionError('Acceso restringido a administración.')
    concepts, analysts = _ids(concept_ids), _ids(analyst_ids)
    db.execute('BEGIN IMMEDIATE')
    try:
        for identifier in concepts:
            if not db.execute('SELECT 1 FROM concept WHERE concept_id=?', (identifier,)).fetchone():
                raise ValueError('Un concepto seleccionado ya no existe.')
        if any(not any(counts.values()) for counts in pending_counts(db, concepts).values()):
            raise ValueError('Solo se pueden asignar conceptos con trabajo pendiente.')
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
