import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_007.db"


GRAMMAR_COLUMNS = {
    "occurrence_grammar_id",
    "occurrence_id",
    "gender",
    "plural",
    "agentive",
    "conjugated_form",
    "negation",
    "grammar_note",
    "is_current",
    "supersedes_occurrence_grammar_id",
    "created_at",
    "created_by",
    "change_note",
}

GRAMMAR_INDEXES = {
    "one_current_grammar_per_occurrence",
    "idx_occurrence_grammar_occurrence",
}

LEGACY_OCCURRENCE_COLUMNS = {
    "legacy_occurrence_id",
    "legacy_source_detail_1",
    "legacy_source_detail_2",
}


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def indexes(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}


def migration_is_complete(connection):
    return (
        GRAMMAR_COLUMNS <= columns(connection, "occurrence_grammar")
        and GRAMMAR_INDEXES <= indexes(connection, "occurrence_grammar")
    )


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        if not (
            LEGACY_OCCURRENCE_COLUMNS <= columns(connection, "occurrence")
            and LEGACY_OCCURRENCE_COLUMNS
                <= columns(connection, "occurrence_revision")
        ):
            raise RuntimeError("La base no coincide con el esquema posterior a 006")
        if migration_is_complete(connection):
            return False
    finally:
        connection.close()

    if backup_path is not None:
        if backup_path.exists():
            raise SystemExit(
                f"El backup ya existe y no se sobrescribira: {backup_path}"
            )
        shutil.copy2(database_path, backup_path)

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS occurrence_grammar (
                occurrence_grammar_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurrence_id INTEGER NOT NULL,
                gender TEXT,
                plural TEXT,
                agentive TEXT,
                conjugated_form TEXT,
                negation TEXT,
                grammar_note TEXT,
                is_current INTEGER NOT NULL DEFAULT 1
                    CHECK (is_current IN (0, 1)),
                supersedes_occurrence_grammar_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                change_note TEXT,
                FOREIGN KEY (occurrence_id)
                    REFERENCES occurrence(occurrence_id),
                FOREIGN KEY (supersedes_occurrence_grammar_id)
                    REFERENCES occurrence_grammar(occurrence_grammar_id)
            )
        """)
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                one_current_grammar_per_occurrence
            ON occurrence_grammar(occurrence_id) WHERE is_current = 1
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_occurrence_grammar_occurrence
            ON occurrence_grammar(occurrence_id)
        """)
        assert migration_is_complete(connection)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    changed = migrate()
    if changed:
        print("Migracion 007 completada. Backup conservado en:", BACKUP_PATH)
    else:
        print("Migracion 007 ya estaba aplicada; no se realizaron cambios.")
