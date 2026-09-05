import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from access_control import install_access_context
from alternative_morphology import create_or_replace_alternative_morphology
from alternative_workflow import create_alternative_submission
from normalization.phase17a_concept_references import normalize
from routes.concepts import concepts_bp
from tests import test_alternative_routes as fixtures


class WorkflowIntegrityTests(unittest.TestCase):
    setUp = fixtures.AlternativeRouteTests.setUp
    tearDown = fixtures.AlternativeRouteTests.tearDown
    connect = fixtures.AlternativeRouteTests.connect

    def test_indexed_components_review_and_versioning(self):
        for count in (2, 3):
            with self.subTest(count=count):
                data = dict(proposal_kind="NEW", phonological_relation_answer="NO",
                            morphology_component_count=str(count), free_permutation="NO",
                            record_components="yes", component_row_id=[str(i) for i in range(count)])
                for i in range(count):
                    data.update({f"component_{i}_position": str(i+1),
                                 f"component_{i}_type": "existing" if i == 0 else "unapproved",
                                 f"component_{i}_alternative_id": "1" if i == 0 else "",
                                 f"component_{i}_note": f"ROW-{count}-{i}"})
                self.assertEqual(self.client.post('/ocurrencias/2/clasificar', data=data).status_code, 302)
                db = self.connect()
                sid = db.execute('SELECT max(submission_id) FROM submission').fetchone()[0]
                rows = db.execute('SELECT * FROM alternative_submission_component WHERE submission_id=? ORDER BY position', (sid,)).fetchall()
                self.assertEqual(len(rows), count)
                self.assertEqual([r['component_alternative_id'] for r in rows], [1]+[None]*(count-1))
                db.close()
                page = self.client.get('/aportes/pendientes').get_data(as_text=True)
                for i in range(count):
                    self.assertIn(f'ROW-{count}-{i}', page)
                self.assertEqual(self.client.post(f'/aportes/{sid}/decidir', data=dict(
                    decision='new', approve_morphology='yes', nomenclature_mode='automatic')).status_code, 302)
                db = self.connect()
                version = db.execute('SELECT * FROM alternative_morphology WHERE created_from_submission_id=?', (sid,)).fetchone()
                self.assertEqual(db.execute('SELECT count(*) FROM alternative_component WHERE alternative_morphology_id=?', (version['alternative_morphology_id'],)).fetchone()[0], count)
                components = [dict(position=r['position'], component_alternative_id=r['component_alternative_id'], note=r['note']) for r in rows]
                new_id, changed = create_or_replace_alternative_morphology(db, version['alternative_id'], component_count=count, free_permutation='NO', note='new version', components=components)
                self.assertTrue(changed)
                self.assertEqual(db.execute('SELECT count(*) FROM alternative_component WHERE alternative_morphology_id=?', (new_id,)).fetchone()[0], count)
                db.close()

    def test_malformed_components_never_truncate(self):
        base = dict(proposal_kind='NEW', phonological_relation_answer='NO', morphology_component_count='2', free_permutation='NO', record_components='yes')
        cases = [dict(component_position=['1', '2'], component_type=['unapproved'], component_alternative_id=['', ''], component_note=['a', 'b']),
                 dict(component_row_id=['0'], component_0_position='1', component_0_type='unapproved', component_0_note='a'),
                 dict(component_row_id=['0', '0'], component_0_position='1', component_0_type='unapproved', component_0_note='a', component_0_alternative_id=''),
                 dict(component_position='1', component_type='', component_alternative_id='', component_note='')]
        for case in cases:
            self.assertEqual(self.client.post('/ocurrencias/2/clasificar', data=base | case).status_code, 400)
        db = self.connect()
        self.assertEqual(db.execute('SELECT count(*) FROM submission').fetchone()[0], 0)
        db.close()

    def test_browser_component_rows_have_independent_types(self):
        from playwright.sync_api import sync_playwright
        page_html = self.client.get('/ocurrencias/2/clasificar').get_data(as_text=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.set_content(page_html)
            page.locator('[name=proposal_kind][value=NEW]').check()
            page.locator('[name=morphology_component_count]').select_option('3')
            page.locator('[name=record_components][value=yes]').check()
            for i in range(3):
                page.locator('#add-component').click()
                page.locator(f'[name=component_{i}_type][value={"existing" if i == 0 else "unapproved"}]').check()
                if i == 0:
                    page.locator('[name=component_0_alternative_id]').select_option('1')
                page.locator(f'[name=component_{i}_note]').fill(f'Browser row {i}')
            self.assertEqual(page.locator('[data-component-type]:checked').count(), 3)
            values = page.locator('#components').evaluate("node => Array.from(new FormData(node.closest('form')).entries())")
            from werkzeug.datastructures import MultiDict
            from routes.occurrences import _component_rows
            rows = _component_rows(MultiDict(values))
            self.assertEqual(len(rows), 3)
            self.assertEqual([row['component_alternative_id'] for row in rows], ['1', None, None])
            browser.close()

    def test_concept_permissions_and_atomic_history(self):
        app = self.client.application
        app.register_blueprint(concepts_bp)
        app.add_url_rule('/trabajo', endpoint='main.trabajo', view_func=lambda: '')
        app.add_url_rule('/conceptos/<int:concept_id>/alternativas', endpoint='alternatives.alternativas', view_func=lambda concept_id: '')
        install_access_context(app)
        app.wsgi_app.routes = {'ana': 'analyst', 'rev': 'reviewer', 'mas': 'master'}
        for path, method in [('/conceptos/nuevo', 'post'), ('/conceptos/1/editar', 'get'), ('/conceptos/1/actualizar', 'post')]:
            self.assertEqual(getattr(self.client, method)('/ana'+path, data={'preferred_label': 'BAD'}).status_code, 404)
        page = self.client.get('/ana/conceptos').get_data(as_text=True)
        self.assertNotIn('Guardar concepto', page)
        self.assertNotIn('/editar', page)
        for prefix, role in [('rev', 'reviewer'), ('mas', 'master')]:
            self.assertEqual(self.client.post(f'/{prefix}/conceptos/nuevo', data={'preferred_label': prefix}).status_code, 302)
            self.assertEqual(self.client.get(f'/{prefix}/conceptos/1/editar').status_code, 200)
            self.assertEqual(self.client.post(f'/{prefix}/conceptos/1/actualizar', data={'preferred_label': prefix+' renamed'}).status_code, 302)
            db = self.connect()
            event = db.execute("SELECT * FROM activity_event WHERE event_type='concept_renamed' ORDER BY activity_event_id DESC").fetchone()
            self.assertEqual(event['entity_id'], 1)
            self.assertEqual(event['access_role'], role)
            self.assertTrue(event['occurred_at'])
            self.assertEqual(json.loads(event['comment'])['new_label'], prefix.upper()+'-RENAMED')
            self.assertEqual(db.execute('SELECT concept_id FROM alternative WHERE alternative_id=1').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            db.close()


class RealCopyBackfillTests(unittest.TestCase):
    def test_baseline_copy_and_idempotence(self):
        baseline = Path(__file__).resolve().parents[1] / 'import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase16b_2026-09-05.db'
        if not baseline.exists():
            self.skipTest('Private baseline unavailable')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'phase17a.db'
            shutil.copy2(baseline, path)
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('occurrence_concept_reference','activity_event','sqlite_sequence')")]
            snapshot = lambda: {t: [tuple(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
            before = snapshot()
            expected = [tuple(r) for r in db.execute('SELECT occurrence_id,concept_id FROM assignment JOIN alternative USING(alternative_id) WHERE assignment.is_current=1 ORDER BY occurrence_id')]
            self.assertEqual(normalize(db, apply=True)['created'], 253)
            self.assertEqual([tuple(r) for r in db.execute('SELECT occurrence_id,concept_id FROM occurrence_concept_reference WHERE is_current=1 ORDER BY occurrence_id')], expected)
            self.assertEqual(normalize(db, apply=True)['created'], 0)
            self.assertEqual(snapshot(), before)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            create_alternative_submission(db, expected[0][0], 'UNSURE', analysis_note='Phase17A reanalysis smoke')
            db.close()
