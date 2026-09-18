"""Administrative assignments only; no linguistic workflow dependencies."""
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS concept_work_assignment (
        work_assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
        analyst_id INTEGER NOT NULL REFERENCES collaborator(collaborator_id),
        analyst_name_snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by_collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
        created_by_name_snapshot TEXT,
        created_access_role TEXT NOT NULL CHECK(created_access_role='master'),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        removed_at TEXT,
        removed_by_collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
        removed_by_name_snapshot TEXT,
        removed_access_role TEXT CHECK(removed_access_role='master'),
        CHECK((active=1 AND removed_at IS NULL AND removed_access_role IS NULL)
           OR (active=0 AND removed_at IS NOT NULL AND removed_access_role='master'))
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_active_concept_work_assignment
       ON concept_work_assignment(concept_id,analyst_id) WHERE active=1""",
    """CREATE INDEX IF NOT EXISTS idx_work_assignment_analyst
       ON concept_work_assignment(analyst_id,concept_id) WHERE active=1""",
)


def install(db):
    for statement in STATEMENTS:
        db.execute(statement)


def validate_schema(db):
    import sqlite3
    reference = sqlite3.connect(':memory:')
    try:
        install(reference)
        for name, sql in reference.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"):
            actual = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
            normalize = lambda s: ''.join(s.lower().replace('if not exists', '').split())
            if not actual or normalize(actual[0]) != normalize(sql):
                raise ValueError('Esquema de asignación incompatible: ' + name)
    finally:
        reference.close()
