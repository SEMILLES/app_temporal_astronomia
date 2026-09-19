"""Local, backed-up, transactional rebuild; historical submissions keep their IDs."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run, check_integrity
from alternative_change_schema import SUBMISSION_SQL, install, validate_schema


def migration(db):
    if any(k.startswith('RAILWAY_') for k in os.environ) or os.environ.get('LESICO_ENV', '').lower() == 'production':
        raise ValueError('Migración exclusivamente local.')
    if not db.in_transaction or db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Se requiere transacción y claves foráneas activas.')
    columns = [r[1] for r in db.execute('PRAGMA table_info(submission)')]
    if 'alternative_id' in columns:
        validate_schema(db)
        check_integrity(db)
        return {'migration': '027', 'changes': 0}
    expected = 'submission_id occurrence_id submission_type status resolution submitted_at resolved_at submitted_by reviewed_by review_note legacy_reviewed_at'.split()
    if columns != expected:
        raise ValueError('Esquema de submission no reconocido.')
    # DROP must never cascade into dependent historical rows.
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        quoted = '"' + table.replace('"', '""') + '"'
        for fk in db.execute('PRAGMA foreign_key_list(' + quoted + ')'):
            if fk[2] == 'submission' and fk[6] != 'NO ACTION':
                raise ValueError('Dependencia con borrado especial; requiere revisión.')
    objects = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='submission' AND type IN ('index','trigger') AND sql IS NOT NULL")]
    seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='submission'").fetchone()
    names = ','.join(columns)
    old_defer = db.execute('PRAGMA defer_foreign_keys').fetchone()[0]
    old_legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
    try:
        db.execute('PRAGMA defer_foreign_keys=ON')
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute(SUBMISSION_SQL.replace('IF NOT EXISTS submission', 'submission_027'))
        db.execute(f'INSERT INTO submission_027 ({names}) SELECT {names} FROM submission')
        for left, right in (('submission', 'submission_027'), ('submission_027', 'submission')):
            if db.execute(f'SELECT {names} FROM {left} EXCEPT SELECT {names} FROM {right}').fetchone():
                raise ValueError('La copia de submissions no coincide.')
        db.execute('DROP TABLE submission')
        db.execute('ALTER TABLE submission_027 RENAME TO submission')
        for sql in objects:
            db.execute(sql)
        if seq:
            db.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='submission'", (seq[0],))
        install(db)
        validate_schema(db)
        check_integrity(db)
        return {'migration': '027', 'changes': 1}
    finally:
        db.execute('PRAGMA legacy_alter_table=' + str(old_legacy))
        # Final FK check above validates the rebuilt parent before clearing the
        # deferred DROP bookkeeping. The safe runner rolls back any exception.
        db.execute('PRAGMA defer_foreign_keys=' + str(old_defer))


def migrate(database_path, *, apply=False, backup_path=None):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == '__main__':
    raise SystemExit(cli(migration, 'Aportes sobre alternativas existentes'))
