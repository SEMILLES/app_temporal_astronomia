"""Explicit, schema-only 19A bootstrap. No application/runtime imports."""
import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

# Standalone CLI needs only this script and classification_schema.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from classification_schema import TABLES, install


PRE19A_TABLES = frozenset('''source concept occurrence alternative assignment
    alternative_relation occurrence_grammar source_revision source_systematization
    occurrence_revision media_asset occurrence_media alternative_media submission
    concept_proposal submission_concept_resolution submission_lexical_decision
    occurrence_draft occurrence_concept_reference alternative_submission
    alternative_submission_relation grammar_submission renumber_event renumber_change
    alternative_submission_morphology alternative_submission_component
    alternative_morphology alternative_component collaborator activity_event
    conflict conflict_subject conflict_resolution_attempt catalog_publication
    publication_open_conflict application_setting'''.split())
PRE19A_COLUMNS = {
    'concept': 'concept_id preferred_label semantic_field_1 semantic_field_2 knowledge_area_1 knowledge_area_2 created_at',
    'source': 'source_id source_name retired_at',
    'occurrence': 'occurrence_id source_id original_gloss',
    'alternative': 'alternative_id concept_id working_label retired_at',
    'assignment': 'assignment_id occurrence_id alternative_id is_current',
    'collaborator': 'collaborator_id display_name active',
    'submission': 'submission_id occurrence_id submission_type status resolution',
    'alternative_submission': 'submission_id reference_concept_id reference_concept_proposal_id',
    'concept_proposal': 'concept_proposal_id proposed_label status resolved_concept_id',
    'occurrence_concept_reference': 'occurrence_concept_reference_id occurrence_id concept_id concept_proposal_id is_current',
    'submission_lexical_decision': 'submission_id concept_resolution_id decision_action resolved_alternative_id',
    'catalog_publication': 'publication_id version_number snapshot_json snapshot_sha256',
}
DECISION_COLUMN = 'classification_decision_json'

# Contract for migration 020; no import of the runtime workflow module.
RESOLUTION_SCHEMA = """
CREATE TABLE submission_concept_resolution (
    submission_concept_resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submission(submission_id),
    concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
    resolution_action TEXT NOT NULL CHECK(resolution_action IN
        ('CONFIRM_REFERENCE','USE_EXISTING','CREATE_NEW')),
    resolution_note TEXT,
    collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
    collaborator_name_snapshot TEXT,
    access_role TEXT NOT NULL CHECK(access_role IN ('reviewer','master')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    supersedes_submission_concept_resolution_id INTEGER REFERENCES
        submission_concept_resolution(submission_concept_resolution_id),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1))
);
CREATE UNIQUE INDEX one_current_submission_concept_resolution
    ON submission_concept_resolution(submission_id) WHERE is_current=1;
CREATE INDEX idx_submission_concept_resolution_submission
    ON submission_concept_resolution(submission_id);
"""


class MigrationError(RuntimeError):
    pass


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def columns(db, table):
    return {r[1]: tuple(r[1:]) for r in db.execute(f'PRAGMA table_info({quote(table)})')}


def normalize_sql(sql):
    return re.sub(r'\s+', '', (sql or '').replace('IF NOT EXISTS', '')).lower().rstrip(';')


def reference_schema():
    db = sqlite3.connect(':memory:')
    try:
        db.executescript('''CREATE TABLE concept(concept_id INTEGER PRIMARY KEY);
            CREATE TABLE collaborator(collaborator_id INTEGER PRIMARY KEY);
            CREATE TABLE submission(submission_id INTEGER PRIMARY KEY,submission_type TEXT,status TEXT);
        ''' + RESOLUTION_SCHEMA)
        install(db)
        return db
    except Exception:
        db.close()
        raise


def check_integrity(db):
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise MigrationError('foreign_key_check falló.')
    integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
    if integrity != ['ok']:
        raise MigrationError('integrity_check falló: ' + '; '.join(integrity[:5]))


def check_pre19a(db, reference):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not PRE19A_TABLES <= names:
        raise MigrationError('Esquema pre-19A incompatible; faltan tablas: ' + ', '.join(sorted(PRE19A_TABLES - names)))
    for table, required in PRE19A_COLUMNS.items():
        if not set(required.split()) <= columns(db, table).keys():
            raise MigrationError(f'Esquema pre-19A incompatible: {table}.')
    table = 'submission_concept_resolution'
    expected = columns(reference, table)
    expected.pop(DECISION_COLUMN)
    actual = columns(db, table)
    actual.pop(DECISION_COLUMN, None)
    if actual != expected:
        raise MigrationError('submission_concept_resolution no corresponde al esquema 020 esperado.')
    actual_sql = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
    expected_sql = (reference.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
                    if DECISION_COLUMN in columns(db, table) else RESOLUTION_SCHEMA.split(';')[0])
    if normalize_sql(actual_sql) != normalize_sql(expected_sql):
        raise MigrationError('Restricciones incompatibles en submission_concept_resolution.')
    if list(db.execute(f'PRAGMA foreign_key_list({table})')) != list(reference.execute(f'PRAGMA foreign_key_list({table})')):
        raise MigrationError('Foreign keys incompatibles en submission_concept_resolution.')
    for name in ('one_current_submission_concept_resolution', 'idx_submission_concept_resolution_submission'):
        actual = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        expected = reference.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if not actual or normalize_sql(actual[0]) != normalize_sql(expected[0]):
            raise MigrationError(f'Índice pre-19A incompatible: {name}.')


