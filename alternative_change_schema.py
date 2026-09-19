"""Additive subtype for submissions about an existing alternative (027)."""

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS alternative_change_submission (
        submission_id INTEGER PRIMARY KEY REFERENCES submission(submission_id),
        concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
        concept_label_snapshot TEXT NOT NULL,
        alternative_label_snapshot TEXT,
        change_kind TEXT NOT NULL CHECK(change_kind IN ('MORPHOLOGY','RELATION')),
        payload TEXT NOT NULL CHECK(json_valid(payload)),
        baseline TEXT NOT NULL,
        submitted_by_collaborator_id INTEGER NOT NULL REFERENCES collaborator(collaborator_id),
        submitted_access_role TEXT NOT NULL CHECK(submitted_access_role IN ('analyst','reviewer','master')),
        reviewed_by_collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
        reviewed_access_role TEXT CHECK(reviewed_access_role IN ('reviewer','master')),
        result_id INTEGER
    )""",
    """CREATE TRIGGER IF NOT EXISTS alternative_change_parent BEFORE INSERT ON alternative_change_submission
    WHEN NOT EXISTS(SELECT 1 FROM submission s JOIN alternative a ON a.alternative_id=s.alternative_id
        WHERE s.submission_id=NEW.submission_id AND s.submission_type='ALTERNATIVE_CHANGE'
        AND s.status='pending' AND a.concept_id=NEW.concept_id AND a.retired_at IS NULL)
    BEGIN SELECT RAISE(ABORT,'invalid alternative submission target'); END""",
    """CREATE TRIGGER IF NOT EXISTS alternative_change_immutable BEFORE UPDATE ON alternative_change_submission
    WHEN NEW.submission_id!=OLD.submission_id OR NEW.concept_id!=OLD.concept_id
      OR NEW.concept_label_snapshot IS NOT OLD.concept_label_snapshot
      OR NEW.alternative_label_snapshot IS NOT OLD.alternative_label_snapshot
      OR NEW.change_kind!=OLD.change_kind OR NEW.payload!=OLD.payload OR NEW.baseline!=OLD.baseline
      OR NEW.submitted_by_collaborator_id!=OLD.submitted_by_collaborator_id
      OR NEW.submitted_access_role!=OLD.submitted_access_role
      OR (SELECT status FROM submission WHERE submission_id=OLD.submission_id)!='pending'
    BEGIN SELECT RAISE(ABORT,'immutable alternative proposal'); END""",
    """CREATE TRIGGER IF NOT EXISTS alternative_change_no_delete BEFORE DELETE ON alternative_change_submission
    BEGIN SELECT RAISE(ABORT,'alternative proposal history is immutable'); END""",
)


def install(db):
    for sql in STATEMENTS:
        db.execute(sql)


def validate_schema(db):
    columns = {row[1]: row for row in db.execute('PRAGMA table_info(submission)')}
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='submission'").fetchone()
    if 'alternative_id' not in columns or columns['occurrence_id'][3] or not sql or 'ALTERNATIVE_CHANGE' not in sql[0]:
        raise ValueError('Se requiere ejecutar explícitamente la migración 027 de Aportes de alternativa.')
    for name in ('alternative_change_submission', 'alternative_change_parent', 'alternative_change_immutable', 'alternative_change_no_delete'):
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone():
            raise ValueError('Esquema 027 incompleto: ' + name)

SUBMISSION_SQL = "CREATE TABLE IF NOT EXISTS submission (\n            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,\n            occurrence_id INTEGER,\n            alternative_id INTEGER REFERENCES alternative(alternative_id),\n            submission_type TEXT NOT NULL\n                CHECK (submission_type IN ('GRAMMAR', 'ALTERNATIVE', 'ALTERNATIVE_CHANGE')),\n            status TEXT NOT NULL\n                CHECK (status IN ('pending', 'resolved')),\n            resolution TEXT CHECK (resolution IN ('accepted', 'rejected')),\n            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n            resolved_at TEXT,\n            submitted_by TEXT,\n            reviewed_by TEXT,\n            review_note TEXT,\n            legacy_reviewed_at TEXT,\n            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),\n            CHECK ((submission_type='ALTERNATIVE_CHANGE' AND occurrence_id IS NULL AND alternative_id IS NOT NULL)\n                OR (submission_type IN ('GRAMMAR','ALTERNATIVE') AND occurrence_id IS NOT NULL AND alternative_id IS NULL)),\n            CHECK (\n                (status = 'pending' AND resolution IS NULL)\n                OR (status = 'resolved'\n                    AND resolution IS NOT NULL\n                    AND resolution IN ('accepted', 'rejected'))\n            )\n        );"
