import importlib
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from database import crear_esquema
from occurrence_registration import RegistrationError, complete_registration, save_draft
from source_details import (effective_detail_2_applicability, normalize_occurrence_details,
                            normalize_applicability_override, normalize_detail)
from source_period import format_source_period
from tests import test_occurrence_grammar_routes as fixtures
from tests.form_client import hidden

ROOT = Path(__file__).resolve().parents[1]
KEY = 'source_detail_2_applicability_override'
SINGLE = 'VIDEO_POR_SENA'
MULTI = 'VARIOS_VIDEOS_VARIAS_SENAS'


class TimeApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,source_type) VALUES('Single',?)", (SINGLE,))
        self.db.execute("INSERT INTO source(source_name,source_type) VALUES('Multi',?)", (MULTI,))
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('Concept')")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def register(self, source_id=1, **values):
        return complete_registration(self.db, source_id=source_id, original_gloss='G',
                                     concept_id=1, source_detail_1_status='VALUE',
                                     source_detail_1='Video', **values)

    def state(self, identifier):
        return tuple(self.db.execute(f'SELECT source_detail_2_status,source_detail_2,{KEY} '
                                    'FROM occurrence WHERE occurrence_id=?', (identifier,)).fetchone())

    def test_normal_and_exceptional_registration(self):
        cases = [
            (2, 'VALUE', '2:35', None, ('VALUE', '2:35', None)),
            (2, 'VALUE', '2:35', 0, ('NA', None, 0)),
            (1, 'UNKNOWN', None, None, ('NA', None, None)),
            (1, 'VALUE', '1:02:35', 1, ('VALUE', '1:02:35', 1)),
        ]
        for source, status, value, override, expected in cases:
            with self.subTest(expected=expected):
                oid = self.register(source, source_detail_2_status=status,
                                    source_detail_2=value, **{KEY: override})
                self.assertEqual(self.state(oid), expected)

    def test_required_time_rejects_missing_invalid_and_nonvalue(self):
        for status, value in [('VALUE', None), ('VALUE', ''), ('VALUE', '99:99'),
                              ('VALUE', '1:2'), ('VALUE', '1:60:00'),
                              ('NA', None), ('UNKNOWN', None)]:
            with self.subTest(status=status, value=value), self.assertRaises(RegistrationError):
                self.register(source_detail_2_status=status, source_detail_2=value, **{KEY: 1})
        self.assertEqual(self.db.execute('SELECT count(*) FROM occurrence').fetchone()[0], 0)

    def test_normal_overrides_canonicalize_in_normalization_registration_and_drafts(self):
        for source_id, kind, normal, exception in [(1, SINGLE, 0, 1), (2, MULTI, 1, 0)]:
            for raw in (normal, str(normal)):
                with self.subTest(kind=kind, raw=raw):
                    self.assertIsNone(normalize_applicability_override(kind, raw))
                    self.assertEqual(normalize_applicability_override(kind, exception), exception)
                    self.assertEqual(
                        normalize_occurrence_details(kind, 'UNKNOWN', None, 'UNKNOWN', None, **{KEY: raw}),
                        normalize_occurrence_details(kind, 'UNKNOWN', None, 'UNKNOWN', None))
                    oid = self.register(source_id, source_detail_2_status='UNKNOWN', **{KEY: raw})
                    self.assertIsNone(self.state(oid)[2])
                    draft = save_draft(self.db, source_id=source_id, **{KEY: raw})
                    self.assertIsNone(self.db.execute(
                        f'SELECT {KEY} FROM occurrence_draft WHERE draft_id=?', (draft,)).fetchone()[0])
        # Canonicalizing normal choices must not override legacy VALUE/NA evidence.
        self.assertTrue(effective_detail_2_applicability(SINGLE, 'VALUE', 0))
        self.assertFalse(effective_detail_2_applicability(MULTI, 'NA', 1))
        for kind in ('UN_VIDEO_VARIAS_SENAS', 'MATERIAL_IMPRESO', 'OTRO'):
            self.assertIsNone(normalize_applicability_override(kind, None))
            for raw in (0, 1, '0', '1'):
                with self.subTest(kind=kind, raw=raw), self.assertRaises(ValueError):
                    normalize_applicability_override(kind, raw)

    def test_validation_messages_use_impersonal_reference_labels(self):
        for status, message in [
            ('invalid', 'Debe indicarse Dato, N/A o Desconocido para cada referencia aplicable.'),
            ('VALUE', 'Un campo marcado como Dato requiere un valor.'),
        ]:
            with self.assertRaises(ValueError) as error:
                normalize_detail(status, None)
            self.assertEqual(str(error.exception), message)

    def test_legacy_rules_are_independent_of_source_ids(self):
        for kind, status, expected in [(SINGLE, 'VALUE', True), (SINGLE, 'UNKNOWN', False),
                                       (MULTI, 'NA', False), ('OTRO', 'UNKNOWN', True),
                                       ('MATERIAL_IMPRESO', 'UNKNOWN', True),
                                       ('UN_VIDEO_VARIAS_SENAS', 'UNKNOWN', True)]:
            self.assertEqual(effective_detail_2_applicability(kind, status), expected)
        oid = self.register(source_detail_2_status='VALUE', source_detail_2='02:35')
        self.assertEqual(self.state(oid), ('VALUE', '02:35', None))
        oid = self.register(2, source_detail_2_status='NA')
        self.assertEqual(self.state(oid), ('NA', None, None))

    def test_draft_required_time_remains_incomplete_and_can_complete(self):
        draft = save_draft(self.db, source_id=1, original_gloss='G', reference_concept_id=1,
                           source_detail_2_status='VALUE', **{KEY: '1'})
        row = self.db.execute('SELECT * FROM occurrence_draft').fetchone()
        self.assertEqual((row[KEY], row['source_detail_2']), (1, None))
        with self.assertRaises(RegistrationError):
            complete_registration(self.db, draft_id=draft)
        oid = complete_registration(self.db, draft_id=draft, source_detail_2='2:03')
        self.assertEqual(self.state(oid), ('VALUE', '2:03', 1))

    def test_draft_clear_and_override_reset_do_not_restore_old_values(self):
        draft = save_draft(self.db, source_id=1, original_gloss='G', reference_concept_id=1,
                           source_detail_2_status='VALUE', source_detail_2='2:03', **{KEY: 1})
        with self.assertRaises(RegistrationError):
            complete_registration(self.db, draft_id=draft, source_detail_2='',
                                  source_detail_2_status='VALUE', **{KEY: '1'})
        oid = complete_registration(self.db, draft_id=draft, source_detail_2_status='NA',
                                    source_detail_2='', **{KEY: ''})
        self.assertEqual(self.state(oid), ('NA', None, None))

    def test_draft_na_clears_time_and_other_incomplete_fields_survive(self):
        draft = save_draft(self.db, source_id=2, source_detail_1_status='VALUE',
                           source_detail_2_status='VALUE', source_detail_2='2:03', **{KEY: 0})
        row = self.db.execute('SELECT * FROM occurrence_draft WHERE draft_id=?', (draft,)).fetchone()
        self.assertEqual((row[KEY], row['source_detail_2_status'], row['source_detail_2']), (0, 'NA', None))

    def test_invalid_overrides_and_unrelated_types_rejected(self):
        for kind, override in [(SINGLE, 'yes'), (MULTI, 2), ('OTRO', 0), ('MATERIAL_IMPRESO', 1),
                               ('UN_VIDEO_VARIAS_SENAS', 0)]:
            with self.subTest(kind=kind, override=override), self.assertRaises(ValueError):
                normalize_occurrence_details(kind, 'UNKNOWN', None, 'UNKNOWN', None, **{KEY: override})
        oid = self.register(2, source_detail_2_status='NA', **{KEY: 1})
        self.assertEqual(self.state(oid), ('NA', None, None))

    def test_migration_preview_backup_idempotence_and_no_backfill(self):
        migration = importlib.import_module('migrations.024_occurrence_time_applicability')
        with tempfile.TemporaryDirectory() as tmp:
            path, backup = Path(tmp)/'old.db', Path(tmp)/'backup.db'
            db = sqlite3.connect(path)
            for table in migration.TABLES:
                db.execute(f'CREATE TABLE {table}(source_detail_2_status TEXT)')
                db.execute(f"INSERT INTO {table} VALUES('VALUE')")
            db.commit()
            db.close()
            self.assertEqual(migration.migrate(path)['changes'], 3)
            self.assertEqual(migration.migrate(path, backup, apply=True)['changes'], 3)
            self.assertEqual(migration.migrate(path, backup, apply=True)['changes'], 0)
            self.assertTrue(backup.exists())
            db = sqlite3.connect(path)
            for table in migration.TABLES:
                self.assertEqual(db.execute(f'SELECT * FROM {table}').fetchall(), [('VALUE', None)])
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f'UPDATE {table} SET {KEY}=2')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            db.close()


