import shutil
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATABASE_PATH=ROOT/"lesico_prototipo.db"
BACKUP_PATH=ROOT/"lesico_prototipo.pre_migration_014.db"
SCHEMA="""
CREATE TABLE catalog_publication (
 publication_id INTEGER PRIMARY KEY AUTOINCREMENT,
 version_number INTEGER NOT NULL UNIQUE CHECK(version_number>=1),
 snapshot_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL UNIQUE CHECK(length(snapshot_sha256)=64),
 change_summary_json TEXT NOT NULL, publication_comment TEXT NOT NULL CHECK(length(trim(publication_comment))>0),
 published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 published_by_collaborator_id INTEGER, published_by_name_snapshot TEXT,
 published_access_role TEXT NOT NULL CHECK(published_access_role='master'),
 concept_count INTEGER NOT NULL CHECK(concept_count>=0), alternative_count INTEGER NOT NULL CHECK(alternative_count>=0),
 occurrence_count INTEGER NOT NULL CHECK(occurrence_count>=0), relation_count INTEGER NOT NULL CHECK(relation_count>=0),
 FOREIGN KEY(published_by_collaborator_id) REFERENCES collaborator(collaborator_id)
);
CREATE TABLE publication_open_conflict (
 publication_open_conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
 publication_id INTEGER NOT NULL, conflict_id INTEGER,
 conflict_type_snapshot TEXT, description_snapshot TEXT NOT NULL,
 severity_snapshot TEXT NOT NULL CHECK(severity_snapshot='non_blocking'), subject_signature_snapshot TEXT,
 FOREIGN KEY(publication_id) REFERENCES catalog_publication(publication_id), FOREIGN KEY(conflict_id) REFERENCES conflict(conflict_id),
 UNIQUE(publication_id,conflict_id)
);
CREATE INDEX idx_publication_open_conflict_publication ON publication_open_conflict(publication_id);
CREATE TRIGGER immutable_catalog_publication_update BEFORE UPDATE ON catalog_publication BEGIN SELECT RAISE(ABORT,'catalog publications are immutable'); END;
CREATE TRIGGER immutable_catalog_publication_delete BEFORE DELETE ON catalog_publication BEGIN SELECT RAISE(ABORT,'catalog publications are immutable'); END;
CREATE TRIGGER immutable_publication_conflict_update BEFORE UPDATE ON publication_open_conflict BEGIN SELECT RAISE(ABORT,'publication conflict snapshots are immutable'); END;
CREATE TRIGGER immutable_publication_conflict_delete BEFORE DELETE ON publication_open_conflict BEGIN SELECT RAISE(ABORT,'publication conflict snapshots are immutable'); END;
"""
EXPECTED={"catalog_publication":{"publication_id","version_number","snapshot_json","snapshot_sha256","change_summary_json","publication_comment","published_at","published_by_collaborator_id","published_by_name_snapshot","published_access_role","concept_count","alternative_count","occurrence_count","relation_count"},"publication_open_conflict":{"publication_open_conflict_id","publication_id","conflict_id","conflict_type_snapshot","description_snapshot","severity_snapshot","subject_signature_snapshot"}}
TRIGGERS={"immutable_catalog_publication_update","immutable_catalog_publication_delete","immutable_publication_conflict_update","immutable_publication_conflict_delete"}
def columns(c,t): return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
def migration_is_complete(c): return all(columns(c,t)==v for t,v in EXPECTED.items()) and TRIGGERS <= {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
def migrate(database_path=DATABASE_PATH,backup_path=BACKUP_PATH):
    database_path=Path(database_path); backup_path=Path(backup_path) if backup_path is not None else None
    db=sqlite3.connect(database_path)
    try:
        if migration_is_complete(db): return False
        if not columns(db,"conflict") or not columns(db,"activity_event"): raise RuntimeError("La base no tiene el esquema post-013")
        if any(columns(db,t) for t in EXPECTED): raise RuntimeError("Existe una instalación parcial de migration 014")
    finally: db.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path,backup_path)
    db=sqlite3.connect(database_path); db.execute("PRAGMA foreign_keys=ON")
    try:
        db.executescript("BEGIN;\n"+SCHEMA)
        if not migration_is_complete(db): raise RuntimeError("Migration 014 incompleta")
        if any(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in EXPECTED): raise RuntimeError("Migration 014 creó publicaciones")
        if db.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        db.commit()
    except Exception: db.rollback(); raise
    finally: db.close()
    return True
if __name__=="__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate,"014")