def check_installed(db, reference, *, initial=False):
    """Validate all 19A DDL, including CHECKs, unique indexes and triggers."""
    for table in (*TABLES, 'submission_concept_resolution'):
        if columns(db, table) != columns(reference, table):
            raise MigrationError(f'Estructura 19A incompatible: {table}.')
    placeholders = ','.join('?' for _ in TABLES)
    objects = reference.execute(f'''SELECT type,name,sql FROM sqlite_master
        WHERE tbl_name IN ({placeholders}) OR name='immutable_classification_decision' ''', TABLES)
    for kind, name, sql in objects:
        actual = db.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if actual is None or actual[0] != kind or normalize_sql(actual[1]) != normalize_sql(sql):
            raise MigrationError(f'Objeto 19A ausente o incompatible: {kind} {name}.')
    check_seeds(db, reference, initial=initial)


def check_seeds(db, reference, *, initial):
    # Initial seeds must be exact. Later invocations must not reset Master edits.
    queries = (
        ('collection', 'SELECT code,name,name_key,active FROM collection ORDER BY code'),
        ('systems', '''SELECT s.code,s.name,c.code,s.active FROM classification_system s
            LEFT JOIN collection c USING(collection_id) ORDER BY s.code'''),
        ('categories', '''SELECT s.code,c.code,c.name,c.name_key,c.active,c.display_order
            FROM classification_category c JOIN classification_system s USING(system_id) ORDER BY s.code,c.code'''),
    )
    for kind, query in queries:
        expected, actual = list(reference.execute(query)), list(db.execute(query))
        if initial:
            valid = actual == expected
        else:
            key = (lambda r: (r[0],r[1])) if kind == 'categories' else (
                (lambda r: (r[0],r[2])) if kind == 'systems' else (lambda r: r[0]))
            valid = {key(r) for r in expected} <= {key(r) for r in actual}
        if not valid:
            raise MigrationError(f'Seeds 19A inválidos: {kind}.')


def existing_rows(db, schema):
    """Counts and content hashes using only columns that existed before 19A."""
    result = {}
    for table, names in schema.items():
        count, checksum = 0, 0
        for row in db.execute(f'SELECT {",".join(quote(n) for n in names)} FROM {quote(table)}'):
            count += 1
            checksum = (checksum + int.from_bytes(hashlib.sha256(repr(tuple(row)).encode('utf-8')).digest(), 'big')) % (1 << 256)
        result[table] = (count, checksum)
    return result


def consistent_backup(source_path, backup_path):
    # Caller holds BEGIN IMMEDIATE without writes. A separate reader backs up
    # the committed state while other writers remain excluded (including WAL).
    with backup_path.open('xb'):
        pass
    with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(backup_path.as_uri() + '?mode=rw', uri=True)) as backup:
            source.backup(backup)
            check_integrity(backup)


def migrate(database_path, *, apply=False, backup_path=None, test_only=False, report=print):
    if not database_path:
        raise MigrationError('Se requiere una ruta explícita.')
    path = Path(database_path).expanduser().resolve()
    temporary_test = test_only and Path(tempfile.gettempdir()).resolve() in path.parents
    if not apply and not temporary_test:
        raise MigrationError('Migración rechazada: se requiere autorización explícita --apply.')
    if not path.is_file():
        raise MigrationError(f'La base no existe: {path}')
    backup = Path(backup_path).expanduser().resolve() if backup_path else path.with_name(path.stem + '.pre_migration_022' + path.suffix)
    if backup == path:
        raise MigrationError('El backup debe ser un archivo diferente a la base.')
    backup_created = False
    with closing(reference_schema()) as reference:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            try:
                db.execute('BEGIN IMMEDIATE')
                check_pre19a(db, reference)
                check_integrity(db)
                names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                present = set(TABLES) & names
                has_column = DECISION_COLUMN in columns(db, 'submission_concept_resolution')
                if present or has_column:
                    if present != set(TABLES) or not has_column:
                        raise MigrationError('Instalación 19A parcial; no se modificó la base.')
                    check_installed(db, reference)
                    db.rollback()
                    report('19A ya está instalada y validada; no hay cambios ni nuevo backup.')
                    return False
                old_schema = {table: list(columns(db, table)) for table in names if not table.startswith('sqlite_')}
                before = existing_rows(db, old_schema)
                try:
                    consistent_backup(path, backup)
                except Exception as error:
                    raise MigrationError(f'Backup fallido ({backup}); no se aplicó la migración: {error}') from error
                backup_created = True
                report(f'Backup SQLite consistente: {backup}')
                install(db)
                check_installed(db, reference, initial=True)
                check_integrity(db)
                if existing_rows(db, old_schema) != before:
                    raise MigrationError('Cambió el conteo o contenido de filas preexistentes.')
                for table in TABLES[3:]:
                    if db.execute(f'SELECT count(*) FROM {quote(table)}').fetchone()[0]:
                        raise MigrationError(f'La migración creó datos de trabajo inesperados: {table}.')
                db.commit()
                report('Migración 022 completada: esquema, seeds e integridad validados; datos preexistentes intactos.')
                return True
            except Exception as error:
                db.rollback()
                detail = f' Backup conservado: {backup}.' if backup_created else ''
                raise MigrationError(f'Migración 022 no aplicada; rollback.{detail} {error}') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description='Migración explícita de esquema 19A; no arranca la aplicación.')
    parser.add_argument('--database', required=True, help='Ruta explícita de la SQLite existente')
    parser.add_argument('--apply', action='store_true', help='Autoriza aplicar la migración a esa ruta')
    parser.add_argument('--backup', help='Ruta nueva del backup (por defecto: <base>.pre_migration_022<extensión>)')
    args = parser.parse_args(argv)
    try:
        migrate(args.database, apply=args.apply, backup_path=args.backup)
    except (OSError, sqlite3.Error, MigrationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
