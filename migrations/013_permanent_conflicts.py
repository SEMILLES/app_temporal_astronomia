import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "lesico_prototipo.db"
BACKUP_PATH = ROOT / "lesico_prototipo.pre_migration_013.db"

SCHEMA = """
CREATE TABLE conflict (
 conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
 origin_kind TEXT NOT NULL CHECK(origin_kind IN ('automatic','manual')),
 rule_code TEXT,
 severity TEXT NOT NULL CHECK(severity IN ('blocking','non_blocking')),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
 description TEXT NOT NULL CHECK(length(trim(description))>0),
 justification TEXT,
 resolution_criteria TEXT,
 subject_signature TEXT NOT NULL CHECK(length(trim(subject_signature))>0),
 detection_source TEXT NOT NULL CHECK(detection_source IN ('workflow','global_validation','manual')),
 triggering_entity_type TEXT,
 triggering_entity_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 resolved_at TEXT,
 created_by_collaborator_id INTEGER,
 created_by_name_snapshot TEXT,
 created_access_role TEXT CHECK(created_access_role IN ('reviewer','master')),
 FOREIGN KEY(created_by_collaborator_id) REFERENCES collaborator(collaborator_id),
 CHECK((origin_kind='automatic' AND rule_code IS NOT NULL)
    OR (origin_kind='manual' AND rule_code IS NULL AND justification IS NOT NULL
        AND length(trim(justification))>0 AND resolution_criteria IS NOT NULL
        AND length(trim(resolution_criteria))>0)),
 CHECK((status='open' AND resolved_at IS NULL)
    OR (status='resolved' AND resolved_at IS NOT NULL)),
 CHECK(origin_kind!='manual' OR created_access_role IN ('reviewer','master'))
);
CREATE TABLE conflict_subject (
 conflict_subject_id INTEGER PRIMARY KEY AUTOINCREMENT,
 conflict_id INTEGER NOT NULL,
 subject_type TEXT NOT NULL CHECK(length(trim(subject_type))>0),
 subject_id INTEGER NOT NULL,
 subject_role TEXT NOT NULL CHECK(length(trim(subject_role))>0),
 FOREIGN KEY(conflict_id) REFERENCES conflict(conflict_id),
 UNIQUE(conflict_id,subject_type,subject_id,subject_role)
);
CREATE TABLE conflict_resolution_attempt (
 conflict_resolution_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
 conflict_id INTEGER NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('failed','succeeded')),
 comment TEXT NOT NULL CHECK(length(trim(comment))>0),
 failure_reason TEXT,
 attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 collaborator_id INTEGER,
 collaborator_name_snapshot TEXT,
 access_role TEXT NOT NULL CHECK(access_role IN ('reviewer','master')),
 FOREIGN KEY(conflict_id) REFERENCES conflict(conflict_id),
 FOREIGN KEY(collaborator_id) REFERENCES collaborator(collaborator_id),
 CHECK((outcome='failed' AND failure_reason IS NOT NULL AND length(trim(failure_reason))>0)
    OR (outcome='succeeded' AND failure_reason IS NULL))
);
CREATE UNIQUE INDEX one_open_automatic_conflict
 ON conflict(rule_code,subject_signature)
 WHERE status='open' AND origin_kind='automatic';
CREATE UNIQUE INDEX one_successful_attempt_per_conflict
 ON conflict_resolution_attempt(conflict_id) WHERE outcome='succeeded';
CREATE INDEX idx_conflict_status ON conflict(status,severity,origin_kind,created_at);
CREATE INDEX idx_conflict_subject_entity ON conflict_subject(subject_type,subject_id);
CREATE INDEX idx_conflict_attempt_conflict ON conflict_resolution_attempt(conflict_id,attempted_at);
"""

EXPECTED = {
    "conflict": {"conflict_id","origin_kind","rule_code","severity","status","description","justification","resolution_criteria","subject_signature","detection_source","triggering_entity_type","triggering_entity_id","created_at","resolved_at","created_by_collaborator_id","created_by_name_snapshot","created_access_role"},
    "conflict_subject": {"conflict_subject_id","conflict_id","subject_type","subject_id","subject_role"},
    "conflict_resolution_attempt": {"conflict_resolution_attempt_id","conflict_id","outcome","comment","failure_reason","attempted_at","collaborator_id","collaborator_name_snapshot","access_role"},
}
INDEXES = {"one_open_automatic_conflict","one_successful_attempt_per_conflict","idx_conflict_status","idx_conflict_subject_entity","idx_conflict_attempt_conflict"}
POST_012 = {
    "collaborator": {"collaborator_id","display_name","active","created_at"},
    "activity_event": {"activity_event_id","event_type","entity_type","entity_id","collaborator_id","collaborator_name_snapshot","access_role","occurred_at","comment"},
}

def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

def migration_is_complete(connection):
    indexes={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    return all(columns(connection,t)==c for t,c in EXPECTED.items()) and INDEXES <= indexes

def migrate(database_path=DATABASE_PATH, backup_path=BACKUP_PATH):
    database_path=Path(database_path); backup_path=Path(backup_path) if backup_path is not None else None
    inspection=sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection): return False
        existing_indexes={row[0] for row in inspection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if (not all(columns(inspection,table)==expected for table,expected in POST_012.items())
                or not {"idx_activity_event_collaborator","idx_activity_event_entity"} <= existing_indexes):
            raise RuntimeError("La base no tiene el esquema post-012")
        if any(columns(inspection,table) for table in EXPECTED):
            raise RuntimeError("Existe una instalacion parcial de migration 013")
    finally: inspection.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribira: {backup_path}")
        shutil.copy2(database_path,backup_path)
    connection=sqlite3.connect(database_path); connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript("BEGIN;\n"+SCHEMA)
        if any(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in EXPECTED):
            raise RuntimeError("Migration 013 creo conflictos historicos")
        if not migration_is_complete(connection): raise RuntimeError("Migration 013 incompleta")
        if connection.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys invalidas")
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally: connection.close()
    return True

if __name__ == "__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate,"013")
