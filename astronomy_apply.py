"""Preparación segura del destino SQLite para el importador de Astronomía."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from astronomy_persistence import IMPORT_TABLES, persist_validated_plan
from database import crear_esquema


class ExistingDatabaseError(RuntimeError):
    """El archivo existente no es una SQLite compatible."""


def _canonical_sql_fragment(value: str) -> str:
    result = []
    in_literal = False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "'":
            result.append(character)
            if in_literal and index + 1 < len(value) and value[index + 1] == "'":
                result.append("'")
                index += 2
                continue
            in_literal = not in_literal
        elif in_literal:
            result.append(character)
        elif not character.isspace():
            result.append(character.lower())
        index += 1
    return "".join(result)


def _check_constraints(create_sql: str) -> tuple[str, ...]:
    constraints = []
    upper = create_sql.upper()
    position = 0
    while True:
        position = upper.find("CHECK", position)
        if position < 0:
            break
        opening = position + len("CHECK")
        while opening < len(create_sql) and create_sql[opening].isspace():
            opening += 1
        if opening >= len(create_sql) or create_sql[opening] != "(":
            position = opening
            continue
        depth = 1
        in_literal = False
        cursor = opening + 1
        while cursor < len(create_sql) and depth:
            character = create_sql[cursor]
            if character == "'":
                if (
                    in_literal
                    and cursor + 1 < len(create_sql)
                    and create_sql[cursor + 1] == "'"
                ):
                    cursor += 2
                    continue
                in_literal = not in_literal
            elif not in_literal:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
            cursor += 1
        if depth:
            raise ExistingDatabaseError("SQLite existente con CHECK mal formado")
        constraints.append(
            _canonical_sql_fragment(create_sql[opening + 1:cursor - 1])
        )
        position = cursor
    return tuple(sorted(constraints))


def _table_metadata(connection: sqlite3.Connection, table: str) -> tuple:
    columns = tuple(
        (
            row[1],
            (row[2] or "").upper(),
            row[3],
            row[4],
            row[5],
        )
        for row in connection.execute(f"PRAGMA table_info({table})")
    )
    foreign_keys = tuple(
        sorted(
            (
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        )
    )
    create_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()[0]
    return columns, foreign_keys, _check_constraints(create_sql)


def _index_metadata(connection: sqlite3.Connection, table: str) -> tuple:
    indexes = []
    for row in connection.execute(f"PRAGMA index_list({table})"):
        name, unique, origin, partial = row[1], row[2], row[3], row[4]
        index_rows = tuple(
            connection.execute(f"PRAGMA index_xinfo({name})")
        )
        keys = tuple(
            (item[1], item[2], item[3], item[4], item[5])
            for item in index_rows
            if item[5]
        )
        semantic_sql = None
        if partial or any(item[1] == -2 for item in index_rows):
            sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            semantic_sql = _canonical_sql_fragment(sql_row[0])
        indexes.append(
            (name, unique, origin, partial, keys, semantic_sql)
        )
    return tuple(sorted(indexes))


def _required_schema_metadata(connection: sqlite3.Connection) -> dict:
    available = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(set(IMPORT_TABLES) - available)
    if missing:
        raise ExistingDatabaseError(
            "SQLite existente sin schema vigente; faltan tablas: "
            + ", ".join(missing)
        )
    return {
        table: (
            _table_metadata(connection, table),
            _index_metadata(connection, table),
        )
        for table in IMPORT_TABLES
    }


def _reference_schema_metadata() -> dict:
    reference = sqlite3.connect(":memory:")
    try:
        crear_esquema(reference)
        return _required_schema_metadata(reference)
    finally:
        reference.close()


def validate_existing_database(database_path: str | Path) -> None:
    """Valida en modo read-only una SQLite existente, sin ejecutar DDL."""
    path = Path(database_path)
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            actual = _required_schema_metadata(connection)
        finally:
            connection.close()
    except ExistingDatabaseError:
        raise
    except sqlite3.DatabaseError as exc:
        raise ExistingDatabaseError(
            "El archivo existente no es una SQLite válida"
        ) from exc

    expected = _reference_schema_metadata()
    incompatible = []
    for table in IMPORT_TABLES:
        actual_table, actual_indexes = actual[table]
        expected_table, expected_indexes = expected[table]
        if actual_table != expected_table or not set(expected_indexes).issubset(
            actual_indexes
        ):
            incompatible.append(table)
    if incompatible:
        raise ExistingDatabaseError(
            "SQLite existente con schema incompatible: "
            + ", ".join(incompatible)
        )


def apply_validated_plan_to_database(plan, database_path: str | Path) -> None:
    """Crea o valida el destino y delega la transacción al persistence layer."""
    path = Path(database_path)
    existed = path.exists()
    created_here = False
    connection = None
    try:
        if existed:
            validate_existing_database(path)
        else:
            with path.open("xb"):
                pass
            created_here = True
        connection = sqlite3.connect(path)
        if not existed:
            crear_esquema(connection)
            connection.commit()
        persist_validated_plan(connection, plan)
    except Exception:
        if connection is not None:
            connection.close()
            connection = None
        if created_here and path.exists() and path.is_file():
            path.unlink()
        raise
    finally:
        if connection is not None:
            connection.close()
