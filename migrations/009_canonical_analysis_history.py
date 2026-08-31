import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_009.db"


ASSIGNMENT_COLUMNS = {
    "assignment_id", "occurrence_id", "alternative_id", "is_current",
    "supersedes_assignment_id", "created_at", "created_by",
    "created_from_submission_id",
}
GRAMMAR_COLUMNS = {
    "occurrence_grammar_id", "occurrence_id", "gender",
    "gender_uncertain", "plural", "plural_uncertain", "agentive",
    "agentive_uncertain", "conjugated_form", "conjugated_form_uncertain",
    "negation", "negation_uncertain", "grammar_note", "is_current",
    "supersedes_occurrence_grammar_id", "created_at", "created_by",
    "change_note", "created_from_submission_id",
}
RELATION_COLUMNS = {
    "alternative_relation_id", "alternative_low_id", "alternative_high_id",
    "phonological_parameter", "is_current",
    "supersedes_alternative_relation_id", "created_from_submission_id",
    "created_at", "created_by",
}


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def indexes(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}


def migration_is_complete(connection):
    return (
        ASSIGNMENT_COLUMNS == columns(connection, "assignment")
        and GRAMMAR_COLUMNS == columns(connection, "occurrence_grammar")
        and RELATION_COLUMNS == columns(connection, "alternative_relation")
        and {
            "one_current_assignment_per_occurrence",
            "idx_assignment_alternative",
            "idx_assignment_occurrence",
        } <= indexes(connection, "assignment")
        and {
            "one_current_grammar_per_occurrence",
            "idx_occurrence_grammar_occurrence",
        } <= indexes(connection, "occurrence_grammar")
        and {
            "one_current_alternative_relation_per_parameter",
            "idx_alternative_relation_low",
            "idx_alternative_relation_high",
        } <= indexes(connection, "alternative_relation")
    )


def _execute_schema(connection, schema):
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)


def _create_assignment(connection):
    _execute_schema(connection, """
        CREATE TABLE assignment (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            alternative_id INTEGER NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_assignment_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            created_from_submission_id INTEGER,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_assignment_id)
                REFERENCES assignment(assignment_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id)
        );
        CREATE UNIQUE INDEX one_current_assignment_per_occurrence
            ON assignment(occurrence_id) WHERE is_current = 1;
        CREATE INDEX idx_assignment_alternative
            ON assignment(alternative_id);
        CREATE INDEX idx_assignment_occurrence
            ON assignment(occurrence_id);
    """)


def _create_grammar(connection):
    _execute_schema(connection, """
        CREATE TABLE occurrence_grammar (
            occurrence_grammar_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            gender TEXT,
            gender_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (gender_uncertain IN (0, 1)
                    AND (gender IS NOT NULL OR gender_uncertain = 0)),
            plural TEXT,
            plural_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (plural_uncertain IN (0, 1)
                    AND (plural IS NOT NULL OR plural_uncertain = 0)),
            agentive TEXT,
            agentive_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (agentive_uncertain IN (0, 1)
                    AND (agentive IS NOT NULL OR agentive_uncertain = 0)),
            conjugated_form TEXT,
            conjugated_form_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (conjugated_form_uncertain IN (0, 1)
                    AND (conjugated_form IS NOT NULL
                        OR conjugated_form_uncertain = 0)),
            negation TEXT,
            negation_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (negation_uncertain IN (0, 1)
                    AND (negation IS NOT NULL OR negation_uncertain = 0)),
            grammar_note TEXT,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_occurrence_grammar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            change_note TEXT,
            created_from_submission_id INTEGER,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (supersedes_occurrence_grammar_id)
                REFERENCES occurrence_grammar(occurrence_grammar_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id)
        );
        CREATE UNIQUE INDEX one_current_grammar_per_occurrence
            ON occurrence_grammar(occurrence_id) WHERE is_current = 1;
        CREATE INDEX idx_occurrence_grammar_occurrence
            ON occurrence_grammar(occurrence_id);
    """)


def _create_relations(connection):
    _execute_schema(connection, """
        CREATE TABLE alternative_relation (
            alternative_relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_low_id INTEGER NOT NULL,
            alternative_high_id INTEGER NOT NULL,
            phonological_parameter TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_alternative_relation_id INTEGER,
            created_from_submission_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (alternative_low_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (alternative_high_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_alternative_relation_id)
                REFERENCES alternative_relation(alternative_relation_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id),
            CHECK (alternative_low_id < alternative_high_id)
        );
        CREATE UNIQUE INDEX one_current_alternative_relation_per_parameter
            ON alternative_relation(
                alternative_low_id, alternative_high_id,
                phonological_parameter
            ) WHERE is_current = 1;
        CREATE INDEX idx_alternative_relation_low
            ON alternative_relation(alternative_low_id);
        CREATE INDEX idx_alternative_relation_high
            ON alternative_relation(alternative_high_id);
    """)


