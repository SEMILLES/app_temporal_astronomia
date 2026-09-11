"""Final lexical decisions; callers supply materialized results in their transaction."""
from activity import resolve_collaborator
from concept_labels import alternative_display_label

SCHEMA = """
CREATE TABLE IF NOT EXISTS submission_lexical_decision (
    submission_id INTEGER PRIMARY KEY
        REFERENCES submission(submission_id),

    concept_resolution_id INTEGER NOT NULL
        REFERENCES submission_concept_resolution(
            submission_concept_resolution_id
        ),

    decision_action TEXT NOT NULL
        CHECK (
            decision_action IN (
                'USE_EXISTING',
                'CREATE_NEW',
                'REJECT_REST'
            )
        ),

    resolved_alternative_id INTEGER
        REFERENCES alternative(alternative_id),

    concept_id_at_decision INTEGER NOT NULL
        REFERENCES concept(concept_id),

    concept_label_snapshot TEXT NOT NULL
        CHECK (length(trim(concept_label_snapshot)) > 0),

    alternative_label_snapshot TEXT,

    assignment_before_id INTEGER
        REFERENCES assignment(assignment_id),

    assignment_result_id INTEGER
        REFERENCES assignment(assignment_id),

    assignment_effect TEXT NOT NULL
        CHECK (
            assignment_effect IN (
                'CREATED',
                'REPLACED',
                'REUSED',
                'UNCHANGED'
            )
        ),

    relations_resolution TEXT NOT NULL
        CHECK (
            relations_resolution IN (
                'NOT_PROPOSED',
                'ACCEPTED',
                'REJECTED'
            )
        ),

    morphology_resolution TEXT NOT NULL
        CHECK (
            morphology_resolution IN (
                'NOT_PROPOSED',
                'ACCEPTED',
                'REJECTED'
            )
        ),

    morphology_result_id INTEGER
        REFERENCES alternative_morphology(alternative_morphology_id),

    collaborator_id INTEGER
        REFERENCES collaborator(collaborator_id),

    collaborator_name_snapshot TEXT,

    access_role TEXT NOT NULL
        CHECK (access_role IN ('reviewer', 'master')),

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        (
            decision_action = 'REJECT_REST'
            AND resolved_alternative_id IS NULL
            AND alternative_label_snapshot IS NULL
            AND assignment_effect = 'UNCHANGED'
            AND assignment_result_id IS assignment_before_id
            AND relations_resolution IN ('NOT_PROPOSED', 'REJECTED')
            AND morphology_resolution IN ('NOT_PROPOSED', 'REJECTED')
        )
        OR
        (
            decision_action IN ('USE_EXISTING', 'CREATE_NEW')
            AND resolved_alternative_id IS NOT NULL
            AND assignment_result_id IS NOT NULL
            AND assignment_effect IN ('CREATED', 'REPLACED', 'REUSED')
        )
    ),

    CHECK (
        decision_action != 'USE_EXISTING'
        OR (
            relations_resolution IN ('NOT_PROPOSED', 'REJECTED')
            AND morphology_resolution IN ('NOT_PROPOSED', 'REJECTED')
        )
    ),

    CHECK (
        decision_action != 'CREATE_NEW'
        OR (
            assignment_effect IN ('CREATED', 'REPLACED')
            AND alternative_label_snapshot IS NOT NULL
        )
    ),

    CHECK (
        (
            assignment_effect = 'CREATED'
            AND assignment_before_id IS NULL
            AND assignment_result_id IS NOT NULL
        )
        OR
        (
            assignment_effect = 'REPLACED'
            AND assignment_before_id IS NOT NULL
            AND assignment_result_id IS NOT NULL
            AND assignment_result_id != assignment_before_id
        )
        OR
        (
            assignment_effect = 'REUSED'
            AND assignment_before_id IS NOT NULL
            AND assignment_result_id IS NOT NULL
            AND assignment_result_id = assignment_before_id
        )
        OR
        (
            assignment_effect = 'UNCHANGED'
            AND assignment_result_id IS assignment_before_id
        )
    ),

    CHECK (
        (
            morphology_resolution = 'ACCEPTED'
            AND decision_action = 'CREATE_NEW'
            AND morphology_result_id IS NOT NULL
        )
        OR
        (
            morphology_resolution IN ('NOT_PROPOSED', 'REJECTED')
            AND morphology_result_id IS NULL
        )
    )
);
"""

def install(connection):
    connection.execute(SCHEMA)


class LexicalDecisionError(ValueError):
    pass


def get_decision(connection, submission_id):
    """Read stored historical snapshots, never current entity labels."""
    return connection.execute(
        'SELECT * FROM submission_lexical_decision WHERE submission_id=?',
        (submission_id,)).fetchone()


def requires_review_note(proposal_kind, proposed_existing_alternative_id,
                         decision_action, resolved_alternative_id,
                         relations_resolution, morphology_resolution):
    return (decision_action == 'REJECT_REST'
            or proposal_kind == 'UNSURE'
            or (proposal_kind == 'NEW' and decision_action == 'USE_EXISTING')
            or (proposal_kind == 'EXISTING' and (
                decision_action == 'CREATE_NEW'
                or proposed_existing_alternative_id != resolved_alternative_id))
            or 'REJECTED' in (relations_resolution, morphology_resolution))


