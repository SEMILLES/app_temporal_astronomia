"""Schema-only administrative assignment migration; explicit path, dry-run default."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run
from work_assignment_schema import install, validate_schema


def migration(db):
    if any(key.startswith('RAILWAY_') for key in os.environ) or os.environ.get('LESICO_ENV', '').lower() == 'production':
        raise ValueError('Esta migración solo se permite localmente, fuera de producción.')
    for table, required in (('concept', {'concept_id', 'preferred_label'}),
                            ('collaborator', {'collaborator_id', 'display_name', 'active'})):
        if not required <= {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
            raise ValueError('Esquema incompatible: ' + table)
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name='concept_work_assignment'").fetchone()
    if not present:
        install(db)
    validate_schema(db)
    return {'migration': '025', 'changes': int(not present)}


def migrate(database_path, *, apply=False, backup_path=None):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == '__main__':
    raise SystemExit(cli(migration, 'Crear las asignaciones administrativas de conceptos'))
