import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_006.db"


LEGACY_PROVENANCE_COLUMNS = (
    ("legacy_occurrence_id", "TEXT"),
    ("legacy_source_detail_1", "TEXT"),
    ("legacy_source_detail_2", "TEXT"),
)


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def add_columns(connection, table, definitions):
    existing = columns(connection, table)
    for name, definition in definitions:
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migration_is_complete(connection):
    expected = {name for name, _ in LEGACY_PROVENANCE_COLUMNS}
    return (
        expected <= columns(connection, "occurrence")
        and expected <= columns(connection, "occurrence_revision")
    )


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        required = ("source_systematization", "occurrence", "occurrence_revision")
        if not all(columns(connection, table) for table in required):
            raise RuntimeError("La base no coincide con el esquema posterior a 005")
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
        add_columns(connection, "occurrence", LEGACY_PROVENANCE_COLUMNS)
        add_columns(connection, "occurrence_revision", LEGACY_PROVENANCE_COLUMNS)
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
        print("Migracion 006 completada. Backup conservado en:", BACKUP_PATH)
    else:
        print("Migracion 006 ya estaba aplicada; no se realizaron cambios.")
