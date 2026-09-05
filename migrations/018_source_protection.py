"""Protect existing sources; retain protection in subsequent history snapshots."""
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_018.db"


def columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def migration_is_complete(db):
    return all("analyst_protected" in columns(db, table)
               for table in ("source", "source_revision"))


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    db = sqlite3.connect(database_path)
    try:
        if migration_is_complete(db):
            return False
        if not all(columns(db, table) for table in ("source", "source_revision", "application_setting")):
            raise RuntimeError("La base no tiene el esquema post-017")
    finally:
        db.close()
    if backup_path is not None:
        backup_path = Path(backup_path)
        if backup_path.exists():
            raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path, backup_path)
    db = sqlite3.connect(database_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        for table in ("source", "source_revision"):
            if "analyst_protected" not in columns(db, table):
                db.execute(f"ALTER TABLE {table} ADD COLUMN analyst_protected INTEGER NOT NULL DEFAULT 1 CHECK (analyst_protected IN (0, 1))")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Foreign keys inválidas")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return True


if __name__ == "__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, "018")
