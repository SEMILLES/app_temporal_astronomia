import shutil
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATABASE_PATH=ROOT/"lesico_prototipo.db"
BACKUP_PATH=ROOT/"lesico_prototipo.pre_migration_015.db"
EXPECTED={"alternative_media_id","alternative_id","media_asset_id","role","is_current","supersedes_alternative_media_id","created_at","created_by","retired_at","created_by_collaborator_id","created_by_name_snapshot","created_access_role"}


def columns(connection,table): return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
def migration_is_complete(connection):
    return columns(connection,"alternative_media")==EXPECTED and "one_current_catalog_video_per_alternative" in {row[1] for row in connection.execute("PRAGMA index_list(alternative_media)")}


SCHEMA="""
ALTER TABLE alternative_media RENAME TO alternative_media_legacy_015;
CREATE TABLE alternative_media (
 alternative_media_id INTEGER PRIMARY KEY AUTOINCREMENT,
 alternative_id INTEGER NOT NULL, media_asset_id INTEGER NOT NULL,
 role TEXT NOT NULL DEFAULT 'internal_reference' CHECK(role IN('internal_reference','catalog_video')),
 is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN(0,1)),
 supersedes_alternative_media_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 created_by TEXT, retired_at TEXT, created_by_collaborator_id INTEGER,
 created_by_name_snapshot TEXT, created_access_role TEXT CHECK(created_access_role IN('reviewer','master')),
 FOREIGN KEY(alternative_id) REFERENCES alternative(alternative_id),
 FOREIGN KEY(media_asset_id) REFERENCES media_asset(media_asset_id),
 FOREIGN KEY(supersedes_alternative_media_id) REFERENCES alternative_media(alternative_media_id),
 FOREIGN KEY(created_by_collaborator_id) REFERENCES collaborator(collaborator_id),
 CHECK((is_current=1 AND retired_at IS NULL) OR(is_current=0 AND retired_at IS NOT NULL)),
 CHECK(role!='catalog_video' OR created_access_role IN('reviewer','master'))
);
INSERT INTO alternative_media(alternative_id,media_asset_id,role,is_current,created_at,created_by)
 SELECT alternative_id,media_asset_id,role,1,created_at,created_by FROM alternative_media_legacy_015;
DROP TABLE alternative_media_legacy_015;
CREATE UNIQUE INDEX one_current_catalog_video_per_alternative ON alternative_media(alternative_id) WHERE role='catalog_video' AND is_current=1;
CREATE INDEX idx_alternative_media_asset ON alternative_media(media_asset_id);
"""


def migrate(database_path=DATABASE_PATH,backup_path=BACKUP_PATH):
    database_path=Path(database_path); backup_path=Path(backup_path) if backup_path is not None else None
    db=sqlite3.connect(database_path)
    try:
        if migration_is_complete(db): return False
        current=columns(db,"alternative_media")
        if current!={"alternative_id","media_asset_id","role","created_at","created_by"}: raise RuntimeError("La base no tiene el esquema alternative_media post-014 esperado")
        if not columns(db,"collaborator") or not columns(db,"catalog_publication"): raise RuntimeError("La base no tiene el esquema post-014")
        legacy=db.execute("SELECT * FROM alternative_media ORDER BY alternative_id,media_asset_id").fetchall()
    finally: db.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path,backup_path)
    db=sqlite3.connect(database_path); db.execute("PRAGMA foreign_keys=ON")
    try:
        db.executescript("BEGIN;\n"+SCHEMA)
        preserved=db.execute("SELECT alternative_id,media_asset_id,role,created_at,created_by FROM alternative_media ORDER BY alternative_id,media_asset_id").fetchall()
        if legacy!=preserved: raise RuntimeError("Migration 015 alteró vínculos legacy")
        if db.execute("SELECT count(*) FROM alternative_media WHERE role='catalog_video'").fetchone()[0]: raise RuntimeError("Migration 015 inventó videos canónicos")
        if not migration_is_complete(db): raise RuntimeError("Migration 015 incompleta")
        violations=db.execute("PRAGMA foreign_key_check").fetchall()
        if violations: raise RuntimeError(f"Foreign keys inválidas: {violations!r}")
        db.commit()
    except Exception: db.rollback(); raise
    finally: db.close()
    return True


if __name__=="__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate,"015")