class TimeApplicabilityRouteTests(unittest.TestCase):
    tearDown = fixtures.GrammarWorkflowRouteTests.tearDown
    connect = fixtures.GrammarWorkflowRouteTests.connect

    def setUp(self):
        fixtures.GrammarWorkflowRouteTests.setUp(self)
        self.client.application.jinja_env.filters['source_period'] = format_source_period
        db = self.connect()
        db.execute('UPDATE source SET source_type=?', (SINGLE,))
        db.execute("INSERT INTO source(source_name,source_type) VALUES('Multi',?)", (MULTI,))
        db.execute("INSERT INTO source(source_name,source_type) VALUES('Printed','MATERIAL_IMPRESO')")
        db.commit()
        db.close()

    def test_post_registration_and_revision_previous_override(self):
        data = {'source_id': '1', 'original_gloss': 'NEW', 'reference_kind': 'concept',
                'reference_concept_id': '1', 'source_detail_1_status': 'UNKNOWN',
                'source_detail_2_status': 'VALUE', 'source_detail_2': '2:03', KEY: '1'}
        self.assertEqual(self.client.post('/aportes', data=data).status_code, 302)
        html = self.client.get('/ocurrencias/2/editar').get_data(as_text=True)
        self.assertEqual(hidden(html, KEY), '1')
        data.update(edit_token=hidden(html, 'edit_token'), source_detail_2='',
                    source_detail_2_status='NA', **{KEY: '0'})
        self.assertEqual(self.client.post('/ocurrencias/2/actualizar', data=data).status_code, 302)
        db = self.connect()
        self.assertEqual(tuple(db.execute(f'SELECT {KEY},source_detail_2_status,source_detail_2 FROM occurrence WHERE occurrence_id=2').fetchone()), (None, 'NA', None))
        self.assertEqual(tuple(db.execute(f'SELECT {KEY},source_detail_2 FROM occurrence_revision WHERE occurrence_id=2').fetchone()), (1, '2:03'))
        db.close()

    def test_draft_post_reload_browser_and_completion(self):
        data = {'source_id': '1', 'original_gloss': 'DRAFT', 'reference_kind': 'concept',
                'reference_concept_id': '1', 'source_detail_1_status': 'UNKNOWN',
                'source_detail_2_status': 'VALUE', 'source_detail_2': '', KEY: '1'}
        self.assertEqual(self.client.post('/borradores/guardar', data=data).status_code, 302)
        html = self.client.get('/borradores/1/editar').get_data(as_text=True)
        self.assertEqual(hidden(html, KEY), '1')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.set_content(re.sub(r'<script src="[^"]*source-details.js"></script>', '', html))
            page.add_script_tag(content=(ROOT/'static/source-details.js').read_text(encoding='utf-8-sig'))
            self.assertTrue(page.locator('#time-applicability-checkbox').is_checked())
            time = page.locator('[name=source_detail_2]')
            self.assertTrue(time.is_enabled())
            self.assertFalse(time.evaluate('node => node.checkValidity()'))
            self.assertTrue(page.locator('button[formnovalidate]').evaluate('node => node.formNoValidate'))
            browser.close()
        self.assertEqual(self.client.post('/borradores/1/completar', data=data).status_code, 400)
        data['source_detail_2'] = '1:02'
        self.assertEqual(self.client.post('/borradores/1/completar', data=data).status_code, 302)
        db = self.connect()
        self.assertEqual(db.execute(f'SELECT {KEY} FROM occurrence WHERE occurrence_id=2').fetchone()[0], 1)
        db.close()

    def test_override_only_edit_snapshots_null_and_invalid_edit_is_atomic(self):
        db = self.connect()
        db.execute("UPDATE occurrence SET source_detail_2_status='VALUE',source_detail_2='2:03' WHERE occurrence_id=1")
        db.commit()
        db.close()
        html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
        data = {'source_id': '1', 'original_gloss': 'EVIDENCE',
                'source_detail_1_status': 'UNKNOWN', 'source_detail_2_status': 'VALUE',
                'source_detail_2': '2:03', KEY: '1', 'edit_token': hidden(html, 'edit_token')}
        self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data).status_code, 302)
        db = self.connect()
        self.assertIsNone(db.execute(f'SELECT {KEY} FROM occurrence_revision').fetchone()[0])
        before = list(db.iterdump())
        html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
        data.update(edit_token=hidden(html, 'edit_token'), source_detail_2='invalid')
        self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data).status_code, 400)
        self.assertEqual(list(db.iterdump()), before)
        db.close()

    def test_browser_legacy_checkboxes_toggle_and_source_preservation(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            for source, status, value, checked, explicit in [
                (1, 'VALUE', '2:03', True, None),
                (1, 'UNKNOWN', None, False, None),
                (2, 'NA', None, True, None),
                (2, 'VALUE', '2:03', False, None),
                (1, 'VALUE', '2:03', True, 1),
                (2, 'NA', None, True, 0),
            ]:
                db = self.connect()
                db.execute(f'UPDATE occurrence SET source_id=?,source_detail_2_status=?,source_detail_2=?,{KEY}=? WHERE occurrence_id=1',
                           (source, status, value, explicit))
                db.commit()
                db.close()
                html = self.client.get('/ocurrencias/1/editar').get_data(as_text=True)
                page.set_content(re.sub(r'<script src="[^"]*source-details.js"></script>', '', html))
                page.add_script_tag(content=(ROOT/'static/source-details.js').read_text(encoding='utf-8-sig'))
                check = page.locator('#time-applicability-checkbox')
                self.assertEqual(check.is_checked(), checked)
                expected_override = '' if explicit is None else str(explicit)
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), expected_override)
                # Unrelated rendering and a Source round trip must not materialize legacy overrides.
                page.locator('[name=original_gloss]').dispatch_event('change')
                page.locator('[name=source_id]').select_option('2' if source == 1 else '1')
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '')
                page.locator('[name=source_id]').select_option(str(source))
                self.assertEqual(check.is_checked(), checked)
                self.assertEqual(page.locator('[name=source_detail_2]').input_value(), value or '')
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), expected_override)
                # Verify the actual form payload and persistence without a checkbox interaction.
                form_data = page.locator('form').evaluate('form => {\n'
                    'form.dispatchEvent(new Event("submit", {cancelable: true}));\n'
                    'return Object.fromEntries(new FormData(form));\n'
                    '}')
                self.assertEqual(form_data[KEY], expected_override)
                self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=form_data).status_code, 302)
                db = self.connect()
                self.assertEqual(db.execute(f'SELECT {KEY} FROM occurrence WHERE occurrence_id=1').fetchone()[0], explicit)
                db.close()
                check.set_checked(not checked)
                expected = '' if checked else ('1' if source == 1 else '0')
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), expected)
                check.set_checked(False)
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '')
                check.set_checked(True)
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '1' if source == 1 else '0')
                page.locator('[name=source_id]').select_option('3')
                page.locator('[name=source_id]').select_option(str(source))
                self.assertTrue(check.is_checked())
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '1' if source == 1 else '0')
                check.set_checked(False)
                self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '')
            page.locator('[name=source_id]').select_option('3')
            self.assertTrue(page.locator('#time-applicability').is_hidden())
            self.assertEqual(page.locator(f'[name={KEY}]').input_value(), '')
            browser.close()


if __name__ == '__main__':
    unittest.main()
