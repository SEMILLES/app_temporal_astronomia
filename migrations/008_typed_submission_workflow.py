import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_008.db"


REQUIRED_TABLES = {
    "concept_proposal",
    "occurrence_draft",
    "occurrence_concept_reference",
    "submission",
    "alternative_submission",
    "alternative_submission_relation",
    "grammar_submission",
}

SUBMISSION_COLUMNS = {
    "submission_id", "occurrence_id", "submission_type", "status",
    "resolution", "submitted_at", "resolved_at", "submitted_by",
    "reviewed_by", "review_note", "legacy_reviewed_at",
}


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def indexes(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}


def migration_is_complete(connection):
    return (
        REQUIRED_TABLES <= tables(connection)
        and SUBMISSION_COLUMNS == columns(connection, "submission")
        and "one_pending_submission_per_occurrence_type"
            in indexes(connection, "submission")
        and "one_current_occurrence_concept_reference"
            in indexes(connection, "occurrence_concept_reference")
        and "one_alternative_relation_target_per_parameter"
            in indexes(connection, "alternative_submission_relation")
        and "one_submission_relation_target_per_parameter"
            in indexes(connection, "alternative_submission_relation")
    )


def _create_schema(connection):
    schema = """
        CREATE TABLE concept_proposal (
            concept_proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposed_label TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'resolved', 'rejected')),
            resolved_concept_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            resolution_note TEXT,
            FOREIGN KEY (resolved_concept_id) REFERENCES concept(concept_id),
            CHECK (
                (status = 'pending' AND resolved_concept_id IS NULL)
                OR (status = 'resolved' AND resolved_concept_id IS NOT NULL)
                OR (status = 'rejected' AND resolved_concept_id IS NULL)
            )
        );

        CREATE TABLE occurrence_draft (
            draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            original_gloss TEXT,
            occurrence_year INTEGER,
            source_locator TEXT,
            provenance_note TEXT,
            reference_concept_id INTEGER,
            reference_concept_proposal_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES source(source_id),
            FOREIGN KEY (reference_concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (reference_concept_proposal_id)
                REFERENCES concept_proposal(concept_proposal_id),
            CHECK (NOT (
                reference_concept_id IS NOT NULL
                AND reference_concept_proposal_id IS NOT NULL
            ))
        );

        CREATE TABLE occurrence_concept_reference (
            occurrence_concept_reference_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            concept_id INTEGER,
            concept_proposal_id INTEGER,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_occurrence_concept_reference_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (concept_proposal_id)
                REFERENCES concept_proposal(concept_proposal_id),
            FOREIGN KEY (supersedes_occurrence_concept_reference_id)
                REFERENCES occurrence_concept_reference(
                    occurrence_concept_reference_id
                ),
            CHECK ((concept_id IS NOT NULL) != (concept_proposal_id IS NOT NULL))
        );

        CREATE UNIQUE INDEX one_current_occurrence_concept_reference
            ON occurrence_concept_reference(occurrence_id)
            WHERE is_current = 1;
        CREATE INDEX idx_occurrence_concept_reference_occurrence
            ON occurrence_concept_reference(occurrence_id);

        CREATE TABLE submission (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            submission_type TEXT NOT NULL
                CHECK (submission_type IN ('GRAMMAR', 'ALTERNATIVE')),
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'resolved')),
            resolution TEXT CHECK (resolution IN ('accepted', 'rejected')),
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            submitted_by TEXT,
            reviewed_by TEXT,
            review_note TEXT,
            legacy_reviewed_at TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            CHECK (
                (status = 'pending' AND resolution IS NULL)
                OR (status = 'resolved'
                    AND resolution IS NOT NULL
                    AND resolution IN ('accepted', 'rejected'))
            )
        );

        CREATE UNIQUE INDEX one_pending_submission_per_occurrence_type
            ON submission(occurrence_id, submission_type)
            WHERE status = 'pending';
        CREATE INDEX idx_submission_occurrence
            ON submission(occurrence_id);
        CREATE INDEX idx_submission_status
            ON submission(status);

        CREATE TABLE alternative_submission (
            submission_id INTEGER PRIMARY KEY,
            proposal_kind TEXT NOT NULL
                CHECK (proposal_kind IN ('EXISTING', 'NEW', 'UNSURE')),
            reference_concept_id INTEGER,
            reference_concept_proposal_id INTEGER,
            proposed_existing_alternative_id INTEGER,
            phonological_relation_answer TEXT
                CHECK (phonological_relation_answer IN ('YES', 'NO', 'UNSURE')),
            analysis_note TEXT,
            resolved_alternative_id INTEGER,
            is_legacy INTEGER NOT NULL DEFAULT 0
                CHECK (is_legacy IN (0, 1)),
            legacy_proposed_alternative_label TEXT,
            legacy_proposed_concept_note TEXT,
            legacy_proposed_concept_status TEXT,
            legacy_concept_uncertainty_note TEXT,
            legacy_alternative_uncertainty_note TEXT,
            legacy_proposed_relation_answer TEXT,
            legacy_related_alternative_id INTEGER,
            legacy_related_submission_id INTEGER,
            legacy_phonological_parameter TEXT,
            FOREIGN KEY (submission_id) REFERENCES submission(submission_id),
            FOREIGN KEY (reference_concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (reference_concept_proposal_id)
                REFERENCES concept_proposal(concept_proposal_id),
            FOREIGN KEY (proposed_existing_alternative_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (resolved_alternative_id)
                REFERENCES alternative(alternative_id),
            CHECK (
                (is_legacy = 1 AND NOT (
                    reference_concept_id IS NOT NULL
                    AND reference_concept_proposal_id IS NOT NULL
                ))
                OR (is_legacy = 0 AND (
                    (reference_concept_id IS NOT NULL)
                    != (reference_concept_proposal_id IS NOT NULL)
                ))
            ),
            CHECK (
                is_legacy = 1
                OR proposal_kind != 'EXISTING'
                OR proposed_existing_alternative_id IS NOT NULL
            ),
            CHECK (
                proposal_kind = 'EXISTING'
                OR proposed_existing_alternative_id IS NULL
            )
        );

        CREATE TABLE alternative_submission_relation (
            alternative_submission_relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            target_alternative_id INTEGER,
            target_submission_id INTEGER,
            phonological_parameter TEXT NOT NULL,
            uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (uncertain IN (0, 1)),
            FOREIGN KEY (submission_id)
                REFERENCES alternative_submission(submission_id),
            FOREIGN KEY (target_alternative_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (target_submission_id)
                REFERENCES submission(submission_id),
            CHECK (
                (target_alternative_id IS NOT NULL)
                != (target_submission_id IS NOT NULL)
            )
        );

        CREATE UNIQUE INDEX one_alternative_relation_target_per_parameter
            ON alternative_submission_relation(
                submission_id, target_alternative_id, phonological_parameter
            ) WHERE target_alternative_id IS NOT NULL;
        CREATE UNIQUE INDEX one_submission_relation_target_per_parameter
            ON alternative_submission_relation(
                submission_id, target_submission_id, phonological_parameter
            ) WHERE target_submission_id IS NOT NULL;
        CREATE INDEX idx_alternative_submission_relation_submission
            ON alternative_submission_relation(submission_id);

        CREATE TABLE grammar_submission (
            submission_id INTEGER PRIMARY KEY,
            gender TEXT,
            gender_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (gender_uncertain IN (0, 1)
                    AND (gender IS NOT NULL OR gender_uncertain = 0)),
            plural TEXT,
            plural_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (plural_uncertain IN (0, 1)
                    AND (plural IS NOT NULL OR plural_uncertain = 0)),
            agentive TEXT,
            agentive_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (agentive_uncertain IN (0, 1)
                    AND (agentive IS NOT NULL OR agentive_uncertain = 0)),
            conjugated_form TEXT,
            conjugated_form_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (conjugated_form_uncertain IN (0, 1)
                    AND (conjugated_form IS NOT NULL
                        OR conjugated_form_uncertain = 0)),
            negation TEXT,
            negation_uncertain INTEGER NOT NULL DEFAULT 0
                CHECK (negation_uncertain IN (0, 1)
                    AND (negation IS NOT NULL OR negation_uncertain = 0)),
            note TEXT,
            FOREIGN KEY (submission_id) REFERENCES submission(submission_id)
        );
    """
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)


