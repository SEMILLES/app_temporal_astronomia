import os
import sqlite3
from pathlib import Path


DEFAULT_BASE_DATOS = Path(__file__).resolve().parent / "lesico_prototipo.db"
_configured_database = os.environ.get("LESICO_DATABASE_PATH")
USING_EXPLICIT_DATABASE = _configured_database is not None
if USING_EXPLICIT_DATABASE:
    if not _configured_database.strip():
        raise RuntimeError("LESICO_DATABASE_PATH no puede estar vacía")
    BASE_DATOS = Path(_configured_database).expanduser().resolve()
else:
    BASE_DATOS = DEFAULT_BASE_DATOS


REQUIRED_APPLICATION_TABLES = frozenset({
    "source",
    "concept",
    "occurrence",
    "alternative",
    "assignment",
    "alternative_relation",
    "occurrence_grammar",
    "source_revision",
    "source_systematization",
    "occurrence_revision",
    "media_asset",
    "occurrence_media",
    "alternative_media",
    "submission",
})


def conectar():

    conexion = sqlite3.connect(BASE_DATOS)

    conexion.row_factory = sqlite3.Row

    conexion.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conexion


def validar_base_explicita():
    if not BASE_DATOS.is_file():
        raise RuntimeError(
            f"LESICO_DATABASE_PATH no existe o no es un archivo: {BASE_DATOS}"
        )

    try:
        uri = BASE_DATOS.as_uri() + "?mode=ro"
        conexion = sqlite3.connect(uri, uri=True)
        try:
            conexion.execute("PRAGMA query_only = ON")
            tables = {
                row[0]
                for row in conexion.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conexion.close()
    except sqlite3.Error as error:
        raise RuntimeError(
            f"LESICO_DATABASE_PATH no es una SQLite utilizable: {BASE_DATOS}"
        ) from error

    missing = sorted(REQUIRED_APPLICATION_TABLES - tables)
    if missing:
        raise RuntimeError(
            "LESICO_DATABASE_PATH no contiene las tablas requeridas: "
            + ", ".join(missing)
        )


def preparar_base_para_startup():
    print(f"Database: {BASE_DATOS}")
    if USING_EXPLICIT_DATABASE:
        validar_base_explicita()
    else:
        crear_base()


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
            legacy_source_code TEXT,
            source_scope TEXT
                CHECK (source_scope IN ('INSTITUTIONAL', 'PERSONAL')),
            format_original TEXT,
            format_detail TEXT,
            region_description TEXT,
            characterization TEXT,
            reported_entry_count INTEGER
                CHECK (reported_entry_count >= 0),
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
            legacy_occurrence_id TEXT,
            source_id INTEGER NOT NULL,
            original_gloss TEXT,
            hyperlink TEXT,
            legacy_source_detail_1 TEXT,
            legacy_source_detail_2 TEXT,
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
            legacy_source_code TEXT,
            source_scope TEXT
                CHECK (source_scope IN ('INSTITUTIONAL', 'PERSONAL')),
            format_original TEXT,
            format_detail TEXT,
            region_description TEXT,
            characterization TEXT,
            reported_entry_count INTEGER
                CHECK (reported_entry_count >= 0),
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_by TEXT,
            change_note TEXT,
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_revision_source
            ON source_revision(source_id);

        CREATE TABLE IF NOT EXISTS source_systematization (
            source_systematization_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN (
                    'NOT_STARTED', 'PARTIAL', 'COMPLETE', 'UNKNOWN'
                )),
            reviewed_at TEXT NOT NULL,
            coverage_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (source_id) REFERENCES source(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_systematization_source_reviewed
            ON source_systematization(
                source_id, reviewed_at, source_systematization_id
            );

        CREATE TABLE IF NOT EXISTS occurrence_revision (
            occurrence_revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            legacy_occurrence_id TEXT,
            source_id INTEGER NOT NULL,
            original_gloss TEXT,
            hyperlink TEXT,
            legacy_source_detail_1 TEXT,
            legacy_source_detail_2 TEXT,
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

        CREATE TABLE IF NOT EXISTS occurrence_grammar (
            occurrence_grammar_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            gender TEXT,
            plural TEXT,
            agentive TEXT,
            conjugated_form TEXT,
            negation TEXT,
            grammar_note TEXT,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_occurrence_grammar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            change_note TEXT,
            FOREIGN KEY (occurrence_id)
                REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (supersedes_occurrence_grammar_id)
                REFERENCES occurrence_grammar(occurrence_grammar_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_current_grammar_per_occurrence
            ON occurrence_grammar(occurrence_id) WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_occurrence_grammar_occurrence
            ON occurrence_grammar(occurrence_id);

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
            proposed_concept_label TEXT,
            proposed_concept_note TEXT,
            proposed_alternative_id INTEGER,
            proposed_alternative_label TEXT,
            proposed_concept_status TEXT,
            concept_uncertainty_note TEXT,
            proposed_relation_answer TEXT,
            proposed_related_alternative_id INTEGER,
            proposed_related_submission_id INTEGER,
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
            FOREIGN KEY (proposed_related_submission_id)
                REFERENCES submission(submission_id),
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
