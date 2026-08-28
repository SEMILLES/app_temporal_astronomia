import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_003.db"

sys.path.insert(0, str(ROOT))

from database import crear_esquema


def has_column(conexion, table, column):
    return column in {
        row[1] for row in conexion.execute(f"PRAGMA table_info({table})")
    }


def migrate():
    if not DATABASE_PATH.exists():
        raise SystemExit(f"No existe la base: {DATABASE_PATH}")
    if BACKUP_PATH.exists():
        raise SystemExit(f"El backup ya existe y no se sobrescribira: {BACKUP_PATH}")

    shutil.copy2(DATABASE_PATH, BACKUP_PATH)
    conexion = sqlite3.connect(DATABASE_PATH)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    try:
        if not has_column(conexion, "submission", "submission_id"):
            raise RuntimeError("La base no corresponde al esquema posterior a 002")
        conexion.execute("BEGIN")
        for column in ("proposed_concept_label", "proposed_concept_note"):
            if not has_column(conexion, "submission", column):
                conexion.execute(f"ALTER TABLE submission ADD COLUMN {column} TEXT")
        crear_esquema(conexion)
        assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conexion.execute("SELECT COUNT(*) FROM submission").fetchone()[0] >= 0
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


if __name__ == "__main__":
    migrate()
    print("Migracion 003 completada. Backup conservado en:", BACKUP_PATH)