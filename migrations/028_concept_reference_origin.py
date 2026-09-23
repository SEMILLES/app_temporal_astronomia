"""Record explicit proposal provenance; historical references remain unknown."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'migration/usage_profile_2026-09-18'))
from safe_database import cli, run, check_integrity


def migration(db):
    if any(k.startswith('RAILWAY_') for k in os.environ) or os.environ.get('LESICO_ENV', '').strip().lower() == 'production':
        raise ValueError('Migración exclusivamente local.')
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
