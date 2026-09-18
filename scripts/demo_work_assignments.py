"""Create a NEW local synthetic demo. Never open an existing database."""
from pathlib import Path
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if any(key.startswith('RAILWAY_') for key in os.environ) or os.environ.get('LESICO_ENV', '').lower() == 'production':
        raise SystemExit('La demo solo se permite en desarrollo local.')
    path = ROOT / 'work_assignment_demo.db'
    with path.open('xb'):
        pass
    from database import crear_esquema
    from work_assignments import assign
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        crear_esquema(db)
        db.executemany('INSERT INTO concept(preferred_label) VALUES(?)',
                       [('Concepto A',), ('Concepto B',), ('Concepto C',)])
        db.executemany('INSERT INTO collaborator(display_name) VALUES(?)', [('Ana',), ('Carlos',)])
        db.commit()
        assign(db, [1], [1, 2], actor_id=2, access_role='master')
        assign(db, [2], [2], actor_id=2, access_role='master')
    finally:
        db.close()
    print(path)


if __name__ == '__main__':
    main()
