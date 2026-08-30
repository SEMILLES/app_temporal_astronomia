"""Persistencia transaccional de un plan de importación de Astronomía validado."""

from __future__ import annotations

from sqlite3 import Connection

from astronomy_import_models import ValidatedImportPlan
from occurrence_grammar import create_or_replace_occurrence_grammar


IMPORT_TABLES = (
    "source_revision",
    "source_systematization",
    "occurrence_revision",
    "submission",
    "source",
    "concept",
    "alternative",
    "alternative_relation",
    "occurrence",
    "assignment",
    "occurrence_grammar",
)


class NonEmptyDatabaseError(RuntimeError):
    """La base no está disponible para una carga inicial."""


class PersistenceValidationError(RuntimeError):
    """La base persistida no coincide con el plan validado."""


def _require_empty_database(connection: Connection) -> None:
    nonempty = [
        table
        for table in IMPORT_TABLES
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        is not None
    ]
    if nonempty:
        raise NonEmptyDatabaseError(
            "La carga inicial exige una base vacía; tablas con filas: "
            + ", ".join(nonempty)
        )


def _insert_sources(connection: Connection, plan: ValidatedImportPlan) -> dict[str, int]:
    source_ids: dict[str, int] = {}
    for source in plan.sources:
        cursor = connection.execute(
            """
            INSERT INTO source (
                source_name, source_type, source_reference, start_year,
                end_year, end_year_status, legacy_source_code, source_scope,
                format_original, format_detail, region_description,
                characterization, reported_entry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.name, source.source_type, source.source_reference,
                source.start_year, source.end_year, source.end_year_status,
                source.legacy_code, source.scope, source.format_original,
                source.format_detail, source.region_description,
                source.characterization, source.reported_entry_count,
            ),
        )
        source_ids[source.key] = cursor.lastrowid
    return source_ids


def _insert_concepts(connection: Connection, plan: ValidatedImportPlan) -> dict[str, int]:
    concept_ids: dict[str, int] = {}
    for concept in plan.concepts:
        cursor = connection.execute(
            "INSERT INTO concept (preferred_label) VALUES (?)",
            (concept.preferred_label,),
        )
        concept_ids[concept.preferred_label] = cursor.lastrowid
    return concept_ids


def _insert_alternatives(
    connection: Connection,
    plan: ValidatedImportPlan,
    concept_ids: dict[str, int],
) -> dict[str, int]:
    alternative_ids: dict[str, int] = {}
    for alternative in plan.alternatives:
        cursor = connection.execute(
            """
            INSERT INTO alternative (concept_id, original_code, working_label)
            VALUES (?, ?, ?)
            """,
            (
                concept_ids[alternative.concept_label],
                alternative.original_code,
                alternative.working_label,
            ),
        )
        alternative_ids[alternative.canonical_code] = cursor.lastrowid
    return alternative_ids


def _insert_relations(
    connection: Connection,
    plan: ValidatedImportPlan,
    alternative_ids: dict[str, int],
) -> None:
    for relation in plan.relations:
        connection.execute(
            """
            INSERT INTO alternative_relation (
                alternative_a_id, alternative_b_id, phonological_parameter
            ) VALUES (?, ?, ?)
            """,
            (
                alternative_ids[relation.alternative_a],
                alternative_ids[relation.alternative_b],
                relation.parameter,
            ),
        )


def _insert_occurrences(
    connection: Connection,
    plan: ValidatedImportPlan,
    source_ids: dict[str, int],
) -> dict[str, int]:
    source_keys_by_name = {source.name: source.key for source in plan.sources}
    occurrence_ids: dict[str, int] = {}
    for occurrence in plan.occurrences:
        cursor = connection.execute(
            """
            INSERT INTO occurrence (
                legacy_occurrence_id, source_id, original_gloss, hyperlink,
                legacy_source_detail_1, legacy_source_detail_2,
                source_locator, occurrence_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurrence.legacy_occurrence_id,
                source_ids[source_keys_by_name[occurrence.source_name]],
                occurrence.original_gloss,
                occurrence.hyperlink,
                occurrence.legacy_source_detail_1,
                occurrence.legacy_source_detail_2,
                occurrence.source_locator,
                occurrence.occurrence_year,
            ),
        )
        occurrence_ids[occurrence.key] = cursor.lastrowid
    return occurrence_ids


