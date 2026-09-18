"""Transactional SQLite rebuild of 025, preserving administrative rows and IDs."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run
from work_assignment_schema import validate_schema as validate_025
from work_assignment_reviewer_schema import STATEMENTS, validate_schema

TABLE = 'concept_work_assignment'
REPLACEMENT = 'concept_work_assignment_026'
COLUMNS = '''work_assignment_id concept_id analyst_id analyst_name_snapshot created_at
    created_by_collaborator_id created_by_name_snapshot created_access_role active
    removed_at removed_by_collaborator_id removed_by_name_snapshot removed_access_role'''.split()


def migration(db):
    if any(key.startswith('RAILWAY_') for key in os.environ) or os.environ.get('LESICO_ENV', '').strip().lower() == 'production':
        raise ValueError('Esta migración solo se permite localmente, fuera de producción.')
    if not db.in_transaction or db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Se requiere una transacción con claves foráneas activas.')
    try:
        validate_schema(db)
    except ValueError:
        validate_025(db)
    else:
        return {'migration': '026', 'changes': 0}

    # Refuse unexpected extensions rather than dropping their indexes/triggers or
    # cascading changes into referencing tables while foreign keys remain enabled.
    expected = {TABLE, 'one_active_concept_work_assignment', 'idx_work_assignment_analyst'}
    objects = {r[0] for r in db.execute('SELECT name FROM sqlite_master WHERE tbl_name=?', (TABLE,))}
    if objects != expected:
        raise ValueError('La tabla tiene índices o disparadores no previstos; no se modifica.')
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        quoted = '"' + table.replace('"', '""') + '"'
        if any(row[2].lower() == TABLE for row in db.execute(f'PRAGMA foreign_key_list({quoted})')):
            raise ValueError('Hay tablas que referencian las asignaciones; se requiere revisión.')
    if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (REPLACEMENT,)).fetchone():
        raise ValueError('Ya existe una tabla de reemplazo; no se modifica.')
    sequence = db.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (TABLE,)).fetchone()
    columns = ','.join(COLUMNS)
    db.execute(STATEMENTS[0].replace('IF NOT EXISTS ', '').replace(TABLE, REPLACEMENT))
    db.execute(f'INSERT INTO {REPLACEMENT} ({columns}) SELECT {columns} FROM {TABLE}')
    for left, right in ((TABLE, REPLACEMENT), (REPLACEMENT, TABLE)):
        if db.execute(f'SELECT {columns} FROM {left} EXCEPT SELECT {columns} FROM {right}').fetchone():
            raise ValueError('La copia de asignaciones no coincide; se cancela la migración.')
    db.execute(f'DROP TABLE {TABLE}')
    db.execute(f'ALTER TABLE {REPLACEMENT} RENAME TO {TABLE}')
    for statement in STATEMENTS[1:]:
        db.execute(statement)
    if sequence is not None:
        db.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?', (sequence[0], TABLE))
    validate_schema(db)
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Claves foráneas inválidas; se cancela la migración.')
    return {'migration': '026', 'changes': 1}


def migrate(database_path, *, apply=False, backup_path=None):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == '__main__':
    raise SystemExit(cli(migration, 'Permitir asignaciones de trabajo por Revisor y Administrador'))
