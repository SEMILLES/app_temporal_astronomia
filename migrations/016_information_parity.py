import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_016.db"

OCCURRENCE_COLUMNS = (
    ("source_detail_1", "TEXT"),
    ("source_detail_2", "TEXT"),
    ("usage_examples_present", "INTEGER NOT NULL DEFAULT 0 CHECK (usage_examples_present IN (0,1))"),
    ("grammatical_info_present", "INTEGER NOT NULL DEFAULT 0 CHECK (grammatical_info_present IN (0,1))"),
    ("grammatical_note", "TEXT"),
)
CONCEPT_COLUMNS = (
    ("semantic_field_1", "TEXT"), ("semantic_field_2", "TEXT"),
    ("knowledge_area_1", "TEXT"), ("knowledge_area_2", "TEXT"),
)

def columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

def migration_is_complete(db):
    occurrence = {name for name, _ in OCCURRENCE_COLUMNS}
    concept = {name for name, _ in CONCEPT_COLUMNS}
    return (occurrence <= columns(db, "occurrence") and
            occurrence <= columns(db, "occurrence_revision") and
            occurrence <= columns(db, "occurrence_draft") and
            concept <= columns(db, "concept"))

def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")
    inspection = sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection): return False
        for table in ("occurrence", "occurrence_revision", "occurrence_draft", "concept"):
            if not columns(inspection, table): raise RuntimeError(f"Falta la tabla {table}")
    finally: inspection.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path, backup_path)
    db = sqlite3.connect(database_path); db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute("BEGIN")
        for table in ("occurrence", "occurrence_revision", "occurrence_draft"):
            existing = columns(db, table)
            for name, definition in OCCURRENCE_COLUMNS:
                if name not in existing: db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        existing = columns(db, "concept")
        for name, definition in CONCEPT_COLUMNS:
            if name not in existing: db.execute(f"ALTER TABLE concept ADD COLUMN {name} {definition}")
        if not migration_is_complete(db): raise RuntimeError("Migration 016 incompleta")
        if db.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()
    return True

if __name__ == "__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, "016")
