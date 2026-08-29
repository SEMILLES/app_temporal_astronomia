class OccurrenceNotFoundError(ValueError):
    pass


class EmptyGrammarError(ValueError):
    pass


CONTENT_COLUMNS = (
    "gender",
    "plural",
    "agentive",
    "conjugated_form",
    "negation",
    "grammar_note",
)


def _empty_string_to_none(value):
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def create_or_replace_occurrence_grammar(
    connection,
    occurrence_id,
    gender=None,
    plural=None,
    agentive=None,
    conjugated_form=None,
    negation=None,
    grammar_note=None,
    created_by=None,
    change_note=None,
):
    content = tuple(_empty_string_to_none(value) for value in (
        gender,
        plural,
        agentive,
        conjugated_form,
        negation,
        grammar_note,
    ))
    created_by = _empty_string_to_none(created_by)
    change_note = _empty_string_to_none(change_note)

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("PRAGMA foreign_keys = ON")
    try:
        if owns_transaction:
            connection.execute("BEGIN IMMEDIATE")
        else:
            connection.execute("SAVEPOINT occurrence_grammar_operation")
        occurrence = connection.execute(
            "SELECT 1 FROM occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        if occurrence is None:
            raise OccurrenceNotFoundError("La ocurrencia no existe.")
        if not any(value is not None for value in content):
            raise EmptyGrammarError(
                "El análisis gramatical debe contener al menos un dato."
            )

        current = connection.execute("""
            SELECT occurrence_grammar_id, gender, plural, agentive,
                   conjugated_form, negation, grammar_note
            FROM occurrence_grammar
            WHERE occurrence_id = ? AND is_current = 1
        """, (occurrence_id,)).fetchone()

        if current is not None and tuple(current[1:]) == content:
            if owns_transaction:
                connection.commit()
            else:
                connection.execute("RELEASE SAVEPOINT occurrence_grammar_operation")
            return current[0], False

        supersedes_id = None
        if current is not None:
            supersedes_id = current[0]
            connection.execute("""
                UPDATE occurrence_grammar
                SET is_current = 0
                WHERE occurrence_grammar_id = ?
            """, (supersedes_id,))

        cursor = connection.execute("""
            INSERT INTO occurrence_grammar (
                occurrence_id, gender, plural, agentive, conjugated_form,
                negation, grammar_note, is_current,
                supersedes_occurrence_grammar_id, created_by, change_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (
            occurrence_id, *content, supersedes_id, created_by, change_note
        ))
        if owns_transaction:
            connection.commit()
        else:
            connection.execute("RELEASE SAVEPOINT occurrence_grammar_operation")
        return cursor.lastrowid, True
    except Exception:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute(
                "ROLLBACK TO SAVEPOINT occurrence_grammar_operation"
            )
            connection.execute(
                "RELEASE SAVEPOINT occurrence_grammar_operation"
            )
        raise
