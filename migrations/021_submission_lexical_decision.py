"""Install final lexical decisions without inferring historical decisions."""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission_lexical_decision import SCHEMA, install


def migration_is_complete(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='submission_lexical_decision'").fetchone()
    if row is None:
        return False
    def normalized(sql):
        return re.sub(r'\s+', '', sql.replace('IF NOT EXISTS', '')).rstrip(';').lower()
    if normalized(row[0] or '') != normalized(SCHEMA):
        raise RuntimeError('La tabla submission_lexical_decision es incompatible.')
    return True


def migrate(database_path, backup_path=None):
    path = Path(database_path).resolve()
    if path == (ROOT / 'import_inputs/astronomia/lesico_astronomia_working.db').resolve():
        raise RuntimeError('Esta migración no puede ejecutarse sobre la base real de trabajo en esta fase.')
    if not path.is_file():
        raise RuntimeError('La base no existe.')
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        complete = migration_is_complete(db)
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('La base contiene referencias inválidas.')
        if complete:
            return False
        backup_path = Path(backup_path) if backup_path is not None else path.with_name(
            f'{path.stem}.pre_migration_021{path.suffix}')
        with backup_path.open('xb'):
            pass
        backup = sqlite3.connect(backup_path)
        try:
            db.backup(backup)
        finally:
            backup.close()
        db.execute('BEGIN IMMEDIATE')
        migration_is_complete(db)
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
    run_migration_cli(migrate, '021')