def _legacy_relation_problem(connection):
    null_parameter = connection.execute(
        "SELECT alternative_relation_id FROM alternative_relation "
        "WHERE phonological_parameter IS NULL LIMIT 1"
    ).fetchone()
    if null_parameter is not None:
        return (
            "La relation legacy " + str(null_parameter[0])
            + " no tiene phonological_parameter"
        )
    duplicate = connection.execute("""
        SELECT
            MIN(alternative_a_id, alternative_b_id) AS low_id,
            MAX(alternative_a_id, alternative_b_id) AS high_id,
            phonological_parameter,
            COUNT(*)
        FROM alternative_relation
        GROUP BY low_id, high_id, phonological_parameter
        HAVING COUNT(*) > 1
        LIMIT 1
    """).fetchone()
    if duplicate is not None:
        return (
            "La normalizacion produciria relaciones duplicadas: "
            f"{tuple(duplicate)!r}"
        )
    return None


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")

    inspection = sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection):
            return False
        if "submission_type" not in columns(inspection, "submission"):
            raise RuntimeError("La base no tiene el esquema post-008")
        if not {
            "alternative_a_id", "alternative_b_id", "phonological_parameter"
        } <= columns(inspection, "alternative_relation"):
            raise RuntimeError("La base no tiene alternative_relation legacy")
        relation_problem = _legacy_relation_problem(inspection)
        if relation_problem is not None:
            raise RuntimeError(relation_problem)
    finally:
        inspection.close()

    if backup_path is not None:
        if backup_path.exists():
            raise SystemExit(
                f"El backup ya existe y no se sobrescribira: {backup_path}"
            )
        shutil.copy2(database_path, backup_path)

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "assignment", "occurrence_grammar", "alternative_relation"
            )
        }

        connection.execute("ALTER TABLE assignment RENAME TO assignment_legacy_009")
        for name in (
            "one_current_assignment_per_occurrence",
            "idx_assignment_alternative",
            "idx_assignment_occurrence",
        ):
            connection.execute(f"DROP INDEX IF EXISTS {name}")
        _create_assignment(connection)
        connection.execute("""
            INSERT INTO assignment (
                assignment_id, occurrence_id, alternative_id, is_current,
                supersedes_assignment_id, created_at, created_by,
                created_from_submission_id
            )
            SELECT
                assignment_id, occurrence_id, alternative_id, is_current,
                supersedes_assignment_id, created_at, created_by, NULL
            FROM assignment_legacy_009
        """)

        connection.execute(
            "ALTER TABLE occurrence_grammar "
            "RENAME TO occurrence_grammar_legacy_009"
        )
        for name in (
            "one_current_grammar_per_occurrence",
            "idx_occurrence_grammar_occurrence",
        ):
            connection.execute(f"DROP INDEX IF EXISTS {name}")
        _create_grammar(connection)
        connection.execute("""
            INSERT INTO occurrence_grammar (
                occurrence_grammar_id, occurrence_id,
                gender, gender_uncertain, plural, plural_uncertain,
                agentive, agentive_uncertain,
                conjugated_form, conjugated_form_uncertain,
                negation, negation_uncertain, grammar_note, is_current,
                supersedes_occurrence_grammar_id, created_at, created_by,
                change_note, created_from_submission_id
            )
            SELECT
                occurrence_grammar_id, occurrence_id,
                gender, 0, plural, 0, agentive, 0,
                conjugated_form, 0, negation, 0, grammar_note, is_current,
                supersedes_occurrence_grammar_id, created_at, created_by,
                change_note, NULL
            FROM occurrence_grammar_legacy_009
        """)

        connection.execute(
            "ALTER TABLE alternative_relation "
            "RENAME TO alternative_relation_legacy_009"
        )
        connection.execute("DROP INDEX IF EXISTS one_symmetric_alternative_relation")
        _create_relations(connection)
        connection.execute("""
            INSERT INTO alternative_relation (
                alternative_relation_id, alternative_low_id,
                alternative_high_id, phonological_parameter, is_current,
                supersedes_alternative_relation_id,
                created_from_submission_id, created_at, created_by
            )
            SELECT
                alternative_relation_id,
                MIN(alternative_a_id, alternative_b_id),
                MAX(alternative_a_id, alternative_b_id),
                phonological_parameter, 1, NULL, NULL, created_at, created_by
            FROM alternative_relation_legacy_009
        """)

        for table in (
            "assignment_legacy_009",
            "occurrence_grammar_legacy_009",
            "alternative_relation_legacy_009",
        ):
            connection.execute(f"DROP TABLE {table}")

        for table, expected in counts.items():
            actual = connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    f"La migracion no preservo filas de {table}: "
                    f"{expected} != {actual}"
                )
        if not migration_is_complete(connection):
            raise RuntimeError("La migracion 009 quedo incompleta")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"La migracion produjo foreign keys invalidas: {violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return True


if __name__ == "__main__":
    changed = migrate()
    if changed:
        print("Migracion 009 completada. Backup conservado en:", BACKUP_PATH)
    else:
        print("Migracion 009 ya estaba aplicada; no se realizaron cambios.")
