"""Synthetic state changes between a real preview and its confirmation."""
import unittest
from unittest.mock import patch
from flask import Flask

from tests import test_atomic_lexical_review as lexical_fixture
from tests import test_alternative_structural as structural_fixture
from tests import test_alternative_routes as route_fixture
from tests.form_client import hidden
from edit_concurrency import StaleEdit
from lexical_preconditions import lexical_token
from alternative_workflow import review_as_existing, review_as_new
from immediate_acceptance import alternative_operation, preview_operation, confirm_operation
from alternative_structural import (retire_preview, merge_preview, split_preview,
    move_preview, component_move_preview, apply_retire, apply_merge, apply_split,
    apply_move, apply_component_move, StructuralAlternativeError)


class LexicalStaleTests(unittest.TestCase):
    setUp = lexical_fixture.AtomicLexicalReviewTests.setUp
    tearDown = lexical_fixture.AtomicLexicalReviewTests.tearDown
    submission = lexical_fixture.AtomicLexicalReviewTests.submission
    dump = lexical_fixture.AtomicLexicalReviewTests.dump

    def token(self, sid):
        with Flask(__name__).app_context():
            return lexical_token(self.db, 1, sid)

    def apply(self, sid, token, new=False):
        with Flask(__name__).app_context():
            kwargs = dict(access_role='reviewer', review_note='Reviewed', expected_preview_token=token)
            return review_as_new(self.db, sid, **kwargs) if new else review_as_existing(self.db, sid, 4, **kwargs)

    def test_unchanged_existing_and_new(self):
        sid = self.submission()
        self.apply(sid, self.token(sid))
        self.db.close()
        self.setUp()
        sid = self.submission()
        self.apply(sid, self.token(sid), new=True)

    def test_stale_changes_block_before_any_write(self):
        changes = (
            'UPDATE assignment SET alternative_id=2 WHERE occurrence_id=1 AND is_current=1',
            'UPDATE alternative SET concept_id=2 WHERE alternative_id=1',
            "INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')",
            "UPDATE alternative SET working_label='3a' WHERE alternative_id=4",
            'UPDATE occurrence SET occurrence_year=1900 WHERE occurrence_id=3',
            'UPDATE submission_concept_resolution SET concept_id=1 WHERE is_current=1',
        )
        sid = self.submission()
        for sql in changes:
            for new in (False, True):
                with self.subTest(sql=sql, new=new):
                    token = self.token(sid)
                    self.db.execute(sql)
                    self.db.commit()
                    before = self.dump()
                    writes = []
                    self.db.set_trace_callback(lambda sql: writes.append(sql) if sql.split()[0].upper() in ('INSERT','UPDATE','DELETE') else None)
                    with self.assertRaises(StaleEdit):
                        self.apply(sid, token, new)
                    self.db.set_trace_callback(None)
                    self.assertEqual(writes, [])
                    self.assertEqual(before, self.dump())
                    # Restore the synthetic fixture for the next independent mutation.
                    self.db.close()
                    self.setUp()
                    sid = self.submission()

    def test_history_and_visual_text_do_not_invalidate(self):
        sid = self.submission()
        token = self.token(sid)
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id,is_current) VALUES(1,2,0)")
        self.db.execute("UPDATE occurrence SET original_gloss='Visual change' WHERE occurrence_id=1")
        self.db.execute("UPDATE source SET source_name='Visual change'")
        self.db.execute("UPDATE concept SET preferred_label='Visual change' WHERE concept_id=2")
        self.db.execute("INSERT INTO renumber_event(concept_id,origin,reason) VALUES(2,'automatic_assisted','Historical annotation')")
        self.db.commit()
        self.apply(sid, token)

    def test_no_renumber_event_when_map_is_unchanged(self):
        sid = self.submission(concept=1)
        with Flask(__name__).app_context():
            review_as_existing(self.db, sid, 1, access_role='reviewer',
                expected_preview_token=self.token(sid))
        self.assertEqual(self.db.execute('SELECT count(*) FROM renumber_event').fetchone()[0], 0)

    def test_tampered_missing_and_wrong_submission_tokens(self):
        sid = self.submission()
        for token in ('', self.token(sid) + 'tampered'):
            before = self.dump()
            with self.assertRaises(StaleEdit):
                self.apply(sid, token)
            self.assertEqual(before, self.dump())

    def test_valid_token_rolls_back_second_nomenclature_failure(self):
        sid = self.submission()
        token = self.token(sid)
        before = self.dump()
        import alternative_workflow
        original = alternative_workflow.apply_nomenclature
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError('injected second nomenclature failure')
            return original(*args, **kwargs)
        with patch('alternative_workflow.apply_nomenclature', side_effect=fail_second):
            with self.assertRaises(ValueError):
                self.apply(sid, token)
        self.assertEqual(len(calls), 2)
        self.assertEqual(before, self.dump())

    def test_immediate_stale_and_changed_decision_no_writes(self):
        proposal = {'proposal_kind': 'EXISTING', 'proposed_existing_alternative_id': 2}
        decision = {'decision': 'existing', 'alternative_id': 2}
        op = alternative_operation(1, proposal, decision, actor_context={'access_role': 'reviewer'}, review_note='Reviewed')
        with Flask(__name__).app_context():
            before = self.dump()
            token = preview_operation(self.db, op)['preview_token']
            self.assertEqual(before, self.dump())
            changed = alternative_operation(1, proposal, dict(decision, alternative_id=1), actor_context={'access_role':'reviewer'})
            with self.assertRaises(StaleEdit):
                confirm_operation(self.db, changed, expected_preview_token=token)
            self.assertEqual(before, self.dump())
            self.db.execute('UPDATE assignment SET alternative_id=2 WHERE occurrence_id=1 AND is_current=1')
            self.db.commit()
            before = self.dump()
            with self.assertRaises(StaleEdit):
                confirm_operation(self.db, op, expected_preview_token=token)
            self.assertEqual(before, self.dump())
            token = preview_operation(self.db, op)['preview_token']
            confirm_operation(self.db, op, expected_preview_token=token)