def _legacy_concept_proposal(connection, row, resolved_concept_id):
    label = row["proposed_concept_label"]
    if label is None or not label.strip():
        return None
    if resolved_concept_id is not None:
        status = "resolved"
    elif row["status"] == "rejected":
        status = "rejected"
    else:
        status = "pending"
    cursor = connection.execute("""
        INSERT INTO concept_proposal (
            proposed_label, status, resolved_concept_id, created_at,
            resolved_at, resolution_note
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (
        label,
        status,
        resolved_concept_id,
        row["submitted_at"],
        row["reviewed_at"] if status in {"resolved", "rejected"} else None,
        row["proposed_concept_note"],
    ))
    return cursor.lastrowid


def _resolved_alternative(connection, row):
    if row["status"] != "accepted":
        return None
    result = connection.execute("""
        SELECT alternative_id
        FROM assignment
        WHERE occurrence_id = ? AND is_current = 1
    """, (row["occurrence_id"],)).fetchone()
    return result[0] if result is not None else None


def _resolved_concept(connection, alternative_id):
    if alternative_id is None:
        return None
    return connection.execute(
        "SELECT concept_id FROM alternative WHERE alternative_id = ?",
        (alternative_id,),
    ).fetchone()[0]


def _migrate_legacy_rows(connection, legacy_rows):
    for row in legacy_rows:
        status = "pending" if row["status"] == "pending" else "resolved"
        resolution = None if status == "pending" else row["status"]
        resolved_alternative_id = _resolved_alternative(connection, row)
        resolved_concept_id = _resolved_concept(
            connection, resolved_alternative_id
        ) if row["proposed_concept_label"] else None
        concept_proposal_id = _legacy_concept_proposal(
            connection, row, resolved_concept_id
        )

        connection.execute("""
            INSERT INTO submission (
                submission_id, occurrence_id, submission_type, status,
                resolution, submitted_at, resolved_at, submitted_by,
                reviewed_by, review_note, legacy_reviewed_at
            ) VALUES (?, ?, 'ALTERNATIVE', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["submission_id"], row["occurrence_id"], status, resolution,
            row["submitted_at"],
            row["reviewed_at"] if status == "resolved" else None,
            row["submitted_by"], row["reviewed_by"], row["review_comment"],
            row["reviewed_at"],
        ))

        relation_answer = {
            "yes": "YES", "no": "NO", "not_sure": "UNSURE",
            "unsure": "UNSURE",
        }.get(row["proposed_relation_answer"])
        proposal_kind = {
            "existing_alternative": "EXISTING",
            "new_alternative": "NEW",
            "not_sure": "UNSURE",
        }[row["proposal_type"]]
        connection.execute("""
            INSERT INTO alternative_submission (
                submission_id, proposal_kind, reference_concept_id,
                reference_concept_proposal_id,
                proposed_existing_alternative_id,
                phonological_relation_answer, analysis_note,
                resolved_alternative_id, is_legacy,
                legacy_proposed_alternative_label,
                legacy_proposed_concept_note,
                legacy_proposed_concept_status,
                legacy_concept_uncertainty_note,
                legacy_alternative_uncertainty_note,
                legacy_proposed_relation_answer,
                legacy_related_alternative_id,
                legacy_related_submission_id,
                legacy_phonological_parameter
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["submission_id"], proposal_kind,
            row["proposed_concept_id"], concept_proposal_id,
            row["proposed_alternative_id"], relation_answer,
            row["alternative_uncertainty_note"], resolved_alternative_id,
            row["proposed_alternative_label"],
            row["proposed_concept_note"],
            row["proposed_concept_status"],
            row["concept_uncertainty_note"],
            row["alternative_uncertainty_note"],
            row["proposed_relation_answer"],
            row["proposed_related_alternative_id"],
            row["proposed_related_submission_id"],
            row["proposed_phonological_parameter"],
        ))

        target_alternative = row["proposed_related_alternative_id"]
        target_submission = row["proposed_related_submission_id"]
        parameter = row["proposed_phonological_parameter"]
        if parameter and ((target_alternative is None) != (target_submission is None)):
            connection.execute("""
                INSERT INTO alternative_submission_relation (
                    submission_id, target_alternative_id,
                    target_submission_id, phonological_parameter
                ) VALUES (?, ?, ?, ?)
            """, (
                row["submission_id"], target_alternative,
                target_submission, parameter,
            ))


def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path = Path(database_path)
    backup_path = Path(backup_path) if backup_path is not None else None
    if not database_path.exists():
        raise SystemExit(f"No existe la base: {database_path}")

    connection = sqlite3.connect(database_path)
    try:
        if migration_is_complete(connection):
            return False
        legacy_columns = columns(connection, "submission")
        if not {"proposal_type", "proposed_concept_id", "status"} <= legacy_columns:
            raise RuntimeError(
                "La base no coincide con el esquema de submission posterior a 004"
            )
    finally:
        connection.close()

    if backup_path is not None:
        if backup_path.exists():
            raise SystemExit(
                f"El backup ya existe y no se sobrescribira: {backup_path}"
            )
        shutil.copy2(database_path, backup_path)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        legacy_rows = connection.execute(
            "SELECT * FROM submission ORDER BY submission_id"
        ).fetchall()
        legacy_count = len(legacy_rows)
        connection.execute("ALTER TABLE submission RENAME TO submission_legacy_008")
        for legacy_index in (
            "idx_submission_status",
            "idx_submission_occurrence",
            "idx_submission_related_pending_submission",
        ):
            connection.execute(f"DROP INDEX IF EXISTS {legacy_index}")
        _create_schema(connection)
        _migrate_legacy_rows(connection, legacy_rows)
        if connection.execute("SELECT COUNT(*) FROM submission").fetchone()[0] != legacy_count:
            raise RuntimeError("La migracion no preservo el numero de submissions")
        if connection.execute(
            "SELECT COUNT(*) FROM alternative_submission"
        ).fetchone()[0] != legacy_count:
            raise RuntimeError(
                "La migracion no creo una alternative_submission por fila legacy"
            )
        connection.execute("DROP TABLE submission_legacy_008")
        if not migration_is_complete(connection):
            raise RuntimeError("La migracion 008 quedo incompleta")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"La migracion produjo foreign keys invalidas: {violations!r}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return True


if __name__ == "__main__":
    changed = migrate()
    if changed:
        print("Migracion 008 completada. Backup conservado en:", BACKUP_PATH)
    else:
        print("Migracion 008 ya estaba aplicada; no se realizaron cambios.")
