class OccurrenceNotFoundError(ValueError):
    pass


class AlternativeNotFoundError(ValueError):
    pass


def create_or_replace_assignment(
    connection,
    occurrence_id,
    alternative_id,
    created_by=None,
    created_from_submission_id=None,
):
    owns_transaction = not connection.in_transaction
    savepoint = "assignment_operation"
    if owns_transaction:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {savepoint}")
    try:
        if connection.execute(
            "SELECT 1 FROM occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone() is None:
            raise OccurrenceNotFoundError("La ocurrencia no existe.")
        if connection.execute(
            "SELECT 1 FROM alternative WHERE alternative_id = ?",
            (alternative_id,),
        ).fetchone() is None:
            raise AlternativeNotFoundError("La alternativa no existe.")

        current = connection.execute("""
            SELECT assignment_id, alternative_id
            FROM assignment
            WHERE occurrence_id = ? AND is_current = 1
        """, (occurrence_id,)).fetchone()
        if current is not None and current[1] == alternative_id:
            if owns_transaction:
                connection.commit()
            else:
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return current[0], False

        supersedes_id = current[0] if current is not None else None
        if supersedes_id is not None:
            connection.execute(
                "UPDATE assignment SET is_current = 0 "
                "WHERE assignment_id = ?",
                (supersedes_id,),
            )
        cursor = connection.execute("""
            INSERT INTO assignment (
                occurrence_id, alternative_id, is_current,
                supersedes_assignment_id, created_by,
                created_from_submission_id
            ) VALUES (?, ?, 1, ?, ?, ?)
        """, (
            occurrence_id, alternative_id, supersedes_id, created_by,
            created_from_submission_id,
        ))
        from conflicts import detect_conflicts_after_change
        detect_conflicts_after_change(connection,"assignment",cursor.lastrowid)
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return cursor.lastrowid, True
    except Exception:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
