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

    crear_esquema(conexion)

    conexion.commit()
    conexion.close()


def crear_esquema(conexion):

    conexion.executescript("""
        CREATE TABLE IF NOT EXISTS source (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL UNIQUE,
            source_type TEXT,
            source_reference TEXT,
            start_year INTEGER,
            end_year INTEGER,
            end_year_status TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            ,updated_at TEXT,
            created_by TEXT,
            updated_by TEXT
        );

        CREATE TABLE IF NOT EXISTS concept (
            concept_id INTEGER PRIMARY KEY AUTOINCREMENT,
            preferred_label TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS occurrence (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            original_gloss TEXT,
            hyperlink TEXT,
            source_locator TEXT,
            provenance_note TEXT,
            occurrence_year INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            updated_at TEXT,
            updated_by TEXT,
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        );

        CREATE TABLE IF NOT EXISTS source_revision (
            source_revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_type TEXT,
            source_reference TEXT,
            start_year INTEGER,
            end_year INTEGER,
            end_year_status TEXT,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_by TEXT,
            change_note TEXT,
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_revision_source
            ON source_revision(source_id);

        CREATE TABLE IF NOT EXISTS occurrence_revision (
            occurrence_revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            original_gloss TEXT,
            hyperlink TEXT,
            source_locator TEXT,
            provenance_note TEXT,
            occurrence_year INTEGER,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_by TEXT,
            change_note TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_occurrence_revision_occurrence
            ON occurrence_revision(occurrence_id);

        CREATE TABLE IF NOT EXISTS alternative (
            alternative_id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept_id INTEGER NOT NULL,
            original_code TEXT,
            working_label TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            retired_at TEXT,
            FOREIGN KEY (concept_id) REFERENCES concept(concept_id)
        );

        CREATE TABLE IF NOT EXISTS assignment (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            alternative_id INTEGER NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_assignment_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_assignment_id)
                REFERENCES assignment(assignment_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_current_assignment_per_occurrence
            ON assignment(occurrence_id) WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_assignment_alternative
            ON assignment(alternative_id);
        CREATE INDEX IF NOT EXISTS idx_assignment_occurrence
            ON assignment(occurrence_id);

        CREATE TABLE IF NOT EXISTS alternative_relation (
            alternative_relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_a_id INTEGER NOT NULL,
            alternative_b_id INTEGER NOT NULL,
            phonological_parameter TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (alternative_a_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (alternative_b_id) REFERENCES alternative(alternative_id),
            CHECK (alternative_a_id <> alternative_b_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_symmetric_alternative_relation
            ON alternative_relation (
                CASE WHEN alternative_a_id < alternative_b_id
                    THEN alternative_a_id ELSE alternative_b_id END,
                CASE WHEN alternative_a_id < alternative_b_id
                    THEN alternative_b_id ELSE alternative_a_id END
            );

        CREATE TABLE IF NOT EXISTS media_asset (
            media_asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            storage_backend TEXT NOT NULL DEFAULT 'local',
            storage_key TEXT NOT NULL UNIQUE,
            original_filename TEXT,
            mime_type TEXT NOT NULL,
            file_size INTEGER,
            checksum TEXT,
            origin_kind TEXT NOT NULL DEFAULT 'uploaded'
                CHECK (origin_kind IN (
                    'uploaded', 'legacy_analysis_material', 'external_reference'
                )),
            origin_label TEXT,
            origin_locator TEXT,
            provenance_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            CHECK (file_size IS NULL OR file_size >= 0)
        );

        CREATE INDEX IF NOT EXISTS idx_media_asset_checksum
            ON media_asset(checksum);

        CREATE TABLE IF NOT EXISTS occurrence_media (
            occurrence_id INTEGER NOT NULL,
            media_asset_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'reference_capture'
                CHECK (role = 'reference_capture'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            PRIMARY KEY (occurrence_id, media_asset_id),
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (media_asset_id) REFERENCES media_asset(media_asset_id)
        );

        CREATE INDEX IF NOT EXISTS idx_occurrence_media_asset
            ON occurrence_media(media_asset_id);

        CREATE TABLE IF NOT EXISTS alternative_media (
            alternative_id INTEGER NOT NULL,
            media_asset_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'internal_reference'
                CHECK (role = 'internal_reference'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            PRIMARY KEY (alternative_id, media_asset_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (media_asset_id) REFERENCES media_asset(media_asset_id)
        );

        CREATE TABLE IF NOT EXISTS submission (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL UNIQUE,
            proposed_concept_id INTEGER,
            proposed_alternative_id INTEGER,
            proposed_alternative_label TEXT,
            proposed_concept_status TEXT,
            concept_uncertainty_note TEXT,
            proposed_relation_answer TEXT,
            proposed_related_alternative_id INTEGER,
            proposed_phonological_parameter TEXT,
            alternative_uncertainty_note TEXT,
            proposal_type TEXT NOT NULL
                CHECK (proposal_type IN (
                    'existing_alternative', 'new_alternative', 'not_sure'
                )),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            submitted_by TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            review_comment TEXT,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (proposed_concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (proposed_alternative_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (proposed_related_alternative_id)
                REFERENCES alternative(alternative_id),
            CHECK (
                (proposal_type = 'existing_alternative'
                    AND proposed_alternative_id IS NOT NULL
                    AND proposed_alternative_label IS NULL)
                OR (proposal_type = 'new_alternative'
                    AND proposed_alternative_id IS NULL
                        )
                OR (proposal_type = 'not_sure'
                    AND proposed_alternative_id IS NULL
                    AND proposed_alternative_label IS NULL)
            )
        );

        CREATE INDEX IF NOT EXISTS idx_submission_status ON submission(status);
        CREATE INDEX IF NOT EXISTS idx_submission_occurrence
            ON submission(occurrence_id);
    """)
