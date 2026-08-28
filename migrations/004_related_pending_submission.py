import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_004.db"

sys.path.insert(0, str(ROOT))


def has_column(conexion, table, column):
    return column in {
        row[1] for row in conexion.execute(f"PRAGMA table_info({table})")
    }


def migrate():
    db_path = Path(DATABASE_PATH)
    backup_path = Path(BACKUP_PATH)
    if not db_path.exists():
        raise SystemExit(f"No existe la base: {db_path}")
    if backup_path.exists():
        raise SystemExit(f"El backup ya existe y no se sobrescribira: {backup_path}")

    shutil.copy2(db_path, backup_path)
    conexion = sqlite3.connect(str(db_path))
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")

    try:
        if not has_column(conexion, "submission", "proposed_related_alternative_id"):
            raise RuntimeError("La base no tiene el esquema de submission previo. Ejecuta la migracion 003 antes.")
        if has_column(conexion, "submission", "proposed_related_submission_id"):
            print("La migracion 004 ya se aplico; no se hace nada.")
            return

        conexion.execute("BEGIN")
        columns = conexion.execute("PRAGMA table_info(submission)").fetchall()
        column_sql = []
        for column in columns:
            name = column[1]
            if name == "submission_id":
                column_sql.append(f"{name} INTEGER PRIMARY KEY AUTOINCREMENT")
            elif name in {"occurrence_id", "proposed_concept_id", "proposed_alternative_id", "proposed_concept_label", "proposed_concept_note", "proposed_alternative_label", "proposed_concept_status", "concept_uncertainty_note", "proposed_relation_answer", "proposed_related_alternative_id", "proposed_phonological_parameter", "alternative_uncertainty_note", "proposal_type", "status", "submitted_at", "submitted_by", "reviewed_at", "reviewed_by", "review_comment"}:
                column_sql.append(f"{name} {column[2]}")
            else:
                column_sql.append(f"{name} {column[2]}")

        column_sql = [
            "submission_id INTEGER PRIMARY KEY AUTOINCREMENT",
            "occurrence_id INTEGER NOT NULL UNIQUE",
            "proposed_concept_id INTEGER",
            "proposed_concept_label TEXT",
            "proposed_concept_note TEXT",
            "proposed_alternative_id INTEGER",
            "proposed_alternative_label TEXT",
            "proposed_concept_status TEXT",
            "concept_uncertainty_note TEXT",
            "proposed_relation_answer TEXT",
            "proposed_related_alternative_id INTEGER",
            "proposed_related_submission_id INTEGER",
            "proposed_phonological_parameter TEXT",
            "alternative_uncertainty_note TEXT",
            "proposal_type TEXT NOT NULL CHECK (proposal_type IN ('existing_alternative', 'new_alternative', 'not_sure'))",
            "status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected'))",
            "submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "submitted_by TEXT",
            "reviewed_at TEXT",
            "reviewed_by TEXT",
            "review_comment TEXT",
            "FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id)",
            "FOREIGN KEY (proposed_concept_id) REFERENCES concept(concept_id)",
            "FOREIGN KEY (proposed_alternative_id) REFERENCES alternative(alternative_id)",
            "FOREIGN KEY (proposed_related_alternative_id) REFERENCES alternative(alternative_id)",
            "FOREIGN KEY (proposed_related_submission_id) REFERENCES submission(submission_id)",
            "CHECK ((proposal_type = 'existing_alternative' AND proposed_alternative_id IS NOT NULL AND proposed_alternative_label IS NULL) OR (proposal_type = 'new_alternative' AND proposed_alternative_id IS NULL) OR (proposal_type = 'not_sure' AND proposed_alternative_id IS NULL AND proposed_alternative_label IS NULL))"
        ]

        conexion.execute("ALTER TABLE submission RENAME TO submission_legacy_004")
        conexion.execute(f"CREATE TABLE submission ({', '.join(column_sql)})")
        conexion.execute("""
            INSERT INTO submission (
                submission_id, occurrence_id, proposed_concept_id,
                proposed_concept_label, proposed_concept_note,
                proposed_alternative_id, proposed_alternative_label,
                proposed_concept_status, concept_uncertainty_note,
                proposed_relation_answer, proposed_related_alternative_id,
                proposed_related_submission_id, proposed_phonological_parameter,
                alternative_uncertainty_note, proposal_type,
                status, submitted_at, submitted_by,
                reviewed_at, reviewed_by, review_comment
            )
            SELECT
                submission_id, occurrence_id, proposed_concept_id,
                proposed_concept_label, proposed_concept_note,
                proposed_alternative_id, proposed_alternative_label,
                proposed_concept_status, concept_uncertainty_note,
                proposed_relation_answer, proposed_related_alternative_id,
                NULL, proposed_phonological_parameter,
                alternative_uncertainty_note, proposal_type,
                status, submitted_at, submitted_by,
                reviewed_at, reviewed_by, review_comment
            FROM submission_legacy_004
        """)
        conexion.execute("DROP TABLE submission_legacy_004")
        conexion.execute("CREATE INDEX IF NOT EXISTS idx_submission_related_pending_submission ON submission(proposed_related_submission_id)")
        conexion.execute("PRAGMA foreign_key_check")
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


if __name__ == "__main__":
    migrate()
    print("Migracion 004 completada. Backup conservado en:", BACKUP_PATH)
