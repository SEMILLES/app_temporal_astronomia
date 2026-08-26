import sqlite3
from pathlib import Path


BASE_DATOS = Path(__file__).resolve().parent / "lesico_prototipo.db"


def conectar():

    conexion = sqlite3.connect(BASE_DATOS)

    conexion.row_factory = sqlite3.Row

    conexion.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conexion


def crear_base():

    conexion = conectar()

    # SOURCE
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS source (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL UNIQUE
        )
    """)

    # CONCEPT
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS concept (
            concept_id INTEGER PRIMARY KEY AUTOINCREMENT,
            preferred_label TEXT NOT NULL UNIQUE
        )
    """)

    # OCCURRENCE
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS occurrence (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_id INTEGER NOT NULL,
            concept_id INTEGER NOT NULL,

            original_gloss TEXT,
            hyperlink TEXT,

            FOREIGN KEY (source_id)
                REFERENCES source(source_id),

            FOREIGN KEY (concept_id)
                REFERENCES concept(concept_id)
        )
    """)

    # OCCURRENCE SUBMISSION
    conexion.execute("""
        CREATE TABLE IF NOT EXISTS occurrence_submission (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_id INTEGER NOT NULL,
            concept_id INTEGER NOT NULL,

            original_gloss TEXT,
            hyperlink TEXT,

            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),

            approved_occurrence_id INTEGER UNIQUE,

            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,

            FOREIGN KEY (source_id)
                REFERENCES source(source_id),

            FOREIGN KEY (concept_id)
                REFERENCES concept(concept_id),

            FOREIGN KEY (approved_occurrence_id)
                REFERENCES occurrence(occurrence_id)
        )
    """)

    conexion.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_occurrence_submission_status

        ON occurrence_submission(status)
    """)

    conexion.commit()
    conexion.close()
