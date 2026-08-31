from grammatical_marks import validate_grammatical_marks
from occurrence_grammar import create_or_replace_occurrence_grammar


FIELDS = ("gender", "plural", "agentive", "conjugated_form", "negation")


class GrammarWorkflowError(ValueError):
    pass


def create_grammar_submission(connection, occurrence_id, values, *, submitted_by=None):
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
    try:
        connection.execute("BEGIN IMMEDIATE")
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
        connection.commit()
        return submission_id
    except Exception:
        connection.rollback()
        raise


def resolve_grammar_submission(connection, submission_id, decision, *, reviewed_by=None, review_note=None):
    if decision not in ("accepted", "rejected"):
        raise GrammarWorkflowError("Decisión no válida.")
    try:
        connection.execute("BEGIN IMMEDIATE")
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
        connection.commit()
    except Exception:
        connection.rollback()
        raise
