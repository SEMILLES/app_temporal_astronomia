"""Temporary work assignments close with canonical decisions, never proposals."""
import sqlite3
import unittest

from database import crear_esquema
from concept_work_state import pending_counts, has_pending_work, reconcile_completed_work_assignments
from work_assignments import assign, my_work, remove, list_concepts
from alternative_change_workflow import create_proposal, review_proposal
from grammar_workflow import create_grammar_submission, resolve_grammar_submission
from alternative_admin import update_morphology, apply_relation_change, apply_direct_nomenclature
from alternative_workflow import create_alternative_submission, review_as_existing, review_as_new
from submission_concept_resolution import save_resolution
from alternative_structural import (apply_move, move_preview, apply_component_move,
    component_move_preview, apply_retire, retire_preview, apply_merge, merge_preview,
    apply_split, split_preview)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        crear_esquema(self.db)
        self.db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('ONE',), ('TWO',)])
        self.db.executemany('INSERT INTO collaborator(display_name) VALUES(?)', [('Ana',), ('Luis',), ('Revisor',)])
        self.db.execute("INSERT INTO source(source_name,start_year,end_year) VALUES('Synthetic',2000,2000)")
        self.db.commit()
        self.actor = dict(collaborator_id=3, access_role='reviewer')
        self.addCleanup(self.db.close)

    def alternative(self, concept=1, label='1a', morphology=False, grammar=True):
        aid = self.db.execute('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)',
                              (concept, label)).lastrowid
        oid = self.occurrence(concept, grammar=grammar)
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (oid, aid))
        if morphology:
            self.db.execute('INSERT INTO alternative_morphology(alternative_id,component_count) VALUES(?,1)', (aid,))
        self.db.commit()
        return aid, oid

    def occurrence(self, concept=1, grammar=True):
        oid = self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'Evidence',2000)").lastrowid
        if concept:
            self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,?)', (oid, concept))
        if grammar:
            self.db.execute("INSERT INTO occurrence_grammar(occurrence_id,gender) VALUES(?,'SIN-MARCA')", (oid,))
        return oid

    def assign(self, concepts=(1,), analysts=(1, 2)):
        self.db.commit()
        return assign(self.db, concepts, analysts, actor_id=3, access_role='reviewer')

    def active(self, concept=1):
        return self.db.execute('SELECT count(*) FROM concept_work_assignment WHERE active=1 AND concept_id=?', (concept,)).fetchone()[0]

    def legacy_assignment(self, concept):
        self.db.execute("INSERT INTO concept_work_assignment(concept_id,analyst_id,analyst_name_snapshot,created_access_role) VALUES(?,1,'Ana','master')", (concept,))
        self.db.commit()

    def test_assignment_requires_canonical_work_and_batch_is_atomic(self):
        self.alternative()
        with self.assertRaises(ValueError):
            self.assign((1, 2))
        self.assertEqual(self.active(), 0)
        self.assertEqual(self.assign(), 2)
        self.assertEqual(self.assign(), 0)
        self.assertTrue(has_pending_work(self.db, 1))
        self.assertFalse(has_pending_work(self.db, 2))
        self.assertEqual(pending_counts(self.db, [1])[1], dict(morphology=1, relations=0, grammar=0, assignment=0))

    def test_personal_work_hides_legacy_completed_assignments_without_writing(self):
        self.legacy_assignment(2)
        before = list(self.db.iterdump())
        self.assertEqual(my_work(self.db, 1)['concepts'], [])
        self.assertEqual(list(self.db.iterdump()), before)

    def test_acceptance_closes_every_analyst_with_history_and_actor(self):
        aid, _ = self.alternative()
        self.assign()
        sid = create_proposal(self.db, aid, 'MORPHOLOGY', {'component_count': 1},
                              collaborator_id=1, access_role='analyst')
        self.assertEqual(self.active(), 2)
        review_proposal(self.db, sid, 'accepted', **self.actor)
        self.assertEqual(self.active(), 0)
        rows = self.db.execute('SELECT * FROM concept_work_assignment').fetchall()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIsNotNone(row['removed_at'])
            self.assertEqual((row['removed_by_collaborator_id'], row['removed_by_name_snapshot'], row['removed_access_role']), (3, 'Revisor', 'reviewer'))
        self.assertEqual(my_work(self.db, 1)['concepts'], [])
        self.assertEqual(reconcile_completed_work_assignments(self.db, [1, 1]), 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='concept_work_assignment_completed'").fetchone()[0], 1)

    def test_remaining_dimension_then_grammar_acceptance_closes(self):
        aid, oid = self.alternative(grammar=False)
        self.assign()
        update_morphology(self.db, aid, {'component_count': 1}, self.actor)
        self.assertEqual(self.active(), 2)
        sid = create_grammar_submission(self.db, oid, {'gender': 'SIN-MARCA'})
        self.assertEqual(self.active(), 2)
        resolve_grammar_submission(self.db, sid, 'accepted', **self.actor)
        self.assertEqual(self.active(), 0)

    def test_new_work_does_not_reactivate_manual_removal_and_reassignment(self):
        aid, _ = self.alternative()
        self.assign(analysts=(1,))
        update_morphology(self.db, aid, {'component_count': 1}, self.actor)
        self.alternative(label='2a')
        self.assertTrue(has_pending_work(self.db, 1))
        self.assertEqual(self.active(), 0)
        self.assertEqual(my_work(self.db, 1)['concepts'], [])
        self.assertEqual(list_concepts(self.db, pending_only=True, status='unassigned')['total'], 1)
        self.assertEqual(self.assign(analysts=(1,)), 1)
        current = self.db.execute('SELECT work_assignment_id FROM concept_work_assignment WHERE active=1').fetchone()[0]
        self.assertEqual(remove(self.db, current, actor_id=3, access_role='master'), 1)
        self.assertEqual(self.assign(analysts=(1,)), 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM concept_work_assignment').fetchone()[0], 3)

    def test_rejections_do_not_close_even_stale_completed_assignments(self):
        aid, oid = self.alternative()
        self.assign()
        sid = create_proposal(self.db, aid, 'MORPHOLOGY', {'component_count': 1}, collaborator_id=1, access_role='analyst')
        self.db.execute('INSERT INTO alternative_morphology(alternative_id,component_count) VALUES(?,1)', (aid,))
        self.db.commit()
        review_proposal(self.db, sid, 'rejected', note='No aplicar', **self.actor)
        gs = create_grammar_submission(self.db, oid, {'gender': 'SIN-MARCA'})
        resolve_grammar_submission(self.db, gs, 'rejected', review_note='No aplicar', **self.actor)
        self.assertFalse(has_pending_work(self.db, 1))
        self.assertEqual(self.active(), 2)

    def test_reconcile_requires_role_only_for_actual_closures_and_caller_transaction(self):
        self.alternative()
        self.assign()
        self.assertEqual(reconcile_completed_work_assignments(self.db, 1), 0)
        self.legacy_assignment(2)
        with self.assertRaises(ValueError):
            reconcile_completed_work_assignments(self.db, 2, **self.actor)
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(ValueError):
            reconcile_completed_work_assignments(self.db, [2], access_role='analyst')
        self.assertEqual(reconcile_completed_work_assignments(self.db, [1, 2, 2], **self.actor), 1)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.active(2), 1)

    def test_outer_rollback_restores_canonical_change_closure_and_activity(self):
        aid, _ = self.alternative()
        self.assign()
        sid = create_proposal(self.db, aid, 'MORPHOLOGY', {'component_count': 1}, collaborator_id=1, access_role='analyst')
        before = list(self.db.iterdump())
        self.db.execute('BEGIN IMMEDIATE')
        review_proposal(self.db, sid, 'accepted', **self.actor)
        self.assertEqual(self.active(), 0)
        self.db.rollback()  # A later failure in the enclosing operation.
        self.assertEqual(list(self.db.iterdump()), before)

    def test_closure_activity_failure_rolls_back_the_entire_admin_decision(self):
        aid, _ = self.alternative()
        self.assign()
        self.db.execute("CREATE TRIGGER fail_completion BEFORE INSERT ON activity_event WHEN NEW.event_type='concept_work_assignment_completed' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            update_morphology(self.db, aid, {'component_count': 1}, self.actor)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_relation_and_direct_nomenclature_close_last_dimension(self):
        left, _ = self.alternative(morphology=True)
        right, _ = self.alternative(label='1b', morphology=True)
        self.assign()
        apply_relation_change(self.db, right, action='add', target_id=left, parameter='CM_1', actor=self.actor)
        self.assertEqual(self.active(), 0)
        self.db.execute('UPDATE alternative_relation SET is_current=0')
        self.db.commit()
        self.assign()
        apply_direct_nomenclature(self.db, 1, None, mode='automatic', reason=None, actor=self.actor)
        self.assertEqual(self.active(), 0)

    def test_negative_relation_confirmation_closes_after_renumbering(self):
        self.alternative(morphology=True)
        right, _ = self.alternative(label='1b', morphology=True)
        self.assign()
        sid = create_proposal(self.db, right, 'RELATION', {'relation_answer': 'NO'}, collaborator_id=1, access_role='analyst')
        review_proposal(self.db, sid, 'pending', **self.actor)
        self.assertEqual(self.active(), 2)
        review_proposal(self.db, sid, 'accepted', relations_resolution='NO_CONFIRMED', **self.actor)
        self.assertEqual(self.active(), 0)

    def test_existing_classification_closes_assignment_dimension(self):
        aid, _ = self.alternative(morphology=True)
        oid = self.occurrence()
        self.assign()
        sid = create_alternative_submission(self.db, oid, 'EXISTING', proposed_existing_alternative_id=aid)
        save_resolution(self.db, sid, 'CONFIRM_REFERENCE', **self.actor)
        self.assertEqual(self.active(), 2)
        review_as_existing(self.db, sid, aid, **self.actor)
        self.assertEqual(self.active(), 0)

    def test_new_classification_closes_assignment_dimension(self):
        oid = self.occurrence()
        self.assign()
        sid = create_alternative_submission(self.db, oid, 'NEW', phonological_relation_answer='NO', morphology={'component_count': 1})
        save_resolution(self.db, sid, 'CONFIRM_REFERENCE', **self.actor)
        review_as_new(self.db, sid, approve_morphology=True, **self.actor)
        self.assertEqual(self.active(), 0)

    def test_concept_resolution_closes_origin_but_leaves_destination_work(self):
        oid = self.occurrence()
        self.assign()
        sid = create_alternative_submission(self.db, oid, 'UNSURE', analysis_note='Revisar concepto')
        save_resolution(self.db, sid, 'USE_EXISTING', concept_id=2, note='Corresponde a TWO', **self.actor)
        self.assertEqual(self.active(), 0)
        self.assertTrue(has_pending_work(self.db, 2))

    def test_move_reconciles_origin_and_destination_independently(self):
        aid, _ = self.alternative()  # Its morphology task moves to destination.
        self.assign()
        self.legacy_assignment(2)
        preview = move_preview(self.db, aid, 2)
        apply_move(self.db, aid, 2, reason='Reclasificar', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(1), 0)
        self.assertEqual(self.active(2), 1)
        preview = move_preview(self.db, aid, 1)
        apply_move(self.db, aid, 1, reason='Reclasificar', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(2), 0)
        self.assertEqual(self.active(1), 0)  # Never reactivate history.

    def test_component_move_reconciles_origin_and_destination_independently(self):
        left, _ = self.alternative(morphology=True)
        right, _ = self.alternative(label='1b')
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,'CM_1')", (left, right))
        self.assign()
        self.legacy_assignment(2)
        preview = component_move_preview(self.db, left, 2)
        apply_component_move(self.db, left, 2, reason='Reclasificar grupo', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(1), 0)
        self.assertEqual(self.active(2), 1)
        preview = component_move_preview(self.db, left, 1)
        apply_component_move(self.db, left, 1, reason='Reclasificar grupo', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(2), 0)
        self.assertEqual(self.active(1), 0)

    def test_retirement_closes_only_when_no_referenced_unassigned_occurrence_remains(self):
        aid, oid = self.alternative()
        self.db.execute('UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=?', (oid,))
        self.assign()
        preview = retire_preview(self.db, aid, {oid: 'unassigned'})
        apply_retire(self.db, aid, {oid: 'unassigned'}, reason='Retiro', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(), 0)

    def test_merge_closes_missing_morphology_of_retired_origin(self):
        source, _ = self.alternative()
        target, _ = self.alternative(label='2a', morphology=True)
        self.assign()
        preview = merge_preview(self.db, source, target, 'keep_target')
        apply_merge(self.db, source, target, 'keep_target', reason='Misma forma', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(), 0)

    def test_split_keeps_assignment_for_new_morphology_tasks(self):
        aid, first = self.alternative(grammar=False)
        second = self.occurrence()
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (second, aid))
        self.assign()
        distribution = {first: 1, second: 2}
        preview = split_preview(self.db, aid, distribution, 2)
        apply_split(self.db, aid, distribution, 2, reason='Dos formas', actor=self.actor, expected_fingerprint=preview['fingerprint'])
        self.assertEqual(self.active(), 2)
        self.assertEqual(pending_counts(self.db, [1])[1]['morphology'], 2)


if __name__ == '__main__':
    unittest.main()