class StructuralStaleTests(unittest.TestCase):
    setUp = structural_fixture.StructuralAlternativeTests.setUp
    tearDown = structural_fixture.StructuralAlternativeTests.tearDown

    def operations(self):
        return (
            (retire_preview, apply_retire, (1, {1:2,2:2})),
            (merge_preview, apply_merge, (1,2,'union')),
            (split_preview, apply_split, (1,{1:1,2:2},2)),
            (move_preview, apply_move, (4,1)),
            (component_move_preview, apply_component_move, (1,2)),
        )

    def test_every_operation_stale_assignment_relation_concept_and_labels(self):
        changes = (
            'UPDATE assignment SET alternative_id=3 WHERE occurrence_id=1 AND is_current=1',
            "UPDATE alternative_relation SET phonological_parameter='UB_M1' WHERE alternative_relation_id=1",
            'UPDATE alternative SET concept_id=2 WHERE alternative_id=3',
            "UPDATE alternative SET working_label='7a' WHERE alternative_id=2",
        )
        for preview, apply, args in self.operations():
            for sql in changes:
                with self.subTest(operation=apply.__name__, sql=sql):
                    before = '\n'.join(self.db.iterdump())
                    token = preview(self.db, *args)['fingerprint']
                    self.assertEqual(before, '\n'.join(self.db.iterdump()))
                    self.db.execute(sql)
                    self.db.commit()
                    before = '\n'.join(self.db.iterdump())
                    with self.assertRaises(StaleEdit):
                        apply(self.db, *args, actor=self.actor, reason='Reviewed', expected_fingerprint=token)
                    self.assertEqual(before, '\n'.join(self.db.iterdump()))
                    self.db.close()
                    self.setUp()

    def test_all_structural_backends_reject_analyst(self):
        for preview, apply, args in self.operations():
            with self.subTest(operation=apply.__name__):
                token = preview(self.db, *args)['fingerprint']
                before = '\n'.join(self.db.iterdump())
                with self.assertRaises(StructuralAlternativeError):
                    apply(self.db, *args, actor={'access_role':'analyst'}, reason='Reviewed', expected_fingerprint=token)
                self.assertEqual(before, '\n'.join(self.db.iterdump()))

    def test_relation_effects_match_preview_and_canonical_maps(self):
        from alternative_nomenclature import calculate_nomenclature_preview
        for preview, apply, args in self.operations():
            with self.subTest(operation=apply.__name__):
                plan = preview(self.db, *args)
                before = {r[0]: tuple(r[1:]) for r in self.db.execute('SELECT alternative_relation_id,alternative_low_id,alternative_high_id,phonological_parameter FROM alternative_relation WHERE is_current=1')}
                apply(self.db, *args, actor={'access_role':'master'}, reason='Reviewed', expected_fingerprint=plan['fingerprint'])
                after = {r[0]: tuple(r[1:]) for r in self.db.execute('SELECT alternative_relation_id,alternative_low_id,alternative_high_id,phonological_parameter FROM alternative_relation WHERE is_current=1')}
                expected_retired = plan.get('relations_retired', plan.get('relations', []) if plan['kind']=='retire' else [])
                self.assertEqual(set(before)-set(after), {r['alternative_relation_id'] for r in expected_retired})
                self.assertEqual({after[r] for r in set(after)-set(before)}, set(plan.get('relations_created', [])))
                self.assertEqual(self.db.execute('SELECT count(*) FROM assignment a JOIN alternative al USING(alternative_id) WHERE a.is_current=1 AND al.retired_at IS NOT NULL').fetchone()[0], 0)
                for cid in (1,2):
                    result = calculate_nomenclature_preview(self.db, cid)
                    self.assertTrue(result['conclusive'])
                    actual = dict(self.db.execute('SELECT alternative_id,working_label FROM alternative WHERE concept_id=? AND retired_at IS NULL', (cid,)))
                    self.assertEqual(actual, result['suggestions'])
                self.db.close()
                self.setUp()

    def test_destructive_previews_block_legacy_cross_concept_relations(self):
        self.db.execute('UPDATE alternative SET concept_id=2 WHERE alternative_id=3')
        self.db.commit()
        for preview, _, args in self.operations():
            if preview == move_preview:
                continue
            with self.subTest(operation=preview.__name__):
                before = '\n'.join(self.db.iterdump())
                with self.assertRaises(StructuralAlternativeError):
                    preview(self.db, *args)
                self.assertEqual(before, '\n'.join(self.db.iterdump()))

    def test_merge_blocks_more_than_26_variants_without_writes(self):
        for index in range(25):
            aid = self.db.execute('INSERT INTO alternative(concept_id,working_label) VALUES(1,?)', (f'{index+4}a',)).lastrowid
            self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,?,'CM_1')", (aid,))
        self.db.commit()
        before = '\n'.join(self.db.iterdump())
        with self.assertRaises(ValueError):
            merge_preview(self.db, 1, 2, 'union')
        self.assertEqual(before, '\n'.join(self.db.iterdump()))


