"""Versioned conceptual decisions belonging to individual lexical submissions."""
import json

from activity import record_activity, resolve_collaborator
from concept_labels import InvalidConceptLabel, normalize_concept_label
from edit_concurrency import check_edit


SCHEMA = """
CREATE TABLE IF NOT EXISTS submission_concept_resolution (
    submission_concept_resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submission(submission_id),
    concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
    resolution_action TEXT NOT NULL CHECK(resolution_action IN
        ('CONFIRM_REFERENCE','USE_EXISTING','CREATE_NEW')),
    resolution_note TEXT,
    collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
    collaborator_name_snapshot TEXT,
    access_role TEXT NOT NULL CHECK(access_role IN ('reviewer','master')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    supersedes_submission_concept_resolution_id INTEGER REFERENCES
        submission_concept_resolution(submission_concept_resolution_id),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_submission_concept_resolution
    ON submission_concept_resolution(submission_id) WHERE is_current=1;
CREATE INDEX IF NOT EXISTS idx_submission_concept_resolution_submission
    ON submission_concept_resolution(submission_id);
"""


def install(connection):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            connection.execute(statement)


class ConceptResolutionError(ValueError):
    pass


def current_resolution(connection, submission_id):
    return connection.execute("""SELECT r.*,c.preferred_label
        FROM submission_concept_resolution r JOIN concept c USING(concept_id)
        WHERE r.submission_id=? AND r.is_current=1""", (submission_id,)).fetchone()


def resolution_history(connection, submission_id):
    return connection.execute("""SELECT r.*,c.preferred_label
        FROM submission_concept_resolution r JOIN concept c USING(concept_id)
        WHERE r.submission_id=? ORDER BY submission_concept_resolution_id DESC""",
        (submission_id,)).fetchall()


def require_concept(connection, submission_id):
    row = current_resolution(connection, submission_id)
    if row is None:
        raise ConceptResolutionError('Es necesario guardar la resolución del concepto antes de resolver el resto del análisis.')
    return row['concept_id']


def proposed_concept_decision(connection, submission):
    """Translate proposal acceptance within the caller's resolution transaction."""
    if not submission['reference_concept_proposal_id']:
        raise ConceptResolutionError('La propuesta original no contiene una propuesta conceptual.')
    label = normalize_concept_label(submission['proposed_label'])
    for concept in connection.execute('SELECT concept_id,preferred_label FROM concept ORDER BY concept_id'):
        try:
            equivalent = normalize_concept_label(concept['preferred_label']) == label
        except InvalidConceptLabel:
            continue
        if equivalent:
            return 'USE_EXISTING', concept['concept_id'], label
    return 'CREATE_NEW', None, label


