import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from urllib.parse import urlsplit
from unittest.mock import patch

from flask import Flask
import database
from access_control import install_access_context
from routes.work_assignments import work_assignments_bp
from work_assignments import assign, assigned_analysts, list_concepts, my_work, remove

ROOT = Path(__file__).resolve().parents[1]


class InterfaceText(HTMLParser):
    """Include disclosure/option text and accessible labels, not technical attributes."""
    def __init__(self, html):
        super().__init__()
        self.text = []
        self.suppressed = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.suppressed = True
        if not self.suppressed:
            self.text.extend(value for key, value in attrs
                             if key in ('aria-label', 'title', 'placeholder', 'alt') and value)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.suppressed = False

    def handle_data(self, data):
        if not self.suppressed:
            self.text.append(data)


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

    def pending_concepts(self):
        self.db.executemany(
            "INSERT INTO alternative(concept_id,working_label) VALUES(?,'1a')",
            [(1,), (2,), (3,)])
        self.db.commit()

    def assign(self, concepts=(1,), analysts=(1,)):
        return assign(self.db, concepts, analysts, actor_id=2, access_role='master')

    def snapshot(self):
        tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('concept_work_assignment','sqlite_sequence')")]
        return {table: [tuple(r) for r in self.db.execute(f'SELECT * FROM "{table}"')] for table in tables}

    def test_one_two_and_no_analysts(self):
        self.pending_concepts()
        self.assertEqual(self.assign(), 1)
        self.assertEqual(self.assign(analysts=(2,)), 1)
        rows = assigned_analysts(self.db, [1, 2])
        self.assertEqual([r['display_name'] for r in rows[1]], ['Ana', 'Carlos'])
        self.assertEqual(rows[2], [])
        self.assertEqual(rows[1][0]['created_by_name_snapshot'], 'Carlos')

    def test_idempotence_constraint_removal_and_reassignment(self):
        self.pending_concepts()
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
        self.pending_concepts()
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
        self.pending_concepts()
        with self.assertRaises(ValueError): self.assign((1, 999))
        with self.assertRaises(ValueError): self.assign(analysts=(1, 999))
        self.db.execute("UPDATE collaborator SET active=0 WHERE collaborator_id=2")
        self.db.commit()
        with self.assertRaises(ValueError): self.assign(analysts=(1, 2))
        self.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON concept_work_assignment WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.assign((1, 2))
        self.assertEqual(self.db.execute('SELECT count(*) FROM concept_work_assignment').fetchone()[0], 0)

    def test_service_permissions(self):
        for role in ('analyst', None):
            with self.assertRaises(PermissionError): assign(self.db, [1], [1], access_role=role)
            with self.assertRaises(PermissionError): remove(self.db, 1, access_role=role)

    def test_reviewer_and_master_mutations_preserve_real_role(self):
        self.pending_concepts()
        for role in ('reviewer', 'master'):
            self.assertEqual(assign(self.db, [1], [1], actor_id=2, access_role=role), 1)
            row = self.db.execute('SELECT * FROM concept_work_assignment WHERE active=1').fetchone()
            self.assertEqual(row['created_access_role'], role)
            remove(self.db, row['work_assignment_id'], actor_id=2, access_role=role)
            saved = self.db.execute('SELECT * FROM concept_work_assignment WHERE work_assignment_id=?', (row['work_assignment_id'],)).fetchone()
            self.assertEqual(saved['removed_access_role'], role)

    def test_personal_work_isolation_shared_concept_and_removal(self):
        self.pending_concepts()
        before = self.snapshot()
        self.assign((1,), (1, 2))
        self.assign((2,), (2,))
        ids = lambda collaborator: [r['concept_id'] for r in my_work(self.db, collaborator)['concepts']]
        self.assertEqual(ids(1), [1])
        self.assertEqual(ids(2), [1, 2])
        remove(self.db, 1, access_role='master')
        self.assertEqual(ids(1), [])
        self.assertEqual(ids(2), [1, 2])
        self.assertEqual(self.db.execute('SELECT active FROM concept_work_assignment WHERE work_assignment_id=1').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM alternative').fetchone()[0], 3)
        self.assertEqual(self.snapshot(), before)

    def test_personal_work_empty_invalid_search_and_pagination(self):
        self.pending_concepts()
        self.assertEqual(my_work(self.db, 1)['total'], 0)
        self.assign((1, 2), (1,))
        for identifier in (None, '', 'bad', 999):
            self.assertEqual(my_work(self.db, identifier)['concepts'], [])
        self.assertEqual(my_work(self.db, 1, search='Concepto B')['total'], 1)
        self.assertEqual(my_work(self.db, 1, search='2')['concepts'][0]['concept_id'], 2)
        self.assertEqual(my_work(self.db, 2, search='2')['total'], 0)
        self.assertEqual(my_work(self.db, 1, search='%')['total'], 0)
        page = my_work(self.db, 1, page=2, per_page=1)
        self.assertEqual(page['concepts'][0]['concept_id'], 2)
        self.assertEqual(page['pages'], 2)
        self.db.execute('UPDATE collaborator SET active=0 WHERE collaborator_id=1')
        self.db.commit()
        self.assertEqual(my_work(self.db, 1)['total'], 0)

    def test_browser_declared_identity_and_assignment_flow(self):
        from playwright.sync_api import sync_playwright, expect
        self.db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)',
                            [(1, '1b'), (2, '1a')])
        self.db.commit()
        app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
        app.config.update(TESTING=True, SECRET_KEY='local-browser-test')
        app.register_blueprint(work_assignments_bp)
        # The real destination is exercised in test_demo_and_real_app_integration.
        from routes.alternatives import alternatives_bp
        app.register_blueprint(alternatives_bp)
        from routes.alternative_changes import alternative_changes_bp
        app.register_blueprint(alternative_changes_bp)
        install_access_context(app)
        with patch.object(database, 'BASE_DATOS', self.path), patch.dict(os.environ, {
            'LESICO_MASTER_ROUTE': 'admin-test', 'LESICO_ANALYST_ROUTE': 'analyst-test'}):
            client = app.test_client()

            def serve(route):
                request = route.request
                url = urlsplit(request.url)
                response = client.open(url.path + ('?' + url.query if url.query else ''),
                                       method=request.method, data=request.post_data,
                                       content_type=request.headers.get('content-type'), follow_redirects=True)
                try:
                    route.fulfill(status=response.status_code, body=response.data,
                                  content_type=response.content_type)
                finally:
                    response.close()

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.route('**/*', serve)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto('http://local.test/admin-test/administracion/asignaciones')
                    expect(page.locator('#work-summary')).not_to_be_visible()
                    self.assertIsNone(page.locator('#work-summary-disclosure').get_attribute('open'))
                    self.assertTrue(page.locator('#work-filters').evaluate(
                        "el => el.previousElementSibling.tagName === 'H1'"))
                    self.assertTrue(page.locator('#work-summary-disclosure').evaluate(
                        "el => el.previousElementSibling.id === 'work-totals' && el.nextElementSibling.id === 'bulk-assignment'"))
                    page.get_by_text('Ver resumen por concepto', exact=True).click()
                    expect(page.locator('#work-summary')).to_be_visible()
                    expect(page.locator('#work-summary th')).to_have_text([
                        'Concepto', 'Morfología', 'Relación fonológica', 'Gramática', 'Asignación a alternativa', 'Total'])
                    page.get_by_text('Ver resumen por concepto', exact=True).click()
                    expect(page.locator('#work-totals')).to_contain_text('Pendientes generales: 3 tareas · 2 conceptos.')
                    page.locator('select[name=concept_id]').select_option('1')
                    page.get_by_role('button', name='Filtrar', exact=True).click()
                    expect(page.locator('#work-detail')).to_contain_text('Concepto A')
                    expect(page.locator('#work-detail')).not_to_contain_text('Concepto B')
                    page.locator('select[name=work_type]').select_option('morphology')
                    page.get_by_role('button', name='Filtrar', exact=True).click()
                    expect(page.locator('select[name=concept_id]')).to_have_value('1')
                    expect(page.locator('#work-totals')).to_contain_text('Pendientes generales: 2 tareas · 1 conceptos.')
                    expect(page.locator('#work-totals')).to_contain_text('Trabajo encontrado: 1 tareas · 1 conceptos')
                    expect(page.locator('#work-detail')).not_to_contain_text('Resolver relación')
                    expect(page.locator('#work-detail')).to_contain_text('Resolver morfología')
                    page.locator('select[name=concept_id]').select_option('')
                    page.get_by_role('button', name='Filtrar', exact=True).click()
                    expect(page.locator('#work-detail')).to_contain_text('Concepto B')
                    expect(page.locator('select[name=work_type]')).to_have_value('morphology')
                    page.get_by_role('link', name='Limpiar', exact=True).click()
                    page.locator('#lesico-collaborator').select_option('1')
                    page.locator('input[name=concept_ids][value="1"]').check()
                    page.locator('input[name=analyst_ids][value="1"]').check()
                    page.locator('input[name=analyst_ids][value="2"]').check()
                    page.get_by_role('button', name='Asignar seleccionados').click()
                    expect(page.get_by_role('status')).to_have_text('Asignaciones nuevas: 2.')
                    page.goto('http://local.test/analyst-test/mi-trabajo')
                    page.get_by_role('link', name='Mi trabajo', exact=True).click()
                    expect(page.locator('#my-work-content')).to_be_visible()
                    expect(page.locator('tbody')).to_contain_text('Concepto A')
                    expect(page.locator('tbody')).not_to_contain_text('Concepto B')
                    page.locator('#lesico-collaborator').select_option('2')
                    expect(page).to_have_url('http://local.test/analyst-test/mi-trabajo?collaborator_id=2')
                    expect(page.locator('#my-work-content')).to_contain_text('Carlos')
                    page.goto('http://local.test/admin-test/administracion/asignaciones')
                    page.get_by_role('button', name='Retirar a Ana del concepto 1', exact=True).click()
                    page.locator('#lesico-collaborator').select_option('1')
                    page.goto('http://local.test/analyst-test/mi-trabajo')
                    page.get_by_role('link', name='Mi trabajo', exact=True).click()
                    expect(page.locator('#my-work-content')).to_contain_text('No tienes conceptos asignados')
                    page.locator('#lesico-collaborator').select_option('2')
                    expect(page.locator('tbody')).to_contain_text('Concepto A')
                    # Direct entry under the analyst prefix restores the same selection.
                    page.goto('http://local.test/analyst-test/mi-trabajo')
                    expect(page).to_have_url('http://local.test/analyst-test/mi-trabajo?collaborator_id=2')
                    expect(page.locator('tbody')).to_contain_text('Concepto A')
                    page.locator('#lesico-collaborator').select_option('')
                    expect(page.locator('#my-work-content')).to_contain_text('Selecciona un colaborador')
                    expect(page.locator('tbody')).to_have_count(0)
                    self.assertEqual(errors, [])
                    no_js = browser.new_page(java_script_enabled=False)
                    no_js.route('**/*', serve)
                    no_js.goto('http://local.test/admin-test/administracion/asignaciones')
                    no_js.locator('select[name=concept_id]').select_option('1')
                    no_js.locator('select[name=work_type]').select_option('relations')
                    no_js.get_by_role('button', name='Filtrar', exact=True).click()
                    expect(no_js.locator('#work-detail')).to_contain_text('Concepto A')
                    expect(no_js.locator('#work-detail')).not_to_contain_text('Concepto B')
                    expect(no_js.locator('#work-detail')).to_contain_text('Resolver relación')
                    expect(no_js.locator('#work-detail')).not_to_contain_text('Resolver morfología')
                    # Exercise real server-side pagination with the same filters.
                    for number in range(52):
                        identifier = self.db.execute('INSERT INTO concept(preferred_label) VALUES(?)',
                                                     (f'Lote {number:02}',)).lastrowid
                        self.db.execute('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)',
                                        (identifier, '1a'))
                    self.db.commit()
                    no_js.goto('http://local.test/admin-test/administracion/asignaciones?search=Lote&work_type=morphology&status=unassigned')
                    expect(no_js.locator('#work-detail tbody tr')).to_have_count(50)
                    expect(no_js.locator('#work-summary')).not_to_be_visible()
                    no_js.get_by_role('link', name='Siguiente', exact=True).click()
                    expect(no_js.locator('#work-detail tbody tr')).to_have_count(2)
                    expect(no_js.locator('#work-detail')).to_contain_text('Lote 50')
                    expect(no_js.locator('select[name=work_type]')).to_have_value('morphology')
                    expect(no_js.locator('input[name=search][type=search]')).to_have_value('Lote')
                    expect(no_js.locator('select[name=status]')).to_have_value('unassigned')
                    no_js.get_by_role('link', name='Anterior', exact=True).click()
                    expect(no_js.locator('#work-detail tbody tr')).to_have_count(50)
                    no_js.close()
                finally:
                    browser.close()

    def test_interface_text_is_spanish_including_disclosures_and_errors(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1b')")
        self.db.execute("INSERT INTO source(source_name) VALUES('Fuente de prueba')")
        self.db.executemany('INSERT INTO occurrence(source_id,original_gloss) VALUES(1,?)',
                            [('Evidencia asignada',), ('Evidencia sin asignar',)])
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(2,1)')
        self.db.commit()
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='spanish-test')
        app.register_blueprint(work_assignments_bp)
        from routes.alternatives import alternatives_bp
        from routes.occurrences import occurrences_bp
        app.register_blueprint(alternatives_bp)
        app.register_blueprint(occurrences_bp)
        install_access_context(app)
        with patch.object(database, 'BASE_DATOS', self.path), patch.dict(os.environ, {
            'LESICO_MASTER_ROUTE': 'm', 'LESICO_REVIEWER_ROUTE': 'r'}):
            client = app.test_client()
            for role in ('m', 'r'):
                url = f'/{role}/administracion/asignaciones'
                responses = [client.get(url), client.get(url + '?concept_id=999'),
                             client.get(url + '?work_type=bad'), client.post(url)]
                with client.session_transaction() as session:
                    token = session['work_assignment_csrf']
                responses.append(client.post(url, data={'action': 'assign', 'csrf_token': token}))
                self.assertEqual([response.status_code for response in responses], [200, 200, 400, 400, 400])
                for response in responses:
                    visible = ' '.join(InterfaceText(response.get_data(as_text=True)).text)
                    self.assertNotRegex(visible, r'\b(?:Concepts?|Alternatives?|Assignment|Reviewer|Work|Pending|Morphology|Relations|Grammar|Bad Request)\b')
                html = responses[0].get_data(as_text=True)
                visible = ' '.join(InterfaceText(html).text)
                for text in ('Todos los conceptos', 'Tipo de trabajo', 'Asignación a alternativa',
                             'Alternativa 1b', 'Ver resumen por concepto', 'Gramática: 1'):
                    self.assertIn(text, visible)
                self.assertIn('Pendientes generales: 4 tareas · 1 conceptos.', visible)
                self.assertIn('name="concept_id"', html)
                self.assertIn('value="assignment"', html)

    def test_routes_permissions_csrf_bulk_and_remove(self):
        self.pending_concepts()
        app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='local-test-only')
        app.register_blueprint(work_assignments_bp)
        from routes.alternatives import alternatives_bp
        app.register_blueprint(alternatives_bp)
        from routes.alternative_changes import alternative_changes_bp
        app.register_blueprint(alternative_changes_bp)
        install_access_context(app)
        with patch.object(database, 'BASE_DATOS', self.path), patch.dict(os.environ, {
            'LESICO_MASTER_ROUTE': 'admin-test', 'LESICO_REVIEWER_ROUTE': 'review-test', 'LESICO_ANALYST_ROUTE': 'analyst-test'}):
            client = app.test_client()
            for prefix, personal, administration in (
                ('analyst-test', 200, 404), ('review-test', 404, 200), ('admin-test', 404, 200)
            ):
                self.assertEqual(client.get(f'/{prefix}/mi-trabajo').status_code, personal)
                response = client.get(f'/{prefix}/administracion/asignaciones')
                self.assertEqual(response.status_code, administration)
                visible = client.get(f'/{prefix}/mi-trabajo') if personal == 200 else response
                header = visible.get_data(as_text=True).split('</aside>')[0]
                self.assertEqual('>Mi trabajo</a>' in header, prefix == 'analyst-test')
                self.assertEqual('>Asignación de trabajo</a>' in header, prefix != 'analyst-test')
                for path in ('colaboradores', 'administracion/clasificaciones', 'actualizar-catalogo', 'publicaciones'):
                    self.assertEqual(f'href="/{prefix}/{path}"' in header, prefix == 'admin-test')
            for prefix in ('', '/analyst-test'):
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
            for query in ('concept_id=bad', 'concept_id=-1', 'concept_id=' + '9' * 100,
                          'work_type=invalid', 'analyst_id=' + '9' * 100):
                self.assertEqual(client.get(url + '?' + query).status_code, 400)
                self.assertEqual(client.get('/analyst-test/administracion/asignaciones?' + query).status_code, 404)
            response = client.post(url, data=dict(payload, concept_id='1', work_type='morphology'))
            self.assertEqual(response.status_code, 302)
            self.assertIn('concept_id=1', response.location)
            self.assertIn('work_type=morphology', response.location)
            review_url = '/review-test/administracion/asignaciones'
            response = client.post(review_url, data=dict(action='assign', concept_ids=['3'],
                analyst_ids=['1'], collaborator_id='2', csrf_token=token))
            self.assertEqual(response.status_code, 302)
            row = self.db.execute('SELECT * FROM concept_work_assignment WHERE concept_id=3').fetchone()
            self.assertEqual(row['created_access_role'], 'reviewer')
            response = client.post(review_url, data=dict(action='remove', assignment_id=row['work_assignment_id'],
                collaborator_id='2', csrf_token=token))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.db.execute('SELECT removed_access_role FROM concept_work_assignment WHERE concept_id=3').fetchone()[0], 'reviewer')

    def test_central_endpoint_policy_without_route_decorators(self):
        from flask import Response
        app = Flask(__name__)
        for path, endpoint in (('/mi-trabajo', 'work_assignments.personal_work'),
                               ('/administracion/asignaciones', 'work_assignments.administration')):
            app.add_url_rule(path, endpoint, lambda: Response('ok', mimetype='text/plain'))
        install_access_context(app)
        with patch.dict(os.environ, {'LESICO_ANALYST_ROUTE': 'a', 'LESICO_REVIEWER_ROUTE': 'r',
                                     'LESICO_MASTER_ROUTE': 'm'}):
            client = app.test_client()
            for prefix, personal, administration in (('a', 200, 404), ('r', 404, 200), ('m', 404, 200)):
                self.assertEqual(client.get(f'/{prefix}/mi-trabajo').status_code, personal)
                self.assertEqual(client.get(f'/{prefix}/administracion/asignaciones').status_code, administration)

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
                   LESICO_SECRET_KEY='test-demo-only', LESICO_MASTER_ROUTE='admin-local',
                   LESICO_ANALYST_ROUTE='analista-local', LESICO_REVIEWER_ROUTE='revision-local')
        code = """from app import app
response = app.test_client().get('/admin-local/administracion/asignaciones')
assert response.status_code == 200, response.status_code
html = response.get_data(as_text=True)
assert all(text in html for text in ('Ana', 'Carlos', 'Sin asignar', 'Trabajo encontrado: 3 tareas'))
assert all(text in html for text in ('Concepto A', 'Concepto B', 'Concepto C'))
client = app.test_client()
assert client.get('/mi-trabajo?collaborator_id=1').status_code == 404
for prefix in ('admin-local', 'revision-local'):
    assert client.get('/' + prefix + '/mi-trabajo?collaborator_id=1').status_code == 404
for prefix in ('analista-local',):
    response = client.get('/' + prefix + '/mi-trabajo?collaborator_id=1')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Concepto A' in html and 'Concepto B' not in html and 'Concepto C' not in html
    from flask import url_for
    with app.test_request_context(environ_overrides={'SCRIPT_NAME': '/' + prefix}):
        link = url_for('alternatives.alternativas', concept_id=1)
    assert link in html
    assert client.get(link).status_code == 200
html = client.get('/analista-local/mi-trabajo?collaborator_id=2').get_data(as_text=True)
assert 'Concepto A' in html and 'Concepto B' in html
html = client.get('/analista-local/mi-trabajo?collaborator_id=2&search=2').get_data(as_text=True)
assert 'Concepto A' not in html and 'Concepto B' in html
assert 'Selecciona un colaborador' in client.get('/analista-local/mi-trabajo').get_data(as_text=True)
assert 'coincidan' in client.get('/analista-local/mi-trabajo?collaborator_id=1&search=ZZZ').get_data(as_text=True)
assert client.get('/analista-local/administracion/asignaciones').status_code == 404
assert client.get('/analista-local/mi-trabajo?collaborator_id=1&page=bad').status_code == 400
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
