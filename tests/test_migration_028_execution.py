"""Exercise 028 entry points on synthetic local files with simulated environments."""
import importlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

migration = importlib.import_module('migrations.028_concept_reference_origin')
ROOT = Path(__file__).resolve().parents[1]
AUTH = 'LESICO_MIGRATION_028_ALLOW_PRUEBAS'
PRUEBAS = {
    'LESICO_ENV': 'production',
    'RAILWAY_ENVIRONMENT_NAME': 'pruebas',
    'RAILWAY_ENVIRONMENT_ID': 'dc6a07af-8842-44c8-a072-a0d3e10ae203',
    AUTH: '1',
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('RAILWAY_') or key in ('LESICO_ENV', AUTH):
            monkeypatch.delenv(key)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / 'synthetic.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE occurrence_concept_reference (
            occurrence_id INTEGER PRIMARY KEY, concept_id INTEGER,
            concept_proposal_id INTEGER, is_current INTEGER)''')
        db.execute('INSERT INTO occurrence_concept_reference VALUES(1,NULL,7,1)')
    return path


def configure(monkeypatch, env):
    for key, value in env.items():
        if value is not None:
            monkeypatch.setenv(key, value)


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return '\n'.join(db.iterdump())


@pytest.mark.parametrize('env', [
    {}, PRUEBAS, {**PRUEBAS, 'LESICO_ENV': None},
    {**PRUEBAS, 'LESICO_ENV': 'development'},
], ids=['local', 'railway-pruebas-production-profile', 'railway-no-profile', 'railway-development-profile'])
def test_preview_apply_backup_integrity_and_idempotence(database_path, monkeypatch, env):
    configure(monkeypatch, env)
    before = database_path.read_bytes()
    original_data = snapshot(database_path)
    preview = migration.migrate(database_path)
    assert preview['mode'] == 'dry-run' and preview['changes'] == 1
    assert database_path.read_bytes() == before
    backup = database_path.with_name('before-028.db')
    result = migration.migrate(database_path, apply=True, backup_path=backup)
    assert result['changes'] == 1 and result['mode'] == 'apply'
    assert snapshot(backup) == original_data
    with sqlite3.connect(database_path) as db:
        assert db.execute('SELECT * FROM occurrence_concept_reference').fetchone() == (1, None, 7, 1, None)
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = database_path.read_bytes()
    assert migration.migrate(database_path, apply=True)['changes'] == 0
    assert database_path.read_bytes() == after


@pytest.mark.parametrize('env', [
    {'RAILWAY_SERVICE_ID': 'synthetic-service'},
    {**PRUEBAS, AUTH: None},
    {**PRUEBAS, AUTH: 'true'},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_NAME': None},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_ID': None},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_NAME': 'production'},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_ID': 'not-pruebas'},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_NAME': 'production', 'RAILWAY_ENVIRONMENT_ID': 'production-id'},
    {**PRUEBAS, 'RAILWAY_ENVIRONMENT_NAME': '', 'RAILWAY_ENVIRONMENT_ID': ''},
    {'LESICO_ENV': 'production', AUTH: '1'},
    {'LESICO_ENV': 'prod', AUTH: '1'},
], ids=['railway-only', 'no-authorization', 'generic-authorization',
        'no-railway-name', 'no-railway-id', 'production-name',
        'wrong-railway-id', 'production-even-authorized', 'empty-railway-identity',
        'local-production-even-authorized', 'production-alias'])
def test_unauthorized_execution_does_not_mutate_or_backup(database_path, monkeypatch, env):
    configure(monkeypatch, env)
    before = database_path.read_bytes()
    backup = database_path.with_name('blocked-backup.db')
    with pytest.raises(ValueError):
        migration.migrate(database_path, apply=True, backup_path=backup)
    assert database_path.read_bytes() == before
    assert not backup.exists()
    # The operation itself is protected, even when called without the runner.
    with sqlite3.connect(database_path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN')
        with pytest.raises(ValueError):
            migration.migration(db)


def test_backup_failure_leaves_schema_unchanged(database_path, monkeypatch):
    configure(monkeypatch, PRUEBAS)
    before = database_path.read_bytes()
    backup = database_path.with_name('occupied.db')
    backup.write_bytes(b'do not overwrite')
    with pytest.raises(FileExistsError):
        migration.migrate(database_path, apply=True, backup_path=backup)
    assert database_path.read_bytes() == before
    assert backup.read_bytes() == b'do not overwrite'


@pytest.mark.parametrize('authorized', [False, True])
def test_actual_cli_requires_explicit_authorization(database_path, monkeypatch, authorized):
    configure(monkeypatch, {**PRUEBAS, AUTH: '1' if authorized else None})
    before = database_path.read_bytes()
    result = subprocess.run([
        sys.executable, '-B', str(ROOT / 'migrations/028_concept_reference_origin.py'),
        '--database', str(database_path), '--apply',
        '--backup', str(database_path.with_name('cli-backup.db')),
    ], capture_output=True, text=True, check=False, cwd=ROOT)
    report = json.loads(result.stdout)
    assert result.returncode == (0 if authorized else 1), result.stderr
    if authorized:
        assert report['changes'] == 1 and report['mode'] == 'apply'
    else:
        assert 'error' in report
        assert database_path.read_bytes() == before