def save_resolution(connection, submission_id, action, *, concept_id=None,
                    label=None, note=None, collaborator_id=None,
                    access_role, expected_edit_token=None):
    if access_role not in ('reviewer', 'master'):
        raise ConceptResolutionError('La resolución del concepto requiere permisos de revisión.')
    owns = not connection.in_transaction
    connection.execute('BEGIN IMMEDIATE' if owns else 'SAVEPOINT local_concept')
    try:
        if expected_edit_token is not None:
            check_edit(connection, 'submission_concept', submission_id, expected_edit_token)
        submission = connection.execute("""SELECT s.*,a.reference_concept_id,
            a.reference_concept_proposal_id,c.preferred_label AS reference_label,
            p.proposed_label FROM submission s JOIN alternative_submission a USING(submission_id)
            LEFT JOIN concept c ON c.concept_id=a.reference_concept_id
            LEFT JOIN concept_proposal p ON p.concept_proposal_id=a.reference_concept_proposal_id
            WHERE s.submission_id=?""", (submission_id,)).fetchone()
        if submission is None or submission['submission_type'] != 'ALTERNATIVE' or submission['status'] != 'pending':
            raise ConceptResolutionError('El aporte léxico debe estar pendiente.')
        previous = current_resolution(connection, submission_id)
        note = (note or '').strip() or None
        if action == 'ACCEPT_PROPOSAL':
            action, concept_id, label = proposed_concept_decision(connection, submission)
        if action == 'CONFIRM_REFERENCE':
            concept_id = submission['reference_concept_id']
            if concept_id is None:
                raise ConceptResolutionError('La propuesta original no contiene una referencia directa a un concepto.')
        if action in ('CONFIRM_REFERENCE', 'USE_EXISTING'):
            concept = connection.execute('SELECT concept_id,preferred_label FROM concept WHERE concept_id=?', (concept_id,)).fetchone()
            if concept is None:
                raise ConceptResolutionError('El concepto seleccionado no existe.')
            concept_id, decided_label = concept
        elif action == 'CREATE_NEW':
            decided_label = normalize_concept_label(label or '')
            if connection.execute('SELECT 1 FROM concept WHERE UPPER(preferred_label)=UPPER(?)', (decided_label,)).fetchone():
                raise ConceptResolutionError('El concepto ya existe; seleccione el concepto existente.')
            concept_id = None
        else:
            raise ConceptResolutionError('La acción de resolución conceptual no es válida.')
        if (previous is not None and previous['concept_id'] == concept_id
                and ((previous['resolution_note'] or '').strip() or None) == note):
            connection.commit() if owns else connection.execute('RELEASE SAVEPOINT local_concept')
            return previous['submission_concept_resolution_id']
        original_id = submission['reference_concept_id']
        original_label = submission['proposed_label']
        same_original = (concept_id == original_id if original_id is not None else
            bool(original_label) and normalize_concept_label(original_label) == normalize_concept_label(decided_label))
        if (not same_original or (previous is not None and previous['concept_id'] != concept_id)) and not note:
            raise ConceptResolutionError('Explique el cambio de concepto en la nota de resolución.')
        if action == 'CREATE_NEW':
            concept_id = connection.execute('INSERT INTO concept(preferred_label) VALUES(?)', (decided_label,)).lastrowid
        actor_id, actor_name = resolve_collaborator(connection, collaborator_id)
        previous_id = previous['submission_concept_resolution_id'] if previous else None
        if previous_id:
            connection.execute('UPDATE submission_concept_resolution SET is_current=0 WHERE submission_concept_resolution_id=?', (previous_id,))
        identifier = connection.execute("""INSERT INTO submission_concept_resolution
            (submission_id,concept_id,resolution_action,resolution_note,collaborator_id,
             collaborator_name_snapshot,access_role,supersedes_submission_concept_resolution_id)
            VALUES(?,?,?,?,?,?,?,?)""", (submission_id,concept_id,action,note,actor_id,actor_name,access_role,previous_id)).lastrowid
        reference = connection.execute('SELECT * FROM occurrence_concept_reference WHERE occurrence_id=? AND is_current=1', (submission['occurrence_id'],)).fetchone()
        reference_id = reference['occurrence_concept_reference_id'] if reference else None
        if reference_id:
            connection.execute('UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_concept_reference_id=?', (reference_id,))
        new_reference = connection.execute("""INSERT INTO occurrence_concept_reference
            (occurrence_id,concept_id,supersedes_occurrence_concept_reference_id)
            VALUES(?,?,?)""", (submission['occurrence_id'],concept_id,reference_id)).lastrowid
        record_activity(connection, 'submission_concept_resolved', entity_type='submission',
            entity_id=submission_id, collaborator_id=actor_id, access_role=access_role,
            comment=json.dumps({'resolution_id':identifier,'reference_id':new_reference,'concept_id':concept_id,'note':note}, ensure_ascii=False))
        connection.commit() if owns else connection.execute('RELEASE SAVEPOINT local_concept')
        return identifier
    except Exception:
        if owns:
            connection.rollback()
        else:
            connection.execute('ROLLBACK TO SAVEPOINT local_concept')
            connection.execute('RELEASE SAVEPOINT local_concept')
        raise
