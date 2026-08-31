import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_012.db"

SCHEMA = """
CREATE TABLE collaborator (
 collaborator_id INTEGER PRIMARY KEY AUTOINCREMENT,
 display_name TEXT NOT NULL CHECK(length(trim(display_name)) > 0),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE activity_event (
 activity_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_type TEXT NOT NULL CHECK(length(trim(event_type)) > 0),
 entity_type TEXT,
 entity_id INTEGER,
 collaborator_id INTEGER,
 collaborator_name_snapshot TEXT,
 access_role TEXT NOT NULL CHECK(access_role IN ('analyst','reviewer','master')),
 occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 comment TEXT,
 FOREIGN KEY(collaborator_id) REFERENCES collaborator(collaborator_id)
);
CREATE INDEX idx_activity_event_collaborator ON activity_event(collaborator_id,occurred_at);
CREATE INDEX idx_activity_event_entity ON activity_event(entity_type,entity_id,occurred_at);
"""

EXPECTED = {
 "collaborator": {"collaborator_id","display_name","active","created_at"},
 "activity_event": {"activity_event_id","event_type","entity_type","entity_id","collaborator_id","collaborator_name_snapshot","access_role","occurred_at","comment"},
}

def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

def migration_is_complete(connection):
    indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    return (
        all(columns(connection, table) == expected for table, expected in EXPECTED.items())
        and {"idx_activity_event_collaborator", "idx_activity_event_entity"} <= indexes
    )

def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    inspection = sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection): return False
        if not columns(inspection, "alternative_morphology"):
            raise RuntimeError("La base no tiene el esquema post-011")
        if columns(inspection, "collaborator") or columns(inspection, "activity_event"):
            raise RuntimeError("Existe una instalación parcial de migration 012")
    finally: inspection.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path, backup_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript("BEGIN;\n" + SCHEMA)
        if any(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in EXPECTED):
            raise RuntimeError("Migration 012 inventó actividad histórica")
        if not migration_is_complete(connection): raise RuntimeError("Migration 012 incompleta")
        if connection.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally: connection.close()
    return True

if __name__ == "__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, "012")
