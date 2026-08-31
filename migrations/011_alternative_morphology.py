import shutil
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATABASE_PATH=ROOT/"lesico_prototipo.db"
BACKUP_PATH=ROOT/"lesico_prototipo.pre_migration_011.db"

TABLE_COLUMNS={
    "alternative_submission_morphology": {"submission_id","component_count","component_count_not_applicable","free_permutation","note"},
    "alternative_submission_component": {"component_id","submission_id","position","component_alternative_id","component_label","note"},
    "alternative_morphology": {"alternative_morphology_id","alternative_id","component_count","component_count_not_applicable","free_permutation","note","is_current","supersedes_alternative_morphology_id","created_from_submission_id","created_at","created_by"},
    "alternative_component": {"alternative_component_id","alternative_morphology_id","position","component_alternative_id","component_label","note"},
}


def columns(connection,table): return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migration_is_complete(connection):
    return all(columns(connection,table)==expected for table,expected in TABLE_COLUMNS.items()) and "one_current_morphology_per_alternative" in {row[1] for row in connection.execute("PRAGMA index_list(alternative_morphology)")}


SCHEMA="""
CREATE TABLE alternative_submission_morphology (
 submission_id INTEGER PRIMARY KEY,
 component_count INTEGER CHECK(component_count IS NULL OR component_count>=1),
 component_count_not_applicable INTEGER NOT NULL DEFAULT 0 CHECK(component_count_not_applicable IN(0,1)),
 free_permutation TEXT,
 note TEXT,
 FOREIGN KEY(submission_id) REFERENCES alternative_submission(submission_id),
 CHECK(component_count_not_applicable=0 OR component_count IS NULL),
 CHECK((component_count_not_applicable=1 AND free_permutation='N/A') OR (component_count_not_applicable=0 AND ((component_count IS NULL AND free_permutation IS NULL) OR (component_count=1 AND free_permutation='N/A') OR (component_count>=2 AND free_permutation IS NOT NULL))))
);
CREATE TABLE alternative_submission_component (
 component_id INTEGER PRIMARY KEY AUTOINCREMENT,
 submission_id INTEGER NOT NULL,
 position INTEGER NOT NULL CHECK(position>=1),
 component_alternative_id INTEGER,
 component_label TEXT,
 note TEXT,
 FOREIGN KEY(submission_id) REFERENCES alternative_submission_morphology(submission_id),
 FOREIGN KEY(component_alternative_id) REFERENCES alternative(alternative_id),
 UNIQUE(submission_id,position),
 CHECK(component_alternative_id IS NOT NULL OR component_label IS NOT NULL OR note IS NOT NULL)
);
CREATE INDEX idx_submission_component_alternative ON alternative_submission_component(component_alternative_id);
CREATE TABLE alternative_morphology (
 alternative_morphology_id INTEGER PRIMARY KEY AUTOINCREMENT,
 alternative_id INTEGER NOT NULL,
 component_count INTEGER CHECK(component_count IS NULL OR component_count>=1),
 component_count_not_applicable INTEGER NOT NULL DEFAULT 0 CHECK(component_count_not_applicable IN(0,1)),
 free_permutation TEXT,
 note TEXT,
 is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN(0,1)),
 supersedes_alternative_morphology_id INTEGER,
 created_from_submission_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 created_by TEXT,
 FOREIGN KEY(alternative_id) REFERENCES alternative(alternative_id),
 FOREIGN KEY(supersedes_alternative_morphology_id) REFERENCES alternative_morphology(alternative_morphology_id),
 FOREIGN KEY(created_from_submission_id) REFERENCES submission(submission_id),
 CHECK(component_count_not_applicable=0 OR component_count IS NULL),
 CHECK((component_count_not_applicable=1 AND free_permutation='N/A') OR (component_count_not_applicable=0 AND ((component_count IS NULL AND free_permutation IS NULL) OR (component_count=1 AND free_permutation='N/A') OR (component_count>=2 AND free_permutation IS NOT NULL))))
);
CREATE UNIQUE INDEX one_current_morphology_per_alternative ON alternative_morphology(alternative_id) WHERE is_current=1;
CREATE INDEX idx_alternative_morphology_alternative ON alternative_morphology(alternative_id);
CREATE TABLE alternative_component (
 alternative_component_id INTEGER PRIMARY KEY AUTOINCREMENT,
 alternative_morphology_id INTEGER NOT NULL,
 position INTEGER NOT NULL CHECK(position>=1),
 component_alternative_id INTEGER,
 component_label TEXT,
 note TEXT,
 FOREIGN KEY(alternative_morphology_id) REFERENCES alternative_morphology(alternative_morphology_id),
 FOREIGN KEY(component_alternative_id) REFERENCES alternative(alternative_id),
 UNIQUE(alternative_morphology_id,position),
 CHECK(component_alternative_id IS NOT NULL OR component_label IS NOT NULL OR note IS NOT NULL)
);
CREATE INDEX idx_alternative_component_target ON alternative_component(component_alternative_id);
"""


def migrate(database_path=DATABASE_PATH,backup_path=BACKUP_PATH):
    database_path=Path(database_path); backup_path=Path(backup_path) if backup_path is not None else None
    if not database_path.exists(): raise SystemExit(f"No existe la base: {database_path}")
    inspection=sqlite3.connect(database_path)
    try:
        if migration_is_complete(inspection): return False
        if not {"renumber_event_id","concept_id"} <= columns(inspection,"renumber_event"): raise RuntimeError("La base no tiene el esquema post-010")
        if any(columns(inspection,table) for table in TABLE_COLUMNS): raise RuntimeError("Existe una instalación parcial de migration 011")
        alternatives=inspection.execute("SELECT * FROM alternative ORDER BY alternative_id").fetchall()
    finally: inspection.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path,backup_path)
    connection=sqlite3.connect(database_path);connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript("BEGIN;\n"+SCHEMA)
        if alternatives!=connection.execute("SELECT * FROM alternative ORDER BY alternative_id").fetchall(): raise RuntimeError("Migration 011 alteró alternatives")
        if any(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLE_COLUMNS): raise RuntimeError("Migration 011 inventó morphology")
        if not migration_is_complete(connection): raise RuntimeError("Migration 011 incompleta")
        violations=connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations: raise RuntimeError(f"Foreign keys inválidas: {violations!r}")
        connection.commit()
    except Exception: connection.rollback();raise
    finally: connection.close()
    return True


if __name__=="__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate, "011")
