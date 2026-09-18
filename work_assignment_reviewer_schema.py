"""Schema 026. Keep work_assignment_schema (025) frozen for reproducible migrations."""
import sqlite3

from work_assignment_schema import STATEMENTS as LEGACY_STATEMENTS

STATEMENTS = (
    LEGACY_STATEMENTS[0]
    .replace("created_access_role='master'", "created_access_role IN ('reviewer','master')")
    .replace("removed_access_role='master'", "removed_access_role IN ('reviewer','master')")
    .replace('active=0 AND removed_at IS NOT NULL',
             'active=0 AND removed_at IS NOT NULL AND removed_access_role IS NOT NULL'),
    *LEGACY_STATEMENTS[1:],
)


def install(db):
    for statement in STATEMENTS:
        db.execute(statement)


def validate_schema(db):
    reference = sqlite3.connect(':memory:')
    try:
        install(reference)
        for name, sql in reference.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name='concept_work_assignment'"):
            actual = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
            # SQLite quotes the table name when ALTER TABLE renames the replacement.
            normalize = lambda s: ''.join(s.lower().replace('if not exists', '').replace('"', '').split())
            if not actual or normalize(actual[0]) != normalize(sql):
                raise ValueError('Esquema de asignación incompatible; aplique la migración 026: ' + name)
    finally:
        reference.close()
