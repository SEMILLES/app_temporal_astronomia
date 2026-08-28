import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_002.db"

sys.path.insert(0, str(ROOT))

from database import crear_esquema


def columns(conexion, table):
    return {row[1] for row in conexion.execute(f"PRAGMA table_info({table})")}


def add_columns(conexion, table, definitions):
    existing = columns(conexion, table)
    for name, definition in definitions:
        if name not in existing:
            conexion.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate():
    if not DATABASE_PATH.exists():
        raise SystemExit(f"No existe la base: {DATABASE_PATH}")
    if BACKUP_PATH.exists():
        raise SystemExit(f"El backup ya existe y no se sobrescribira: {BACKUP_PATH}")

    shutil.copy2(DATABASE_PATH, BACKUP_PATH)
    conexion = sqlite3.connect(DATABASE_PATH)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")

    try:
        expected = ("source", "occurrence", "occurrence_revision", "alternative", "submission")
        if not all(columns(conexion, table) for table in expected):
            raise RuntimeError("La base no coincide con el esquema posterior a la migracion 001")

        before = snapshot_counts(conexion)
        before_values = snapshot_values(conexion)
        conexion.execute("BEGIN")

        add_columns(conexion, "source", [
            ("start_year", "INTEGER"),
            ("end_year", "INTEGER"),
            ("end_year_status", "TEXT"),
            ("updated_at", "TEXT"),
            ("created_by", "TEXT"),
            ("updated_by", "TEXT"),
        ])
        add_columns(conexion, "occurrence", [("occurrence_year", "INTEGER")])
        add_columns(conexion, "occurrence_revision", [("occurrence_year", "INTEGER")])
        add_columns(conexion, "alternative", [("original_code", "TEXT")])
        add_columns(conexion, "submission", [
            ("proposed_concept_status", "TEXT"),
            ("concept_uncertainty_note", "TEXT"),
            ("proposed_relation_answer", "TEXT"),
            ("proposed_related_alternative_id", "INTEGER"),
            ("proposed_phonological_parameter", "TEXT"),
            ("alternative_uncertainty_note", "TEXT"),
        ])

        conexion.execute("ALTER TABLE submission RENAME TO submission_legacy_002")
        crear_esquema(conexion)
        conexion.execute("""
            INSERT INTO submission (
                submission_id, occurrence_id, proposed_concept_id,
                proposed_alternative_id, proposed_alternative_label,
                proposal_type, status, submitted_at, submitted_by,
                reviewed_at, reviewed_by, review_comment
            )
            SELECT
                submission_id, occurrence_id, proposed_concept_id,
                proposed_alternative_id, proposed_alternative_label,
                proposal_type, status, submitted_at, submitted_by,
                reviewed_at, reviewed_by, review_comment
            FROM submission_legacy_002
        """)
        conexion.execute("DROP TABLE submission_legacy_002")
        verify(conexion, before, before_values)
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def snapshot_counts(conexion):
    return {
        table: conexion.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("source", "concept", "occurrence", "alternative", "assignment", "submission")
    }


def snapshot_values(conexion):
    return {
        "source": [tuple(row) for row in conexion.execute(
            "SELECT source_id, source_name, source_type, source_reference FROM source ORDER BY source_id"
        )],
        "occurrence": [tuple(row) for row in conexion.execute(
            "SELECT occurrence_id, source_id, original_gloss, hyperlink FROM occurrence ORDER BY occurrence_id"
        )],
        "alternative": [tuple(row) for row in conexion.execute(
            "SELECT alternative_id, concept_id, working_label FROM alternative ORDER BY alternative_id"
        )],
        "submission": [tuple(row) for row in conexion.execute(
            "SELECT submission_id, occurrence_id, proposed_concept_id, proposal_type, status FROM submission ORDER BY submission_id"
        )],
    }


def verify(conexion, before, before_values):
    assert snapshot_counts(conexion) == before
    assert snapshot_values(conexion) == before_values
    assert conexion.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conexion.execute("SELECT COUNT(*) FROM alternative_relation").fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM occurrence_revision").fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM occurrence WHERE occurrence_year IS NOT NULL").fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM alternative WHERE original_code IS NOT NULL").fetchone()[0] == 0


if __name__ == "__main__":
    migrate()
    print("Migracion 002 completada. Backup conservado en:", BACKUP_PATH)
