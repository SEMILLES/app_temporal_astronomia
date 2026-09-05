import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

import database
from concept_labels import alternative_display_label, human_concept_label
from source_period import format_source_period
from routes.occurrences import occurrences_bp
from routes.sources import sources_bp
from routes.concepts import concepts_bp
from routes.alternatives import alternatives_bp
from tests import test_alternative_structural as fixtures
from tests.form_client import hidden
from edit_concurrency import STALE_EDIT, STALE_PREVIEW


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        fixtures.StructuralAlternativeTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "concurrency.db"
        target = sqlite3.connect(self.path)
        self.db.backup(target)
        target.close()
        self.db.close()
        self.db = self.connect()
        self.db.execute("UPDATE source SET source_type='OTRO',end_year=2000")
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Editor')")
        self.db.commit()
        self.old = database.BASE_DATOS
        database.BASE_DATOS = self.path
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        app.config.update(TESTING=True, SECRET_KEY="test-concurrency")
        app.jinja_env.filters.update(alternative_display_label=alternative_display_label,
                                     human_concept_label=human_concept_label, source_period=format_source_period)
        for bp in (occurrences_bp, sources_bp, concepts_bp, alternatives_bp):
            app.register_blueprint(bp)
        self.role = "reviewer"
        @app.before_request
        def role():
            g.current_access_role = self.role
        self.client = app.test_client()
        self.app = app

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def tearDown(self):
        self.db.close()
        database.BASE_DATOS = self.old
        self.tmp.cleanup()

    def dump(self):
        return list(self.db.iterdump())

    def token(self, path, name="edit_token"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        token = hidden(response.get_data(as_text=True), name)
        self.assertTrue(token)
        return token

    def test_ten_occurrence_readers_first_writer_wins_and_activity(self):
        tokens = [self.token('/ocurrencias/1/editar') for _ in range(10)]
        data = dict(source_id=1, original_gloss='Edited', collaborator_id=1)
        self.assertEqual(self.client.post('/ocurrencias/1/actualizar', data=data | {'edit_token': tokens[0]}).status_code, 302)
        before = self.dump()
        for token in tokens[1:]:
            response = self.client.post('/ocurrencias/1/actualizar', data=data | {'edit_token': token, 'original_gloss': 'Lost'})
            self.assertEqual(response.status_code, 409)
            self.assertIn(STALE_EDIT, response.get_data(as_text=True))
            self.assertEqual(before, self.dump())
        self.assertEqual(self.db.execute('SELECT count(*) FROM occurrence_revision').fetchone()[0], 1)
        event = self.db.execute("SELECT * FROM activity_event WHERE event_type='occurrence_updated'").fetchone()
        self.assertEqual((event['collaborator_name_snapshot'], event['access_role']), ('Editor', 'reviewer'))

    def test_source_and_concept_stale_and_fresh(self):
        for prefix, data in [('fuentes', dict(source_name='Changed', source_type='OTRO', start_year=1900, end_year=2000, end_year_status='known')),
                             ('conceptos', dict(preferred_label='Renamed'))]:
            with self.subTest(prefix=prefix):
                path = f'/{prefix}/1'
                token = self.token(path+'/editar')
                payload = data | dict(edit_token=token, collaborator_id=1)
                self.assertEqual(self.client.post(path+'/actualizar', data=payload).status_code, 302)
                before = self.dump()
                self.assertEqual(self.client.post(path+'/actualizar', data=payload).status_code, 409)
                self.assertEqual(before, self.dump())
                payload['edit_token'] = self.token(path+'/editar')
                self.assertEqual(self.client.post(path+'/actualizar', data=payload).status_code, 302)
        events = self.db.execute("SELECT collaborator_name_snapshot,access_role FROM activity_event WHERE event_type IN ('source_updated','concept_renamed')").fetchall()
        self.assertEqual([tuple(r) for r in events], [('Editor', 'reviewer')]*2)

    def test_missing_tampered_and_wrong_record_edit_token(self):
        token = self.token('/ocurrencias/2/editar')
        before = self.dump()
        for value in ('', token, token+'bad'):
            response = self.client.post('/ocurrencias/1/actualizar', data=dict(source_id=1, original_gloss='bad', edit_token=value))
            self.assertEqual(response.status_code, 409)
            self.assertEqual(before, self.dump())

    def test_morphology_current_and_stale(self):
        path = '/alternativas/1/gestionar'
        token = self.token(path)
        data = dict(action='morphology', confirm='yes', component_count='2', free_permutation='NO', edit_token=token)
        self.assertEqual(self.client.post(path, data=data).status_code, 200)
        before = self.dump()
        self.assertEqual(self.client.post(path, data=data | {'component_count': '3'}).status_code, 409)
        self.assertEqual(before, self.dump())

    def test_grammar_precondition_from_open_through_confirm(self):
        token = self.token('/ocurrencias/1/gramatica')
        path = '/ocurrencias/1/gramatica/aceptacion-inmediata/'
        data = dict(gender='FEM-A', edit_token=token, confirm_immediate='yes', collaborator_id=1)
        self.assertEqual(self.client.post(path+'preview', data=data).status_code, 200)
        self.assertEqual(self.client.post(path+'confirmar', data=data).status_code, 302)
        before = self.dump()
        self.assertEqual(self.client.post(path+'confirmar', data=data).status_code, 409)
        self.assertEqual(self.client.post(path+'preview', data=data).status_code, 409)
        self.assertEqual(before, self.dump())

    def structural_data(self, kind):
        return {'retire': dict(occurrence_1=2, occurrence_2=2),
                'merge': dict(target_id=2, relation_mode='union'),
                'split': dict(new_count=2, split_occurrence_1=1, split_occurrence_2=2),
                'move': dict(destination_concept_id=2)}[kind]

    def preview(self, kind):
        before = self.dump()
        response = self.client.post('/alternativas/1/gestionar', data=self.structural_data(kind) | dict(action='preview_'+kind))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(before, self.dump())
        token = hidden(response.get_data(as_text=True), 'preview_token')
        self.assertTrue(token, response.get_data(as_text=True))
        return self.structural_data(kind) | dict(action='confirm_'+kind, preview_token=token, confirm='yes', reason='Reviewed', collaborator_id=1)

    def check_structural(self, kind):
        data = self.preview(kind)
        self.db.execute("UPDATE alternative_morphology SET note='changed' WHERE alternative_id=1 AND is_current=1")
        self.db.commit()
        before = self.dump()
        response = self.client.post('/alternativas/1/gestionar', data=data)
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn(STALE_PREVIEW, response.get_data(as_text=True))
        self.assertEqual(before, self.dump())
        fresh = self.preview(kind)
        response = self.client.post('/alternativas/1/gestionar', data=fresh)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        event = self.db.execute("SELECT * FROM activity_event WHERE event_type=?", ('alternative_'+{'retire':'retired','merge':'merged','split':'split','move':'moved'}[kind],)).fetchone()
        self.assertEqual((event['collaborator_name_snapshot'], event['access_role']), ('Editor', 'reviewer'))
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_retire_stale_then_new_preview(self): self.check_structural('retire')
    def test_merge_stale_then_new_preview(self): self.check_structural('merge')
    def test_split_stale_then_new_preview(self): self.check_structural('split')
    def test_move_stale_then_new_preview(self): self.check_structural('move')

    def test_structural_token_tampered_spec_changed_and_analyst(self):
        data = self.preview('merge')
        before = self.dump()
        for changes in (dict(preview_token='bad'), dict(target_id=3), dict(relation_mode='keep_target'), dict(preview_token='')):
            self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data | changes).status_code, 409)
            self.assertEqual(before, self.dump())
        self.role = 'analyst'
        self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 404)
        self.assertEqual(before, self.dump())

    def test_unrelated_change_does_not_invalidate(self):
        data = self.preview('merge')
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('Unrelated')")
        self.db.execute("UPDATE occurrence SET provenance_note='Unrelated note' WHERE occurrence_id=1")
        self.db.commit()
        self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 302)

    def test_all_operations_rollback_on_late_failure(self):
        self.db.execute("CREATE TRIGGER reject_structural BEFORE INSERT ON activity_event WHEN NEW.event_type IN ('alternative_retired','alternative_merged','alternative_split','alternative_moved') BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.db.commit()
        for kind in ('retire', 'merge', 'split', 'move'):
            data = self.preview(kind)
            before = self.dump()
            self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 400)
            self.assertEqual(before, self.dump())

    def test_relations_and_nomenclature_stale(self):
        path = '/alternativas/1/gestionar'
        token = self.token(path, 'state_token')
        response = self.client.post(path, data=dict(action='preview_retire_relation', relation_id=1))
        relation_token = hidden(response.get_data(as_text=True), 'state_token')
        self.db.execute("UPDATE alternative SET working_label='4a' WHERE alternative_id=3")
        self.db.commit()
        before = self.dump()
        for data in [dict(action='apply_nomenclature', state_token=token),
                     dict(action='confirm_relation', state_token=relation_token, relation_action='retire', target_id=2, parameter='CM_1', relation_id=1)]:
            self.assertEqual(self.client.post(path, data=data | dict(confirm='yes')).status_code, 409)
            self.assertEqual(before, self.dump())

    def test_relevant_state_changes_and_lock_before_reads(self):
        mutations = [
            "UPDATE assignment SET is_current=0 WHERE occurrence_id=1",
            "UPDATE occurrence SET occurrence_year=1950 WHERE occurrence_id=3",
            "UPDATE alternative_relation SET is_current=0 WHERE alternative_relation_id=1",
            "UPDATE concept SET preferred_label='Changed destination' WHERE concept_id=2",
            "UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1",
        ]
        for sql in mutations:
            with self.subTest(sql=sql):
                data = self.preview('move')
                original = self.db.serialize()
                self.db.execute(sql)
                self.db.commit()
                before = self.dump()
                self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 409)
                self.assertEqual(before, self.dump())
                self.db.close()
                clone = sqlite3.connect(':memory:')
                clone.deserialize(original)
                self.db = self.connect()
                clone.backup(self.db)
                clone.close()
        from alternative_structural import relevant_state
        data = self.preview('merge')
        checked = []
        def locked(db, *args):
            self.assertTrue(db.in_transaction)
            checked.append(True)
            return relevant_state(db, *args)
        # Only the first re-read is on the real DB; preview uses an isolated DB.
        def verify(db, *args):
            if db is not None and db.execute('PRAGMA database_list').fetchone()[2]:
                return locked(db, *args)
            return relevant_state(db, *args)
        with patch('alternative_structural.relevant_state', side_effect=verify):
            self.assertEqual(self.client.post('/alternativas/1/gestionar', data=data).status_code, 302)
        self.assertTrue(checked)

    def test_video_stale_replace_and_retire(self):
        path = '/alternativas/1/video'
        self.assertEqual(self.client.post(path, data=dict(action='add', youtube_url='https://youtu.be/dQw4w9WgXcQ')).status_code, 302)
        token = self.token(path)
        data = dict(action='replace', confirm='yes', youtube_url='https://youtu.be/9bZkp7q19f0', edit_token=token)
        self.assertEqual(self.client.post(path, data=data).status_code, 302)
        before = self.dump()
        for action in ('replace', 'retire'):
            self.assertEqual(self.client.post(path, data=data | dict(action=action)).status_code, 409)
            self.assertEqual(before, self.dump())

    def test_current_relation_preview_confirms(self):
        import re
        from html import unescape
        path = '/alternativas/1/gestionar'
        response = self.client.post(path, data=dict(action='preview_retire_relation', relation_id=1))
        page = response.get_data(as_text=True)
        # Only the confirmation form's proposed labels, not the separate form.
        form = page.split('value="confirm_relation"', 1)[1].split('</form>', 1)[0]
        labels = {name: unescape(value) for name, value in re.findall(r'name="(label_\d+)" value="([^"]*)"', form)}
        data = labels | dict(action='confirm_relation', confirm='yes', state_token=hidden(form, 'state_token'),
                             relation_action='retire', relation_id=1, target_id=2, parameter='CM_1', collaborator_id=1)
        response = self.client.post(path, data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.db.execute('SELECT is_current FROM alternative_relation WHERE alternative_relation_id=1').fetchone()[0], 0)

    def test_structural_previews_allow_read_only_connection(self):
        from alternative_structural import retire_preview, merge_preview, split_preview, move_preview
        self.db.execute('PRAGMA query_only=ON')
        changes = self.db.total_changes
        retire_preview(self.db, 1, {1: 2, 2: 2})
        merge_preview(self.db, 1, 2, 'union')
        split_preview(self.db, 1, {1: 1, 2: 2}, 2)
        move_preview(self.db, source_id=1, destination_concept_id=2)
        self.assertEqual(changes, self.db.total_changes)


if __name__ == '__main__':
    unittest.main()
