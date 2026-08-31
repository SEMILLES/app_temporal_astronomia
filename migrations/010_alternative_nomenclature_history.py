import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_010.db"


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migration_is_complete(connection):
    return columns(connection, "renumber_event") == {
        "renumber_event_id", "concept_id", "origin", "reason", "created_at",
        "created_from_submission_id", "created_by",
    } and columns(connection, "renumber_change") == {
        "renumber_change_id", "renumber_event_id", "alternative_id",
        "old_working_label", "new_working_label",
    }


SCHEMA = """
CREATE TABLE renumber_event (
    renumber_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('automatic_assisted','manual')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_from_submission_id INTEGER,
    created_by TEXT,
    FOREIGN KEY(concept_id) REFERENCES concept(concept_id),
    FOREIGN KEY(created_from_submission_id) REFERENCES submission(submission_id),
    CHECK(origin != 'manual' OR (reason IS NOT NULL AND length(trim(reason)) > 0))
);
CREATE INDEX idx_renumber_event_concept ON renumber_event(concept_id);
CREATE TABLE renumber_change (
    renumber_change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    renumber_event_id INTEGER NOT NULL,
    alternative_id INTEGER NOT NULL,
    old_working_label TEXT,
    new_working_label TEXT NOT NULL CHECK(length(trim(new_working_label)) > 0),
    FOREIGN KEY(renumber_event_id) REFERENCES renumber_event(renumber_event_id),
    FOREIGN KEY(alternative_id) REFERENCES alternative(alternative_id),
    UNIQUE(renumber_event_id, alternative_id)
);
CREATE INDEX idx_renumber_change_alternative ON renumber_change(alternative_id);
"""


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")
    inspection = sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection):
            return False
        if "created_from_submission_id" not in columns(inspection, "assignment"):
            raise RuntimeError("La base no tiene el esquema post-009")
        if columns(inspection, "renumber_event") or columns(inspection, "renumber_change"):
            raise RuntimeError("Existe una instalación parcial de migration 010")
    finally:
        inspection.close()
    if backup_path is not None:
        if backup_path.exists():
            raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path, backup_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        labels = connection.execute("SELECT alternative_id, working_label FROM alternative ORDER BY alternative_id").fetchall()
        connection.executescript("BEGIN;\n" + SCHEMA)
        if connection.execute("SELECT count(*) FROM renumber_event").fetchone()[0] != 0:
            raise RuntimeError("Migration 010 inventó eventos baseline")
        if labels != connection.execute("SELECT alternative_id, working_label FROM alternative ORDER BY alternative_id").fetchall():
            raise RuntimeError("Migration 010 alteró working_label")
        if not migration_is_complete(connection):
            raise RuntimeError("Migration 010 incompleta")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign keys inválidas: {violations!r}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return True


if __name__ == "__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, "010")
