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
    "concept_proposal",
    "occurrence_draft",
    "occurrence_concept_reference",
    "alternative_submission",
    "alternative_submission_relation",
    "grammar_submission",
    "renumber_event",
    "renumber_change",
    "alternative_submission_morphology",
    "alternative_submission_component",
    "alternative_morphology",
    "alternative_component",
    "collaborator",
    "activity_event",
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
            grammar_note TEXT,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_occurrence_grammar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            change_note TEXT,
            created_from_submission_id INTEGER,
            FOREIGN KEY (occurrence_id)
                REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (supersedes_occurrence_grammar_id)
                REFERENCES occurrence_grammar(occurrence_grammar_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id)
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
            created_from_submission_id INTEGER,
            FOREIGN KEY (occurrence_id) REFERENCES occurrence(occurrence_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_assignment_id)
                REFERENCES assignment(assignment_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_current_assignment_per_occurrence
            ON assignment(occurrence_id) WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_assignment_alternative
            ON assignment(alternative_id);
        CREATE INDEX IF NOT EXISTS idx_assignment_occurrence
            ON assignment(occurrence_id);

        CREATE TABLE IF NOT EXISTS alternative_relation (
            alternative_relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_low_id INTEGER NOT NULL,
            alternative_high_id INTEGER NOT NULL,
            phonological_parameter TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1
                CHECK (is_current IN (0, 1)),
            supersedes_alternative_relation_id INTEGER,
            created_from_submission_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT,
            FOREIGN KEY (alternative_low_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (alternative_high_id)
                REFERENCES alternative(alternative_id),
            FOREIGN KEY (supersedes_alternative_relation_id)
                REFERENCES alternative_relation(alternative_relation_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id),
            CHECK (alternative_low_id < alternative_high_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
            one_current_alternative_relation_per_parameter
            ON alternative_relation(
                alternative_low_id, alternative_high_id,
                phonological_parameter
            ) WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_alternative_relation_low
            ON alternative_relation(alternative_low_id);
        CREATE INDEX IF NOT EXISTS idx_alternative_relation_high
            ON alternative_relation(alternative_high_id);

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

        CREATE TABLE IF NOT EXISTS concept_proposal (
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

        CREATE TABLE IF NOT EXISTS occurrence_draft (
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

        CREATE TABLE IF NOT EXISTS occurrence_concept_reference (
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

        CREATE UNIQUE INDEX IF NOT EXISTS
            one_current_occurrence_concept_reference
            ON occurrence_concept_reference(occurrence_id)
            WHERE is_current = 1;
        CREATE INDEX IF NOT EXISTS idx_occurrence_concept_reference_occurrence
            ON occurrence_concept_reference(occurrence_id);

        CREATE TABLE IF NOT EXISTS submission (
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

        CREATE UNIQUE INDEX IF NOT EXISTS
            one_pending_submission_per_occurrence_type
            ON submission(occurrence_id, submission_type)
            WHERE status = 'pending';
        CREATE INDEX IF NOT EXISTS idx_submission_occurrence
            ON submission(occurrence_id);
        CREATE INDEX IF NOT EXISTS idx_submission_status
            ON submission(status);

        CREATE TABLE IF NOT EXISTS alternative_submission (
            submission_id INTEGER PRIMARY KEY,
            proposal_kind TEXT NOT NULL
                CHECK (proposal_kind IN ('EXISTING', 'NEW', 'UNSURE')),
            reference_concept_id INTEGER,
            reference_concept_proposal_id INTEGER,
            proposed_existing_alternative_id INTEGER,
            phonological_relation_answer TEXT
                CHECK (phonological_relation_answer IN (
                    'YES', 'NO', 'UNSURE'
                )),
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

        CREATE TABLE IF NOT EXISTS alternative_submission_relation (
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

        CREATE UNIQUE INDEX IF NOT EXISTS
            one_alternative_relation_target_per_parameter
            ON alternative_submission_relation(
                submission_id, target_alternative_id, phonological_parameter
            ) WHERE target_alternative_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS
            one_submission_relation_target_per_parameter
            ON alternative_submission_relation(
                submission_id, target_submission_id, phonological_parameter
            ) WHERE target_submission_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS
            idx_alternative_submission_relation_submission
            ON alternative_submission_relation(submission_id);

        CREATE TABLE IF NOT EXISTS grammar_submission (
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

        CREATE TABLE IF NOT EXISTS renumber_event (
            renumber_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept_id INTEGER NOT NULL,
            origin TEXT NOT NULL
                CHECK (origin IN ('automatic_assisted', 'manual')),
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_from_submission_id INTEGER,
            created_by TEXT,
            FOREIGN KEY (concept_id) REFERENCES concept(concept_id),
            FOREIGN KEY (created_from_submission_id)
                REFERENCES submission(submission_id),
            CHECK (origin != 'manual'
                OR (reason IS NOT NULL AND length(trim(reason)) > 0))
        );
        CREATE INDEX IF NOT EXISTS idx_renumber_event_concept
            ON renumber_event(concept_id);

        CREATE TABLE IF NOT EXISTS renumber_change (
            renumber_change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            renumber_event_id INTEGER NOT NULL,
            alternative_id INTEGER NOT NULL,
            old_working_label TEXT,
            new_working_label TEXT NOT NULL
                CHECK (length(trim(new_working_label)) > 0),
            FOREIGN KEY (renumber_event_id)
                REFERENCES renumber_event(renumber_event_id),
            FOREIGN KEY (alternative_id) REFERENCES alternative(alternative_id),
            UNIQUE (renumber_event_id, alternative_id)
        );
        CREATE INDEX IF NOT EXISTS idx_renumber_change_alternative
            ON renumber_change(alternative_id);

        CREATE TABLE IF NOT EXISTS alternative_submission_morphology (
            submission_id INTEGER PRIMARY KEY,
            component_count INTEGER CHECK(component_count IS NULL OR component_count >= 1),
            component_count_not_applicable INTEGER NOT NULL DEFAULT 0 CHECK(component_count_not_applicable IN (0,1)),
            free_permutation TEXT, note TEXT,
            FOREIGN KEY(submission_id) REFERENCES alternative_submission(submission_id),
            CHECK(component_count_not_applicable=0 OR component_count IS NULL),
            CHECK((component_count_not_applicable=1 AND free_permutation='N/A') OR (component_count_not_applicable=0 AND ((component_count IS NULL AND free_permutation IS NULL) OR (component_count=1 AND free_permutation='N/A') OR (component_count>=2 AND free_permutation IS NOT NULL))))
        );
        CREATE TABLE IF NOT EXISTS alternative_submission_component (
            component_id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL, position INTEGER NOT NULL CHECK(position>=1),
            component_alternative_id INTEGER, component_label TEXT, note TEXT,
            FOREIGN KEY(submission_id) REFERENCES alternative_submission_morphology(submission_id),
            FOREIGN KEY(component_alternative_id) REFERENCES alternative(alternative_id),
            UNIQUE(submission_id,position),
            CHECK(component_alternative_id IS NOT NULL OR component_label IS NOT NULL OR note IS NOT NULL)
        );
        CREATE INDEX IF NOT EXISTS idx_submission_component_alternative ON alternative_submission_component(component_alternative_id);
        CREATE TABLE IF NOT EXISTS alternative_morphology (
            alternative_morphology_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_id INTEGER NOT NULL,
            component_count INTEGER CHECK(component_count IS NULL OR component_count>=1),
            component_count_not_applicable INTEGER NOT NULL DEFAULT 0 CHECK(component_count_not_applicable IN(0,1)),
            free_permutation TEXT, note TEXT,
            is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN(0,1)),
            supersedes_alternative_morphology_id INTEGER,
            created_from_submission_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, created_by TEXT,
            FOREIGN KEY(alternative_id) REFERENCES alternative(alternative_id),
            FOREIGN KEY(supersedes_alternative_morphology_id) REFERENCES alternative_morphology(alternative_morphology_id),
            FOREIGN KEY(created_from_submission_id) REFERENCES submission(submission_id),
            CHECK(component_count_not_applicable=0 OR component_count IS NULL),
            CHECK((component_count_not_applicable=1 AND free_permutation='N/A') OR (component_count_not_applicable=0 AND ((component_count IS NULL AND free_permutation IS NULL) OR (component_count=1 AND free_permutation='N/A') OR (component_count>=2 AND free_permutation IS NOT NULL))))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_current_morphology_per_alternative ON alternative_morphology(alternative_id) WHERE is_current=1;
        CREATE INDEX IF NOT EXISTS idx_alternative_morphology_alternative ON alternative_morphology(alternative_id);
        CREATE TABLE IF NOT EXISTS alternative_component (
            alternative_component_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alternative_morphology_id INTEGER NOT NULL, position INTEGER NOT NULL CHECK(position>=1),
            component_alternative_id INTEGER, component_label TEXT, note TEXT,
            FOREIGN KEY(alternative_morphology_id) REFERENCES alternative_morphology(alternative_morphology_id),
            FOREIGN KEY(component_alternative_id) REFERENCES alternative(alternative_id),
            UNIQUE(alternative_morphology_id,position),
            CHECK(component_alternative_id IS NOT NULL OR component_label IS NOT NULL OR note IS NOT NULL)
        );
        CREATE INDEX IF NOT EXISTS idx_alternative_component_target ON alternative_component(component_alternative_id);
        CREATE TABLE IF NOT EXISTS collaborator (
            collaborator_id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL CHECK(length(trim(display_name)) > 0),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS activity_event (
            activity_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL CHECK(length(trim(event_type)) > 0),
            entity_type TEXT, entity_id INTEGER, collaborator_id INTEGER,
            collaborator_name_snapshot TEXT,
            access_role TEXT NOT NULL CHECK(access_role IN ('analyst','reviewer','master')),
            occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            comment TEXT,
            FOREIGN KEY(collaborator_id) REFERENCES collaborator(collaborator_id)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_event_collaborator ON activity_event(collaborator_id,occurred_at);
        CREATE INDEX IF NOT EXISTS idx_activity_event_entity ON activity_event(entity_type,entity_id,occurred_at);
    """)