def save_decision(connection, submission_id, decision_action, *,
                  concept_resolution_id, assignment_effect,
                  relations_resolution, morphology_resolution,
                  resolved_alternative_id=None, assignment_before_id=None,
                  assignment_result_id=None, morphology_result_id=None,
                  collaborator_id=None, access_role, review_note=None):
    """Record once within the caller's transaction, or own an atomic transaction.

    The workflow supplies already materialized IDs and later closes the submission
    and stores review_note. This function validates that note but does not store a
    draft, close the submission, or mutate its original proposal.
    """
    if access_role not in ('reviewer', 'master'):
        raise LexicalDecisionError('La decisión requiere permisos de revisión.')
    for value, allowed in (
        (decision_action, ('USE_EXISTING', 'CREATE_NEW', 'REJECT_REST')),
        (assignment_effect, ('CREATED', 'REPLACED', 'REUSED', 'UNCHANGED')),
        (relations_resolution, ('NOT_PROPOSED', 'ACCEPTED', 'REJECTED')),
        (morphology_resolution, ('NOT_PROPOSED', 'ACCEPTED', 'REJECTED')),
    ):
        if value not in allowed:
            raise LexicalDecisionError('Valor de decisión no válido.')
    owns = not connection.in_transaction
    connection.execute('BEGIN IMMEDIATE' if owns else 'SAVEPOINT lexical_decision')
    try:
        if get_decision(connection, submission_id) is not None:
            raise LexicalDecisionError('El aporte ya tiene una decisión final.')
        proposal = connection.execute('''SELECT s.*, a.proposal_kind,
            a.proposed_existing_alternative_id FROM submission s
            JOIN alternative_submission a USING(submission_id)
            WHERE submission_id=?''', (submission_id,)).fetchone()
        if proposal is None or proposal['submission_type'] != 'ALTERNATIVE' or proposal['status'] != 'pending':
            raise LexicalDecisionError('El aporte léxico debe estar pendiente.')
        concept = connection.execute('''SELECT r.*, c.preferred_label
            FROM submission_concept_resolution r JOIN concept c USING(concept_id)
            WHERE submission_concept_resolution_id=? AND submission_id=? AND is_current=1''',
            (concept_resolution_id, submission_id)).fetchone()
        if concept is None:
            raise LexicalDecisionError('Se requiere la resolución conceptual local vigente.')
        for table, resolution in (('alternative_submission_relation', relations_resolution),
                                  ('alternative_submission_morphology', morphology_resolution)):
            proposed = connection.execute(f'SELECT 1 FROM {table} WHERE submission_id=?', (submission_id,)).fetchone() is not None
            if proposed != (resolution != 'NOT_PROPOSED'):
                raise LexicalDecisionError('La resolución del grupo no corresponde a la propuesta.')
        if requires_review_note(proposal['proposal_kind'], proposal['proposed_existing_alternative_id'],
                                decision_action, resolved_alternative_id,
                                relations_resolution, morphology_resolution) and not (review_note or '').strip():
            raise LexicalDecisionError('Explique el cambio en la nota de revisión.')
        alternative_label = None
        current_assignment = connection.execute(
            'SELECT assignment_id FROM assignment WHERE occurrence_id=? AND is_current=1',
            (proposal['occurrence_id'],)).fetchone()
        if assignment_result_id != (current_assignment[0] if current_assignment else None):
            raise LexicalDecisionError('El resultado debe reflejar la asignación vigente.')
        if resolved_alternative_id is not None:
            alternative = connection.execute('SELECT * FROM alternative WHERE alternative_id=?', (resolved_alternative_id,)).fetchone()
            if alternative is None or alternative['concept_id'] != concept['concept_id']:
                raise LexicalDecisionError('La alternativa no pertenece al concepto resuelto.')
            alternative_label = (alternative['working_label'] if decision_action == 'CREATE_NEW'
                                 else alternative_display_label(concept['preferred_label'], alternative['working_label']))
        for identifier, is_result in ((assignment_before_id, False), (assignment_result_id, True)):
            if identifier is None:
                continue
            assignment = connection.execute('SELECT * FROM assignment WHERE assignment_id=?', (identifier,)).fetchone()
            if assignment is None or assignment['occurrence_id'] != proposal['occurrence_id']:
                raise LexicalDecisionError('La asignación no pertenece a la ocurrencia.')
            if is_result and (not assignment['is_current'] or (decision_action != 'REJECT_REST' and assignment['alternative_id'] != resolved_alternative_id)):
                raise LexicalDecisionError('La asignación resultante no corresponde a la decisión.')
        if morphology_result_id is not None:
            morphology = connection.execute('SELECT * FROM alternative_morphology WHERE alternative_morphology_id=?', (morphology_result_id,)).fetchone()
            if morphology is None or morphology['alternative_id'] != resolved_alternative_id or not morphology['is_current']:
                raise LexicalDecisionError('La morfología no corresponde a la alternativa resultante.')
        actor_id, actor_name = resolve_collaborator(connection, collaborator_id)
        if collaborator_id not in (None, '') and actor_id is None:
            raise LexicalDecisionError('El colaborador no existe o está inactivo.')
        connection.execute('''INSERT INTO submission_lexical_decision (
            submission_id,concept_resolution_id,decision_action,resolved_alternative_id,
            concept_id_at_decision,concept_label_snapshot,alternative_label_snapshot,
            assignment_before_id,assignment_result_id,assignment_effect,
            relations_resolution,morphology_resolution,morphology_result_id,
            collaborator_id,collaborator_name_snapshot,access_role)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            submission_id,concept_resolution_id,decision_action,resolved_alternative_id,
            concept['concept_id'],concept['preferred_label'],alternative_label,
            assignment_before_id,assignment_result_id,assignment_effect,
            relations_resolution,morphology_resolution,morphology_result_id,
            actor_id,actor_name,access_role))
        connection.commit() if owns else connection.execute('RELEASE SAVEPOINT lexical_decision')
        return submission_id
    except Exception:
        if owns:
            connection.rollback()
        else:
            connection.execute('ROLLBACK TO SAVEPOINT lexical_decision')
            connection.execute('RELEASE SAVEPOINT lexical_decision')
        raise
