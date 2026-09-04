import shutil, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATABASE_PATH=ROOT/"lesico_prototipo.db"
BACKUP_PATH=ROOT/"lesico_prototipo.pre_migration_017.db"
STATUS_COLUMNS=(("source_detail_1_status","TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(source_detail_1_status IN('VALUE','NA','UNKNOWN'))"),("source_detail_2_status","TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(source_detail_2_status IN('VALUE','NA','UNKNOWN'))"))

def columns(db,table): return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
def migration_is_complete(db):
    return all({n for n,_ in STATUS_COLUMNS}<=columns(db,t) for t in ("occurrence","occurrence_revision","occurrence_draft")) and "application_setting" in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
def migrate(database_path=DATABASE_PATH,backup_path=BACKUP_PATH):
    database_path=Path(database_path); backup_path=Path(backup_path) if backup_path is not None else None
    db=sqlite3.connect(database_path)
    try:
        if migration_is_complete(db): return False
        if not all(columns(db,t) for t in ("source","occurrence","occurrence_revision","occurrence_draft","activity_event")): raise RuntimeError("La base no tiene el esquema post-016")
    finally: db.close()
    if backup_path is not None:
        if backup_path.exists(): raise SystemExit(f"El backup ya existe y no se sobrescribirá: {backup_path}")
        shutil.copy2(database_path,backup_path)
    db=sqlite3.connect(database_path); db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute("BEGIN")
        for table in ("occurrence","occurrence_revision","occurrence_draft"):
            existing=columns(db,table)
            for name,definition in STATUS_COLUMNS:
                if name not in existing: db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.execute("CREATE TABLE IF NOT EXISTS application_setting(setting_key TEXT PRIMARY KEY,setting_value TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_by_collaborator_id INTEGER,FOREIGN KEY(updated_by_collaborator_id) REFERENCES collaborator(collaborator_id))")
        db.execute("INSERT OR IGNORE INTO application_setting(setting_key,setting_value) VALUES('analyst_source_creation','1')")
        if not migration_is_complete(db): raise RuntimeError("Migration 017 incompleta")
        if db.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        db.commit()
    except Exception: db.rollback(); raise
    finally: db.close()
    return True
if __name__=="__main__":
    from migration_cli import run_migration_cli
    run_migration_cli(migrate,"017")
