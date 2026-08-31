class OccurrenceNotFoundError(ValueError):
    pass


class EmptyGrammarError(ValueError):
    pass


class InvalidGrammarUncertaintyError(ValueError):
    pass


CONTENT_COLUMNS = (
    "gender",
    "plural",
    "agentive",
    "conjugated_form",
    "negation",
    "grammar_note",
)

UNCERTAINTY_COLUMNS = tuple(
    column + "_uncertain" for column in CONTENT_COLUMNS[:-1]
)


def _empty_string_to_none(value):
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _uncertainty_flag(value, field, content):
    if value not in (0, 1, False, True):
        raise InvalidGrammarUncertaintyError(
            f"{field}_uncertain debe ser 0 o 1."
        )
    flag = int(value)
    if content is None and flag:
        raise InvalidGrammarUncertaintyError(
            f"{field}_uncertain no puede ser 1 si {field} es NULL."
        )
    return flag


def create_or_replace_occurrence_grammar(
    connection,
    occurrence_id,
    gender=None,
    plural=None,
    agentive=None,
    conjugated_form=None,
    negation=None,
    grammar_note=None,
    gender_uncertain=0,
    plural_uncertain=0,
    agentive_uncertain=0,
    conjugated_form_uncertain=0,
    negation_uncertain=0,
    created_by=None,
    change_note=None,
    created_from_submission_id=None,
):
    text_content = tuple(_empty_string_to_none(value) for value in (
        gender,
        plural,
        agentive,
        conjugated_form,
        negation,
        grammar_note,
    ))
    flags = tuple(
        _uncertainty_flag(flag, field, value)
        for flag, field, value in zip(
            (
                gender_uncertain,
                plural_uncertain,
                agentive_uncertain,
                conjugated_form_uncertain,
                negation_uncertain,
            ),
            CONTENT_COLUMNS[:-1],
            text_content[:-1],
        )
    )
    content = (
        text_content[0], flags[0],
        text_content[1], flags[1],
        text_content[2], flags[2],
        text_content[3], flags[3],
        text_content[4], flags[4],
        text_content[5],
    )
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
        if not any(value is not None for value in text_content):
            raise EmptyGrammarError(
                "El análisis gramatical debe contener al menos un dato."
            )

        current = connection.execute("""
            SELECT occurrence_grammar_id,
                   gender, gender_uncertain,
                   plural, plural_uncertain,
                   agentive, agentive_uncertain,
                   conjugated_form, conjugated_form_uncertain,
                   negation, negation_uncertain, grammar_note
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
                occurrence_id,
                gender, gender_uncertain,
                plural, plural_uncertain,
                agentive, agentive_uncertain,
                conjugated_form, conjugated_form_uncertain,
                negation, negation_uncertain, grammar_note, is_current,
                supersedes_occurrence_grammar_id, created_by, change_note,
                created_from_submission_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """, (
            occurrence_id, *content, supersedes_id, created_by, change_note,
            created_from_submission_id,
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
