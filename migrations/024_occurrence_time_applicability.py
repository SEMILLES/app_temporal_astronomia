"""Schema-only migration 024; explicit database, dry-run by default."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run

COLUMN = "source_detail_2_applicability_override"
TABLES = ("occurrence", "occurrence_revision", "occurrence_draft")


def migration(db):
    changes = 0
    for table in TABLES:
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if "source_detail_2_status" not in columns:
            raise ValueError(f"Esquema incompatible: {table}")
        if COLUMN not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {COLUMN} INTEGER "
                       f"CHECK({COLUMN} IS NULL OR {COLUMN} IN (0,1))")
            changes += 1
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Foreign keys inválidas")
    return {"migration": "024", "changes": changes}


def migrate(database_path, backup_path=None, *, apply=False):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == "__main__":
    raise SystemExit(cli(migration, "Agregar la aplicabilidad excepcional del tiempo"))
