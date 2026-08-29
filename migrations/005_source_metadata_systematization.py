import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_005.db"


SOURCE_METADATA_COLUMNS = (
    ("legacy_source_code", "TEXT"),
    (
        "source_scope",
        "TEXT CHECK (source_scope IN ('INSTITUTIONAL', 'PERSONAL'))",
    ),
    ("format_original", "TEXT"),
    ("format_detail", "TEXT"),
    ("region_description", "TEXT"),
    ("characterization", "TEXT"),
    ("reported_entry_count", "INTEGER CHECK (reported_entry_count >= 0)"),
)

SYSTEMATIZATION_COLUMNS = {
    "source_systematization_id", "source_id", "status", "reviewed_at",
    "coverage_note", "created_at", "created_by",
}


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def add_columns(connection, table, definitions):
    existing = columns(connection, table)
    for name, definition in definitions:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


def migration_is_complete(connection):
    metadata_names = {name for name, _ in SOURCE_METADATA_COLUMNS}
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    return (
        metadata_names <= columns(connection, "source")
        and metadata_names <= columns(connection, "source_revision")
        and SYSTEMATIZATION_COLUMNS
            <= columns(connection, "source_systematization")
        and "idx_source_systematization_source_reviewed" in indexes
    )


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        required = ("source", "source_revision")
        if not all(columns(connection, table) for table in required):
            raise RuntimeError("La base no coincide con el esquema posterior a 004")
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
        add_columns(connection, "source", SOURCE_METADATA_COLUMNS)
        add_columns(connection, "source_revision", SOURCE_METADATA_COLUMNS)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS source_systematization (
                source_systematization_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'NOT_STARTED', 'PARTIAL', 'COMPLETE', 'UNKNOWN'
                    )),
                reviewed_at TEXT NOT NULL,
                coverage_note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                FOREIGN KEY (source_id) REFERENCES source(source_id)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS
                idx_source_systematization_source_reviewed
            ON source_systematization(
                source_id, reviewed_at, source_systematization_id
            )
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
        print("Migracion 005 completada. Backup conservado en:", BACKUP_PATH)
    else:
        print("Migracion 005 ya estaba aplicada; no se realizaron cambios.")
