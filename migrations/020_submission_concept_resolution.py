"""Add local conceptual resolution history, without inferring historical decisions."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission_concept_resolution import install


def migration_is_complete(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    return {'submission_concept_resolution', 'one_current_submission_concept_resolution',
            'idx_submission_concept_resolution_submission'} <= names


def migrate(database_path, backup_path=None):
    path = Path(database_path).resolve()
    if path == (ROOT / 'import_inputs/astronomia/lesico_astronomia_working.db').resolve():
        raise RuntimeError('Esta migración no puede ejecutarse sobre la base real de trabajo en esta fase.')
    if not path.is_file():
        raise RuntimeError('La base no existe.')
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        if migration_is_complete(db):
            return False
        if backup_path is not None:
            with Path(backup_path).open('xb'):
                pass
            backup = sqlite3.connect(backup_path)
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute('BEGIN IMMEDIATE')
        install(db)
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('La base contiene referencias inválidas.')
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, '020')
