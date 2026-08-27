import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration.db"

sys.path.insert(0, str(ROOT))

from database import crear_esquema


def table_exists(conexion, table_name):
    return conexion.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def migrate():
    if not DATABASE_PATH.exists():
        raise SystemExit(f"No existe la base: {DATABASE_PATH}")

    if BACKUP_PATH.exists():
        raise SystemExit(
            f"El backup ya existe y no se sobrescribira: {BACKUP_PATH}"
        )

    shutil.copy2(DATABASE_PATH, BACKUP_PATH)

    conexion = sqlite3.connect(DATABASE_PATH)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")

    try:
        required_legacy = ("source", "concept", "occurrence", "occurrence_submission")
        if not all(table_exists(conexion, table) for table in required_legacy):
            raise RuntimeError("La base no coincide con el esquema anterior esperado")

        old_sources = conexion.execute("SELECT * FROM source ORDER BY source_id").fetchall()
        old_concepts = conexion.execute("SELECT * FROM concept ORDER BY concept_id").fetchall()
        old_occurrences = conexion.execute(
            "SELECT * FROM occurrence ORDER BY occurrence_id"
        ).fetchall()
        old_submissions = conexion.execute(
            "SELECT * FROM occurrence_submission ORDER BY submission_id"
        ).fetchall()

        old_occurrence_ids = {row["occurrence_id"] for row in old_occurrences}
        linked_occurrence_ids = {
            row["approved_occurrence_id"]
            for row in old_submissions
            if row["approved_occurrence_id"] is not None
        }

        conexion.execute("BEGIN")
        for table in ("occurrence_submission", "occurrence", "concept", "source"):
            conexion.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")

        crear_esquema(conexion)

        conexion.executemany(
            """
            INSERT INTO source (source_id, source_name)
            VALUES (?, ?)
            """,
            [(row["source_id"], row["source_name"]) for row in old_sources],
        )
        conexion.executemany(
            """
            INSERT INTO concept (concept_id, preferred_label)
            VALUES (?, ?)
            """,
            [(row["concept_id"], row["preferred_label"]) for row in old_concepts],
        )

        conexion.executemany(
            """
            INSERT INTO occurrence (
                occurrence_id, source_id, original_gloss, hyperlink, provenance_note
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["occurrence_id"],
                    row["source_id"],
                    row["original_gloss"],
                    row["hyperlink"],
                    "Migrada desde occurrence canonica del esquema anterior.",
                )
                for row in old_occurrences
            ],
        )

        submission_rows = []
        migration_timestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for row in old_submissions:
            occurrence_id = row["approved_occurrence_id"]
            if occurrence_id is None:
                conexion.execute(
                    """
                    INSERT INTO occurrence (
                        source_id, original_gloss, hyperlink, provenance_note
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row["source_id"],
                        row["original_gloss"],
                        row["hyperlink"],
                        "Ocurrencia creada para conservar un aporte del esquema anterior.",
                    ),
                )
                occurrence_id = conexion.execute(
                    "SELECT last_insert_rowid()"
                ).fetchone()[0]

            status = "accepted" if row["status"] == "approved" else row["status"]
            submission_rows.append(
                (
                    row["submission_id"],
                    occurrence_id,
                    row["concept_id"],
                    None,
                    None,
                    "not_sure",
                    status,
                    row["submitted_at"],
                    None,
                    row["reviewed_at"],
                    None,
                    "Migrada desde occurrence_submission del esquema anterior.",
                )
            )

        orphan_rows = [
            row for row in old_occurrences
            if row["occurrence_id"] not in linked_occurrence_ids
        ]
        next_submission_id = (max((row["submission_id"] for row in old_submissions), default=0) + 1)
        for offset, row in enumerate(orphan_rows):
            submission_rows.append(
                (
                    next_submission_id + offset,
                    row["occurrence_id"],
                    row["concept_id"],
                    None,
                    None,
                    "not_sure",
                    "accepted",
                    migration_timestamp,
                    None,
                    migration_timestamp,
                    None,
                    "Migrada desde una occurrence canonica sin occurrence_submission.",
                )
            )

        conexion.executemany(
            """
            INSERT INTO submission (
                submission_id, occurrence_id, proposed_concept_id,
                proposed_alternative_id, proposed_alternative_label,
                proposal_type, status, submitted_at, submitted_by,
                reviewed_at, reviewed_by, review_comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            submission_rows,
        )

        verify(conexion, old_sources, old_concepts, old_occurrences, old_submissions)
        for table in (
            "occurrence_submission_legacy",
            "occurrence_legacy",
            "concept_legacy",
            "source_legacy",
        ):
            conexion.execute(f"DROP TABLE {table}")
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def verify(conexion, old_sources, old_concepts, old_occurrences, old_submissions):
    assert conexion.execute("SELECT COUNT(*) FROM source").fetchone()[0] == len(old_sources)
    assert conexion.execute("SELECT COUNT(*) FROM concept").fetchone()[0] == len(old_concepts)
    expected_occurrences = len(old_occurrences) + sum(
        row["approved_occurrence_id"] is None for row in old_submissions
    )
    assert conexion.execute("SELECT COUNT(*) FROM occurrence").fetchone()[0] == expected_occurrences
    linked_occurrence_ids = {
        row["approved_occurrence_id"]
        for row in old_submissions
        if row["approved_occurrence_id"] is not None
    }
    orphan_count = sum(
        row["occurrence_id"] not in linked_occurrence_ids
        for row in old_occurrences
    )
    assert conexion.execute("SELECT COUNT(*) FROM submission").fetchone()[0] == (
        len(old_submissions) + orphan_count
    )

    current_submission_ids = {
        row["submission_id"]
        for row in conexion.execute("SELECT submission_id FROM submission")
    }
    assert {
        row["submission_id"] for row in old_submissions
    } <= current_submission_ids

    for old in old_sources:
        current = conexion.execute(
            "SELECT source_name FROM source WHERE source_id = ?",
            (old["source_id"],),
        ).fetchone()
        assert current["source_name"] == old["source_name"]

    for old in old_concepts:
        current = conexion.execute(
            "SELECT preferred_label FROM concept WHERE concept_id = ?",
            (old["concept_id"],),
        ).fetchone()
        assert current["preferred_label"] == old["preferred_label"]

    for old in old_occurrences:
        current = conexion.execute(
            """
            SELECT source_id, original_gloss, hyperlink
            FROM occurrence WHERE occurrence_id = ?
            """,
            (old["occurrence_id"],),
        ).fetchone()
        assert tuple(current) == (
            old["source_id"],
            old["original_gloss"],
            old["hyperlink"],
        )

    for old in old_submissions:
        current = conexion.execute(
            """
            SELECT proposed_concept_id, proposal_type, status
            FROM submission WHERE submission_id = ?
            """,
            (old["submission_id"],),
        ).fetchone()
        assert current["proposed_concept_id"] == old["concept_id"]
        assert current["proposal_type"] == "not_sure"
        assert current["status"] == (
            "accepted" if old["status"] == "approved" else old["status"]
        )

    assert conexion.execute(
        "SELECT COUNT(*) FROM submission WHERE occurrence_id NOT IN "
        "(SELECT occurrence_id FROM occurrence)"
    ).fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM assignment").fetchone()[0] == 0
    assert conexion.execute("SELECT COUNT(*) FROM alternative").fetchone()[0] == 0


if __name__ == "__main__":
    migrate()
    print("Migracion completada. Backup conservado en:", BACKUP_PATH)
