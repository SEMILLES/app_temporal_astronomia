"""Record explicit proposal provenance; historical references remain unknown."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run, check_integrity


def authorize_environment():
    if any(k.startswith('RAILWAY_') for k in os.environ):
        # Require deliberate authorization AND the identity already allowlisted
        # by safe_database.run. Missing Railway identity is not evidence of tests.
        if not (
            os.environ.get('LESICO_MIGRATION_028_ALLOW_PRUEBAS') == '1'
            and os.environ.get('RAILWAY_ENVIRONMENT_NAME') == 'pruebas'
            and os.environ.get('RAILWAY_ENVIRONMENT_ID') == 'dc6a07af-8842-44c8-a072-a0d3e10ae203'
        ):
            raise ValueError(
                'Railway requiere LESICO_MIGRATION_028_ALLOW_PRUEBAS=1, '
                'y nombre/ID verificados del entorno PRUEBAS.')
        return
    # Outside Railway only, retain the local production-profile safeguard.
    # In Railway LESICO_ENV is an application profile, not environment identity.
    environment = os.environ.get('LESICO_ENV', '').strip().lower()
    if environment in ('production', 'produccion', 'producción', 'prod'):
        raise ValueError('La migración 028 local no está autorizada con perfil de producción.')


def migration(db):
    authorize_environment()
    if not db.in_transaction or db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Se requiere transacción y claves foráneas activas.')
    columns = {r[1] for r in db.execute('PRAGMA table_info(occurrence_concept_reference)')}
    if not {'occurrence_id', 'concept_id', 'concept_proposal_id', 'is_current'} <= columns:
        raise ValueError('Esquema de referencia conceptual no reconocido.')
    changed = 'proposal_origin' not in columns
    if changed:
        db.execute("""ALTER TABLE occurrence_concept_reference ADD COLUMN
            proposal_origin TEXT CHECK (proposal_origin IS NULL OR
                (concept_proposal_id IS NOT NULL AND
                 proposal_origin IN ('NEW_PROPOSAL', 'SELECTED_PENDING')))""")
    check_integrity(db)
    return {'migration': '028', 'changes': int(changed)}


def migrate(database_path, *, apply=False, backup_path=None):
    return run(database_path, migration, apply=apply, backup=backup_path)


if __name__ == '__main__':
    raise SystemExit(cli(migration, 'Proveniencia de la elección de propuesta conceptual'))
