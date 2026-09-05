"""Add non-destructive source retirement and enforce active evidence sources."""
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_retirement_schema import TRIGGERS, install

DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_019.db"


def columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def migration_is_complete(db):
    triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    return all("retired_at" in columns(db, t) for t in ("source", "source_revision")) and set(TRIGGERS) <= triggers


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    if not database_path.is_file():
        raise RuntimeError("La base no existe.")
    db = sqlite3.connect(database_path)
    try:
        if migration_is_complete(db):
            return False
        if not all("analyst_protected" in columns(db, t) for t in ("source", "source_revision")):
            raise RuntimeError("La base debe tener migration 018.")
    finally:
        db.close()
    if backup_path is not None:
        with database_path.open("rb") as src, Path(backup_path).open("xb") as dst:
            shutil.copyfileobj(src, dst)
    db = sqlite3.connect(database_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        for table in ("source", "source_revision"):
            if "retired_at" not in columns(db, table):
                db.execute(f"ALTER TABLE {table} ADD COLUMN retired_at TEXT")
        install(db)
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
    run_migration_cli(migrate, "019")