def _insert_assignments(
    connection: Connection,
    plan: ValidatedImportPlan,
    occurrence_ids: dict[str, int],
    alternative_ids: dict[str, int],
) -> None:
    for assignment in plan.assignments:
        connection.execute(
            """
            INSERT INTO assignment (
                occurrence_id, alternative_id, is_current
            ) VALUES (?, ?, 1)
            """,
            (
                occurrence_ids[assignment.occurrence_key],
                alternative_ids[assignment.alternative_code],
            ),
        )


def _insert_grammar(
    connection: Connection,
    plan: ValidatedImportPlan,
    occurrence_ids: dict[str, int],
) -> None:
    for grammar in plan.grammar:
        create_or_replace_occurrence_grammar(
            connection,
            occurrence_ids[grammar.occurrence_key],
            gender=grammar.gender,
            plural=grammar.plural,
            agentive=grammar.agentive,
            conjugated_form=grammar.conjugated_form,
            negation=grammar.negation,
            grammar_note=grammar.grammar_note,
        )


def _count(connection: Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _validate_persisted_plan(
    connection: Connection, plan: ValidatedImportPlan
) -> None:
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise PersistenceValidationError(
            f"foreign_key_check encontró {len(foreign_key_errors)} errores"
        )

    expected_counts = {
        "source": len(plan.sources),
        "concept": len(plan.concepts),
        "alternative": len(plan.alternatives),
        "alternative_relation": len(plan.relations),
        "occurrence": len(plan.occurrences),
        "assignment": len(plan.assignments),
        "occurrence_grammar": len(plan.grammar),
    }
    for table, expected in expected_counts.items():
        actual = _count(connection, table)
        if actual != expected:
            raise PersistenceValidationError(
                f"{table}: se esperaban {expected} filas y se persistieron {actual}"
            )

    for table in (
        "submission",
        "source_revision",
        "source_systematization",
        "occurrence_revision",
    ):
        if _count(connection, table) != 0:
            raise PersistenceValidationError(
                f"{table}: la carga inicial no debe crear filas"
            )

    duplicate_assignments = connection.execute(
        """
        SELECT occurrence_id
        FROM assignment
        WHERE is_current = 1
        GROUP BY occurrence_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicate_assignments:
        raise PersistenceValidationError(
            "existen múltiples assignments current para una occurrence"
        )

    duplicate_grammar = connection.execute(
        """
        SELECT occurrence_id
        FROM occurrence_grammar
        WHERE is_current = 1
        GROUP BY occurrence_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicate_grammar:
        raise PersistenceValidationError(
            "existen múltiples occurrence_grammar current para una occurrence"
        )


def persist_validated_plan(
    connection: Connection, plan: ValidatedImportPlan
) -> None:
    """Persiste atómicamente un plan validado en una base inicial vacía."""
    if not isinstance(plan, ValidatedImportPlan):
        raise TypeError("plan debe ser una instancia de ValidatedImportPlan")
    if connection.in_transaction:
        raise RuntimeError(
            "persist_validated_plan requiere una conexión sin transacción activa"
        )

    connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if foreign_keys != 1:
        raise PersistenceValidationError(
            "no fue posible activar PRAGMA foreign_keys"
        )

    try:
        connection.execute("BEGIN IMMEDIATE")
        _require_empty_database(connection)
        source_ids = _insert_sources(connection, plan)
        concept_ids = _insert_concepts(connection, plan)
        alternative_ids = _insert_alternatives(connection, plan, concept_ids)
        _insert_relations(connection, plan, alternative_ids)
        occurrence_ids = _insert_occurrences(connection, plan, source_ids)
        _insert_assignments(
            connection, plan, occurrence_ids, alternative_ids
        )
        _insert_grammar(connection, plan, occurrence_ids)
        _validate_persisted_plan(connection, plan)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
