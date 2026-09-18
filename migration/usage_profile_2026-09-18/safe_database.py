"""Shared explicit, backed-up, transactional maintenance entry point."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def check_integrity(db):
    if [r[0] for r in db.execute('PRAGMA integrity_check')] != ['ok']:
        raise ValueError('integrity_check falló')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('foreign_key_check falló')


def ledger_digest(db):
    result = {}
    for table in ('reconciliation_run', 'reconciliation_operation'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            digest = hashlib.sha256()
            for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                digest.update(repr(tuple(row)).encode('utf-8'))
            result[table] = digest.hexdigest()
    return result


def run(database, operation, *, apply=False, backup=None):
    path = Path(database).expanduser().resolve(strict=True)
    # Defense in depth for Railway; local copies are explicitly selected by path.
    import os
    environment = os.environ.get('RAILWAY_ENVIRONMENT_NAME')
    environment_id = os.environ.get('RAILWAY_ENVIRONMENT_ID')
    if environment and environment != 'pruebas' or environment_id and environment_id != 'dc6a07af-8842-44c8-a072-a0d3e10ae203':
        raise ValueError('Mantenimiento autorizado únicamente en Railway pruebas')
    with closing(sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)) as source:
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA foreign_keys=ON')
        if not apply:
            with closing(sqlite3.connect(':memory:')) as preview:
                source.backup(preview)
                preview.row_factory = sqlite3.Row
                preview.execute('PRAGMA foreign_keys=ON')
                check_integrity(preview)
                preview.execute('BEGIN IMMEDIATE')
                report = operation(preview)
                check_integrity(preview)
                preview.rollback()
                return dict(report, mode='dry-run')
        source.execute('BEGIN IMMEDIATE')
        try:
            check_integrity(source)
            before = ledger_digest(source)
            # Preview under the write lock so bad preconditions cause no writes or backup.
            with closing(sqlite3.connect(':memory:')) as preview:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
                    reader.backup(preview)
                preview.row_factory = sqlite3.Row
                preview.execute('PRAGMA foreign_keys=ON')
                preview.execute('BEGIN IMMEDIATE')
                planned = operation(preview)
                check_integrity(preview)
            if not planned['changes']:
                source.rollback()
                return dict(planned, mode='apply', backup=None)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            destination = Path(backup).resolve() if backup else path.with_name(path.stem + '.' + stamp + '.backup.db')
            with destination.open('xb'):
                pass
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
                with closing(sqlite3.connect(destination)) as target:
                    reader.backup(target)
                    check_integrity(target)
            report = operation(source)
            check_integrity(source)
            if ledger_digest(source) != before:
                raise ValueError('El ledger de reconciliación cambió')
            source.commit()
            return dict(report, mode='apply', backup=str(destination), ledger=before)
        except Exception:
            source.rollback()
            raise


def cli(operation, description):
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--report')
    args = parser.parse_args()
    try:
        report = run(args.database, operation, apply=args.apply, backup=args.backup)
    except Exception as error:
        report = {'error': str(error)}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output + '\n', encoding='utf-8')
    print(output)
    return int('error' in report)
