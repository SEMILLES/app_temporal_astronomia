from grammatical_marks import validate_grammatical_marks
from occurrence_grammar import create_or_replace_occurrence_grammar
from activity import record_activity, resolve_collaborator
from conflicts import detect_conflicts_after_change


FIELDS = ("gender", "plural", "agentive", "conjugated_form", "negation")


class GrammarWorkflowError(ValueError):
    pass


def _transaction(connection,name):
    owns=not connection.in_transaction
    connection.execute("BEGIN IMMEDIATE" if owns else f"SAVEPOINT {name}")
    return owns


def _finish(connection,name,owns):
    connection.commit() if owns else connection.execute(f"RELEASE SAVEPOINT {name}")


def _rollback(connection,name,owns):
    if owns: connection.rollback()
    else:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")


def create_grammar_submission(connection, occurrence_id, values, *, submitted_by=None,
                              collaborator_id=None, access_role=None):
    current = connection.execute(
        "SELECT * FROM occurrence_grammar WHERE occurrence_id = ? AND is_current = 1",
        (occurrence_id,),
    ).fetchone()
    if connection.execute("SELECT 1 FROM occurrence WHERE occurrence_id = ?", (occurrence_id,)).fetchone() is None:
        raise GrammarWorkflowError("La occurrence no existe.")
    legacy = {field: current[field] for field in FIELDS} if current else {}
    marks = validate_grammatical_marks(values, legacy)
    if not any(marks.values()):
        raise GrammarWorkflowError("Debe proponer al menos un campo gramatical.")
    flags = {}
    for field in FIELDS:
        flag = 1 if str(values.get(field + "_uncertain", "")).lower() in ("1", "true", "on", "yes") else 0
        if marks[field] is None and flag:
            raise GrammarWorkflowError("Un campo sin analizar no puede marcarse con duda.")
        flags[field] = flag
    name="create_grammar_submission";owns=_transaction(connection,name)
    try:
        cursor = connection.execute(
            "INSERT INTO submission (occurrence_id, submission_type, status, submitted_by) "
            "VALUES (?, 'GRAMMAR', 'pending', ?)", (occurrence_id, submitted_by)
        )
        submission_id = cursor.lastrowid
        columns = []
        params = []
        for field in FIELDS:
            columns.extend((field, field + "_uncertain"))
            params.extend((marks[field], flags[field]))
        columns.append("note")
        params.append((values.get("note") or "").strip() or None)
        connection.execute(
            f"INSERT INTO grammar_submission (submission_id, {', '.join(columns)}) "
            f"VALUES (?, {', '.join('?' for _ in params)})",
            (submission_id, *params),
        )
        if access_role:
            record_activity(connection, "grammar_submission_created",
                            entity_type="submission", entity_id=submission_id,
                            collaborator_id=collaborator_id, access_role=access_role)
        _finish(connection,name,owns)
        return submission_id
    except Exception:
        _rollback(connection,name,owns)
        raise


def resolve_grammar_submission(connection, submission_id, decision, *, reviewed_by=None,
                               review_note=None, collaborator_id=None, access_role=None):
    if decision not in ("accepted", "rejected"):
        raise GrammarWorkflowError("Decisión no válida.")
    name="resolve_grammar_submission";owns=_transaction(connection,name)
    try:
        row = connection.execute(
            "SELECT s.*, gs.* FROM submission s JOIN grammar_submission gs USING (submission_id) "
            "WHERE s.submission_id = ? AND s.submission_type = 'GRAMMAR' AND s.status = 'pending'",
            (submission_id,),
        ).fetchone()
        if row is None:
            raise GrammarWorkflowError("La propuesta gramatical no está pendiente.")
        if decision == "accepted":
            kwargs = {field: row[field] for field in FIELDS}
            kwargs.update({field + "_uncertain": row[field + "_uncertain"] for field in FIELDS})
            create_or_replace_occurrence_grammar(
                connection, row["occurrence_id"], **kwargs,
                grammar_note=row["note"], created_by=reviewed_by,
                change_note=review_note, created_from_submission_id=submission_id,
                force_new=True,
            )
        connection.execute(
            "UPDATE submission SET status = 'resolved', resolution = ?, "
            "resolved_at = CURRENT_TIMESTAMP, reviewed_by = ?, review_note = ? "
            "WHERE submission_id = ?",
            (decision, reviewed_by, (review_note or "").strip() or None, submission_id),
        )
        if access_role:
            record_activity(connection,
                            "grammar_submission_accepted" if decision == "accepted" else "grammar_submission_rejected",
                            entity_type="submission", entity_id=submission_id,
                            collaborator_id=collaborator_id, access_role=access_role,
                            comment=review_note)
        detect_conflicts_after_change(connection,"occurrence",row["occurrence_id"],
            actor_context={"collaborator_id":collaborator_id,"access_role":access_role})
        _finish(connection,name,owns)
    except Exception:
        _rollback(connection,name,owns)
        raise