class LexicalHTTPTests(unittest.TestCase):
    setUp = route_fixture.AlternativeRouteTests.setUp
    tearDown = route_fixture.AlternativeRouteTests.tearDown
    connect = route_fixture.AlternativeRouteTests.connect

    def snapshot(self):
        db = self.connect()
        try:
            return '\n'.join(db.iterdump())
        finally:
            db.close()

    def ordinary(self):
        from alternative_workflow import create_alternative_submission
        from submission_concept_resolution import save_resolution
        db = self.connect()
        try:
            sid = create_alternative_submission(db, 2, 'EXISTING', proposed_existing_alternative_id=1)
            save_resolution(db, sid, 'CONFIRM_REFERENCE', access_role='reviewer')
        finally:
            db.close()
        page = self.client.get(f'/aportes/{sid}').get_data(as_text=True)
        return f'/aportes/{sid}/decidir', {'decision':'existing', 'alternative_id':'1',
            'lexical_preview_token':hidden(page, 'lexical_preview_token')}

    def immediate(self):
        path = '/ocurrencias/3/clasificar/aceptacion-inmediata/'
        form = {'proposal_kind':'EXISTING', 'proposed_existing_alternative_id':'1', 'confirm_immediate':'yes'}
        page = self.client.post(path+'preview', data=form)
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
        form['lexical_preview_token'] = hidden(page.get_data(as_text=True), 'lexical_preview_token')
        return path+'confirmar', form

    def test_real_forms_stale_409_without_partial_events(self):
        for prepare in (self.ordinary, self.immediate):
            with self.subTest(flow=prepare.__name__):
                path, form = prepare()
                self.assertTrue(form['lexical_preview_token'])
                db = self.connect()
                db.execute('UPDATE occurrence SET occurrence_year=occurrence_year+1 WHERE occurrence_id=1')
                db.commit()
                db.close()
                before = self.snapshot()
                response = self.client.post(path, data=form)
                self.assertEqual(response.status_code, 409)
                self.assertIn('El estado cambió desde la vista previa', response.get_data(as_text=True))
                self.assertEqual(before, self.snapshot())

    def test_direct_missing_and_tampered_tokens_rejected(self):
        for prepare in (self.ordinary, self.immediate):
            path, form = prepare()
            for token in ('', form['lexical_preview_token']+'tampered'):
                with self.subTest(flow=prepare.__name__, token=bool(token)):
                    before = self.snapshot()
                    response = self.client.post(path, data=dict(form, lexical_preview_token=token))
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(before, self.snapshot())

    def test_immediate_changed_proposal_and_token_replay(self):
        path, form = self.immediate()
        before = self.snapshot()
        changed = dict(form, proposal_kind='NEW', phonological_relation_answer='NO', morphology_component_count='N/A')
        self.assertEqual(self.client.post(path, data=changed).status_code, 409)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.client.post(path, data=form).status_code, 302)
        before = self.snapshot()
        self.assertEqual(self.client.post(path, data=form).status_code, 409)
        self.assertEqual(before, self.snapshot())

    def test_ordinary_replay_and_analyst_post(self):
        from flask import g
        path, form = self.ordinary()
        self.assertEqual(self.client.post(path, data=form).status_code, 302)
        before = self.snapshot()
        self.assertEqual(self.client.post(path, data=form).status_code, 409)
        self.assertEqual(before, self.snapshot())
        # The app has already served requests; replace its test role callback.
        self.client.application.before_request_funcs[None][-1] = lambda: setattr(g, 'current_access_role', 'analyst')
        self.assertEqual(self.client.post(path, data=form).status_code, 404)
        self.assertEqual(before, self.snapshot())

