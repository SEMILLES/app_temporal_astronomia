class SelfRelationError(ValueError):
    pass


class DuplicateCurrentRelationError(ValueError):
    pass


class RelationNotFoundError(ValueError):
    pass


class InvalidPhonologicalParameterError(ValueError):
    pass


def normalize_alternative_pair(alternative_a_id, alternative_b_id):
    if alternative_a_id == alternative_b_id:
        raise SelfRelationError("Una alternativa no puede relacionarse consigo misma.")
    return tuple(sorted((alternative_a_id, alternative_b_id)))


def _parameter(value):
    if value is None or not str(value).strip():
        raise InvalidPhonologicalParameterError(
            "La relacion requiere un parametro fonologico."
        )
    return value


def current_relation(connection, alternative_a_id, alternative_b_id, parameter):
    low_id, high_id = normalize_alternative_pair(
        alternative_a_id, alternative_b_id
    )
    return connection.execute("""
        SELECT *
        FROM alternative_relation
        WHERE alternative_low_id = ?
          AND alternative_high_id = ?
          AND phonological_parameter = ?
          AND is_current = 1
    """, (low_id, high_id, _parameter(parameter))).fetchone()


def _transaction(connection, savepoint):
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {savepoint}")
    return owns_transaction


def _finish(connection, savepoint, owns_transaction):
    if owns_transaction:
        connection.commit()
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def _rollback(connection, savepoint, owns_transaction):
    if owns_transaction:
        connection.rollback()
    else:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def create_current_relation(
    connection,
    alternative_a_id,
    alternative_b_id,
    phonological_parameter,
    created_by=None,
    created_from_submission_id=None,
):
    low_id, high_id = normalize_alternative_pair(
        alternative_a_id, alternative_b_id
    )
    parameter = _parameter(phonological_parameter)
    savepoint = "create_alternative_relation"
    owns_transaction = _transaction(connection, savepoint)
    try:
        if current_relation(connection, low_id, high_id, parameter) is not None:
            raise DuplicateCurrentRelationError(
                "Ya existe una relacion current para el par y parametro."
            )
        cursor = connection.execute("""
            INSERT INTO alternative_relation (
                alternative_low_id, alternative_high_id,
                phonological_parameter, is_current, created_by,
                created_from_submission_id
            ) VALUES (?, ?, ?, 1, ?, ?)
        """, (
            low_id, high_id, parameter, created_by,
            created_from_submission_id,
        ))
        _finish(connection, savepoint, owns_transaction)
        return cursor.lastrowid
    except Exception:
        _rollback(connection, savepoint, owns_transaction)
        raise


def replace_current_relation(
    connection,
    previous_relation_id,
    alternative_a_id=None,
    alternative_b_id=None,
    phonological_parameter=None,
    created_by=None,
    created_from_submission_id=None,
):
    savepoint = "replace_alternative_relation"
    owns_transaction = _transaction(connection, savepoint)
    try:
        previous = connection.execute("""
            SELECT alternative_low_id, alternative_high_id,
                   phonological_parameter
            FROM alternative_relation
            WHERE alternative_relation_id = ? AND is_current = 1
        """, (previous_relation_id,)).fetchone()
        if previous is None:
            raise RelationNotFoundError("La relacion current no existe.")
        a_id = previous[0] if alternative_a_id is None else alternative_a_id
        b_id = previous[1] if alternative_b_id is None else alternative_b_id
        low_id, high_id = normalize_alternative_pair(a_id, b_id)
        parameter = _parameter(
            previous[2] if phonological_parameter is None
            else phonological_parameter
        )
        duplicate = current_relation(connection, low_id, high_id, parameter)
        if duplicate is not None and duplicate[0] != previous_relation_id:
            raise DuplicateCurrentRelationError(
                "Ya existe otra relacion current para el par y parametro."
            )
        connection.execute(
            "UPDATE alternative_relation SET is_current = 0 "
            "WHERE alternative_relation_id = ?",
            (previous_relation_id,),
        )
        cursor = connection.execute("""
            INSERT INTO alternative_relation (
                alternative_low_id, alternative_high_id,
                phonological_parameter, is_current,
                supersedes_alternative_relation_id,
                created_by, created_from_submission_id
            ) VALUES (?, ?, ?, 1, ?, ?, ?)
        """, (
            low_id, high_id, parameter, previous_relation_id,
            created_by, created_from_submission_id,
        ))
        _finish(connection, savepoint, owns_transaction)
        return cursor.lastrowid
    except Exception:
        _rollback(connection, savepoint, owns_transaction)
        raise


def retire_current_relation(connection, relation_id):
    savepoint = "retire_alternative_relation"
    owns_transaction = _transaction(connection, savepoint)
    try:
        cursor = connection.execute(
            "UPDATE alternative_relation SET is_current = 0 "
            "WHERE alternative_relation_id = ? AND is_current = 1",
            (relation_id,),
        )
        if cursor.rowcount != 1:
            raise RelationNotFoundError("La relacion current no existe.")
        _finish(connection, savepoint, owns_transaction)
    except Exception:
        _rollback(connection, savepoint, owns_transaction)
        raise


def list_current_relations(connection, *, alternative_id=None, concept_id=None):
    if (alternative_id is None) == (concept_id is None):
        raise ValueError("Indica exactamente alternative_id o concept_id.")
    if alternative_id is not None:
        return connection.execute("""
            SELECT * FROM alternative_relation
            WHERE is_current = 1
              AND (alternative_low_id = ? OR alternative_high_id = ?)
            ORDER BY alternative_low_id, alternative_high_id,
                     phonological_parameter
        """, (alternative_id, alternative_id)).fetchall()
    return connection.execute("""
        SELECT relation.*
        FROM alternative_relation AS relation
        JOIN alternative AS low
          ON low.alternative_id = relation.alternative_low_id
        JOIN alternative AS high
          ON high.alternative_id = relation.alternative_high_id
        WHERE relation.is_current = 1
          AND (low.concept_id = ? OR high.concept_id = ?)
        ORDER BY relation.alternative_low_id, relation.alternative_high_id,
                 relation.phonological_parameter
    """, (concept_id, concept_id)).fetchall()
