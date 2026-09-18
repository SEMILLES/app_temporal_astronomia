"""Schema-only migration 023. Dry-run by default; requires an explicit DB path."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run
from usage_profile import install, validate_schema


def migration(db):
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name='alternative_usage_profile'").fetchone()
    if not present:
        required = {'alternative_id', 'concept_id', 'working_label', 'retired_at'}
        if not required <= {r[1] for r in db.execute('PRAGMA table_info(alternative)')}:
            raise ValueError('Esquema Alternative incompatible')
        install(db)
    validate_schema(db)
    return {'migration': '023', 'changes': int(not present)}


def migrate(database_path, *, apply=False, backup_path=None):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == '__main__':
    raise SystemExit(cli(migration, 'Crear el perfil opcional de uso y contexto'))
