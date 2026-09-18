import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
import database
from access_control import install_access_context
from routes.work_assignments import work_assignments_bp
from work_assignments import assign, assigned_analysts, list_concepts, remove

ROOT = Path(__file__).resolve().parents[1]


class WorkAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'assignments.db'
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        database.crear_esquema(self.db)
        self.db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('Concepto A',), ('Concepto B',), ('Concepto C',)])
        self.db.executemany('INSERT INTO collaborator(display_name) VALUES(?)', [('Ana',), ('Carlos',)])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def assign(self, concepts=(1,), analysts=(1,)):
        return assign(self.db, concepts, analysts, actor_id=2, access_role='master')

    def snapshot(self):
        tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('concept_work_assignment','sqlite_sequence')")]
        return {table: [tuple(r) for r in self.db.execute(f'SELECT * FROM "{table}"')] for table in tables}

    def test_one_two_and_no_analysts(self):
        self.assertEqual(self.assign(), 1)
        self.assertEqual(self.assign(analysts=(2,)), 1)
        rows = assigned_analysts(self.db, [1, 2])
        self.assertEqual([r['display_name'] for r in rows[1]], ['Ana', 'Carlos'])
        self.assertEqual(rows[2], [])
        self.assertEqual(rows[1][0]['created_by_name_snapshot'], 'Carlos')

    def test_idempotence_constraint_removal_and_reassignment(self):
        self.assign()
        self.assertEqual(self.assign(), 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO concept_work_assignment(concept_id,analyst_id,analyst_name_snapshot,created_access_role) VALUES(1,1,'Ana','master')")
        self.db.rollback()
        self.assertEqual(remove(self.db, 1, actor_id=2, access_role='master'), 1)
        self.assertEqual(remove(self.db, 1, access_role='master'), 0)
        self.assertEqual(assigned_analysts(self.db, [1])[1], [])
        self.assertEqual(self.assign(), 1)
        rows = self.db.execute('SELECT * FROM concept_work_assignment ORDER BY work_assignment_id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['removed_by_name_snapshot'], 'Carlos')
        self.assertIsNotNone(rows[0]['removed_at'])

    def test_bulk_filters_pagination_and_no_linguistic_writes(self):
        before = self.snapshot()
        self.assign((1, 2), (1, 2))
        self.assertEqual(list_concepts(self.db, status='assigned')['total'], 2)
        self.assertEqual(list_concepts(self.db, status='unassigned')['total'], 1)
        self.assertEqual(list_concepts(self.db, analyst_id=2)['total'], 2)
        self.assertEqual(list_concepts(self.db, search='Concepto B')['total'], 1)
        self.assertEqual(list_concepts(self.db, search='%')['total'], 0)
        data = list_concepts(self.db, per_page=1, page=2)
        self.assertEqual(data['concepts'][0]['concept_id'], 2)
        self.assertEqual(data['pages'], 3)
        remove(self.db, 1, access_role='master')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_batch_and_late_failure_roll_back(self):
        with self.assertRaises(ValueError): self.assign((1, 999))
        with self.assertRaises(ValueError): self.assign(analysts=(1, 999))
        self.db.execute("UPDATE collaborator SET active=0 WHERE collaborator_id=2")
        self.db.commit()
        with self.assertRaises(ValueError): self.assign(analysts=(1, 2))
        self.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON concept_work_assignment WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.assign((1, 2))
        self.assertEqual(self.db.execute('SELECT count(*) FROM concept_work_assignment').fetchone()[0], 0)

    def test_service_permissions(self):
        for role in ('analyst', 'reviewer', None):
            with self.assertRaises(PermissionError): assign(self.db, [1], [1], access_role=role)
            with self.assertRaises(PermissionError): remove(self.db, 1, access_role=role)

    def test_routes_permissions_csrf_bulk_and_remove(self):
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='local-test-only')
        app.register_blueprint(work_assignments_bp)
        install_access_context(app)
        with patch.object(database, 'BASE_DATOS', self.path), patch.dict(os.environ, {
            'LESICO_MASTER_ROUTE': 'admin-test', 'LESICO_REVIEWER_ROUTE': 'review-test', 'LESICO_ANALYST_ROUTE': 'analyst-test'}):
            client = app.test_client()
            for prefix in ('', '/review-test', '/analyst-test'):
                for method in (client.get, client.post):
                    self.assertEqual(method(prefix + '/administracion/asignaciones').status_code, 404)
            url = '/admin-test/administracion/asignaciones'
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Sin asignar', response.get_data(as_text=True))
            self.assertEqual(client.post(url, data={'action': 'assign'}).status_code, 400)
            with client.session_transaction() as session:
                token = session['work_assignment_csrf']
            payload = dict(action='assign', concept_ids=['1', '2'], analyst_ids=['1', '2'], csrf_token=token, collaborator_id='2')
            response = client.post(url, data=payload, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Asignaciones nuevas: 4.', response.get_data(as_text=True))
            self.assertEqual(client.post(url, data=dict(action='remove', assignment_id='1', csrf_token=token)).status_code, 302)
            self.assertEqual(client.post(url, data=dict(action='assign', csrf_token=token)).status_code, 400)
            self.assertEqual(client.get(url + '?page=invalid').status_code, 400)
            self.assertEqual(client.get(url + '?status=unassigned').status_code, 200)

    def test_migration_preview_apply_idempotence_and_preservation(self):
        spec = importlib.util.spec_from_file_location('migration025', ROOT / 'migrations/025_concept_work_assignment.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.db.execute('DROP TABLE concept_work_assignment')
        self.db.commit()
        before = self.snapshot()
        self.assertEqual(module.migrate(self.path)['mode'], 'dry-run')
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='concept_work_assignment'").fetchone())
        result = module.migrate(self.path, apply=True)
        self.assertTrue(Path(result['backup']).exists())
        self.assertEqual(module.migrate(self.path, apply=True)['changes'], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_NAME': 'pruebas'}):
            with self.assertRaises(ValueError): module.migrate(self.path)

    def test_demo_and_real_app_integration(self):
        spec = importlib.util.spec_from_file_location('assignment_demo', ROOT / 'scripts/demo_work_assignments.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = Path(self.tmp.name)
        module.main()
        demo = module.ROOT / 'work_assignment_demo.db'
        before = demo.read_bytes()
        with self.assertRaises(FileExistsError): module.main()
        self.assertEqual(demo.read_bytes(), before)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('LESICO_', 'RAILWAY_'))}
        env.update(LESICO_ENV='development', LESICO_DATABASE_PATH=str(demo),
                   LESICO_SECRET_KEY='test-demo-only', LESICO_MASTER_ROUTE='admin-local')
        code = """from app import app
response = app.test_client().get('/admin-local/administracion/asignaciones')
assert response.status_code == 200, response.status_code
html = response.get_data(as_text=True)
assert all(text in html for text in ('Concepto A', 'Concepto B', 'Concepto C', 'Ana', 'Carlos', 'Sin asignar'))
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
