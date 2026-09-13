"""A.4 acceptance coverage; reuse the audit fixture without its bug assertions."""
import sqlite3
import unittest
from unittest.mock import patch
from database import crear_esquema
from alternative_workflow import (create_alternative_submission, review_as_existing,
    review_as_new, plan_lexical_review, new_review_preview)
from alternative_nomenclature import InvalidNomenclatureError, calculate_nomenclature_preview
from submission_concept_resolution import save_resolution
from immediate_acceptance import alternative_operation, preview_operation, run_normal_review, confirm_operation


class AtomicLexicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('Audit')")
        self.db.executemany("INSERT INTO concept(preferred_label) VALUES(?)",
                            [("ORIGIN",), ("DESTINATION",)])
        # Initially canonical: origin A=1990, C=2000; destination D=1995, B=2005.
        for aid, concept, label, year in (
                (1, 1, "1a", 1990), (2, 1, "2a", 2000),
                (3, 2, "1a", 1995), (4, 2, "2a", 2005)):
            self.db.execute(
                "INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,?,?)",
                (aid, concept, label))
            self.db.execute(
                "INSERT INTO occurrence(occurrence_id,source_id,original_gloss,occurrence_year) VALUES(?,1,?,?)",
                (aid, str(aid), year))
            self.db.execute(
                "INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,?)",
                (aid, concept))
            self.db.execute(
                "INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)", (aid, aid))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def stored(self, concept):
        return dict(self.db.execute(
            "SELECT alternative_id,working_label FROM alternative WHERE concept_id=?",
            (concept,)))


    def dump(self):
        return '\n'.join(self.db.iterdump())

    def submission(self, concept=2, relations=()):
        sid = create_alternative_submission(self.db, 1, 'EXISTING', proposed_existing_alternative_id=1)
        save_resolution(self.db, sid, 'USE_EXISTING', concept_id=concept,
                        note='Reviewed', access_role='reviewer')
        return sid

    def existing(self, sid, destination=4):
        return review_as_existing(self.db, sid, destination, access_role='reviewer', review_note='Reviewed')

    def assert_plan(self, plan, new_id=None):
        for cid, calculation in plan['affected_concepts'].items():
            expected = {(new_id if k == 'new_alternative:1' else k): v for k,v in calculation['final_labels'].items()}
            self.assertEqual(self.stored(cid), expected)
            self.assertEqual(self.stored(cid), calculate_nomenclature_preview(self.db,cid)['suggestions'])

    def test_same_concept_preview_equals_close_one_event(self):
        sid = self.submission(1)
        before = self.dump()
        plan = plan_lexical_review(self.db,1,1,destination=2)
        self.assertEqual(before,self.dump())
        self.existing(sid,2)
        self.assert_plan(plan)
        self.assertEqual(self.db.execute('SELECT count(*) FROM renumber_event').fetchone()[0],1)

    def test_cross_concept_old_evidence_moves_both_maps_and_events(self):
        sid = self.submission()
        plan = plan_lexical_review(self.db,1,2,destination=4)
        self.existing(sid)
        self.assert_plan(plan)
        self.assertEqual(self.stored(1),{1:'2a',2:'1a'})
        self.assertEqual(self.stored(2),{3:'2a',4:'1a'})
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT concept_id,created_from_submission_id,origin FROM renumber_event ORDER BY concept_id')],[(1,sid,'automatic_assisted'),(2,sid,'automatic_assisted')])
        self.assertIsNone(self.db.execute('SELECT retired_at FROM alternative WHERE alternative_id=1').fetchone()[0])
        origin = calculate_nomenclature_preview(self.db,1)
        self.assertIsNone(next(r for r in origin['rows'] if r['alternative_id']==1)['reference_year'])

    def test_empty_origin_keeps_relations_and_participates(self):
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        self.db.commit()
        sid=self.submission()
        relations=[tuple(r) for r in self.db.execute('SELECT * FROM alternative_relation')]
        self.existing(sid)
        self.assertEqual(relations,[tuple(r) for r in self.db.execute('SELECT * FROM alternative_relation')])
        self.assertEqual(self.stored(1),{1:'1b',2:'1a'})

    def test_new_cross_concept_virtual_materialized_and_preview_equal(self):
        sid=self.submission()
        plan=plan_lexical_review(self.db,1,2)
        preview=new_review_preview(self.db,1,2)
        new=review_as_new(self.db,sid,access_role='reviewer',review_note='Reviewed')
        self.assert_plan(plan,new)
        self.assertEqual(self.stored(2),{(new if k=='new' else k):v for k,v in preview['suggestions'].items()})

    def test_origin_validation_failure_writes_nothing(self):
        from lexical_simulation import validate_concept_nomenclature
        sid=self.submission();before=self.dump()
        def invalid(state, labels):
            if state.concept_id==1:
                return dict(applicable=False,conflicts=[dict(code='ORIGIN_INVALID')])
            return validate_concept_nomenclature(state,labels)
        with patch('alternative_workflow.validate_concept_nomenclature',side_effect=invalid):
            with self.assertRaisesRegex(InvalidNomenclatureError,'ORIGIN_INVALID'):self.existing(sid)
        self.assertEqual(before,self.dump())

    def test_destination_validation_failure_writes_nothing(self):
        sid=self.submission();before=self.dump()
        from lexical_simulation import validate_concept_nomenclature
        def invalid(state,labels):
            if state.concept_id==2:return dict(applicable=False,conflicts=[dict(code='DESTINATION_INVALID')])
            return validate_concept_nomenclature(state,labels)
        with patch('alternative_workflow.validate_concept_nomenclature',side_effect=invalid):
            with self.assertRaisesRegex(InvalidNomenclatureError,'DESTINATION_INVALID'):self.existing(sid)
        self.assertEqual(before,self.dump())

    def test_exception_after_origin_map_rolls_back_all(self):
        sid=self.submission()
        self.db.execute("CREATE TRIGGER fail_destination BEFORE INSERT ON renumber_event WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.db.commit();before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.existing(sid)
        self.assertEqual(before,self.dump())

    def test_new_relation_and_closure_failure_roll_back_every_table(self):
        sid=self.submission()
        self.db.execute("INSERT INTO alternative_submission_relation(submission_id,target_alternative_id,phonological_parameter) VALUES(?,3,'CM_1')",(sid,))
        self.db.execute("CREATE TRIGGER fail_close BEFORE UPDATE ON submission WHEN NEW.status='resolved' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.db.commit();before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            review_as_new(self.db,sid,relations_resolution='ACCEPTED',access_role='reviewer',review_note='Reviewed')
        self.assertEqual(before,self.dump())

    def test_outer_savepoint_preserves_outer_work(self):
        sid=self.submission()
        self.db.execute("CREATE TRIGGER fail_destination BEFORE INSERT ON renumber_event WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.db.commit();self.db.execute('BEGIN')
        self.db.execute("UPDATE source SET source_name='Outer'")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.existing(sid)
        self.assertTrue(self.db.in_transaction);self.assertEqual(before,self.dump())

    def test_immediate_preview_ordinary_and_confirmation_identical(self):
        operation=alternative_operation(1,dict(proposal_kind='EXISTING',proposed_existing_alternative_id=1),
            dict(decision='existing',alternative_id=4,concept_resolution=dict(action='USE_EXISTING',concept_id=2,note='Reviewed')),
            actor_context=dict(access_role='reviewer'),review_note='Reviewed')
        before=self.dump(); observed=[]
        def inspect(db):
            result=operation(db);observed.append((self.stored(1),self.stored(2)));return result
        preview_operation(self.db,inspect)
        self.assertEqual(before,self.dump())
        confirm_operation(self.db,inspect)
        self.assertEqual(observed[0],observed[1])
        self.tearDown();self.setUp()
        run_normal_review(self.db,inspect,'Reviewed')
        self.assertEqual(observed[0],observed[2])

    def test_capacity_27_blocks_entire_new_close(self):
        for aid in range(5,29):
            self.db.execute("INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,2,?)",(aid,'1'+chr(97+aid-3)))
            self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(3,?,'CM_1')",(aid,))
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(3,4,'CM_1')")
        self.db.commit();sid=self.submission()
        self.db.execute("INSERT INTO alternative_submission_relation(submission_id,target_alternative_id,phonological_parameter) VALUES(?,3,'CM_1')",(sid,));self.db.commit()
        before=self.dump()
        with self.assertRaisesRegex(InvalidNomenclatureError,'VARIANT_CAPACITY_EXCEEDED'):
            review_as_new(self.db,sid,relations_resolution='ACCEPTED',access_role='reviewer',review_note='Reviewed')
        self.assertEqual(before,self.dump())

    def test_new_accepted_relations_follow_plan(self):
        sid=self.submission()
        self.db.execute("INSERT INTO alternative_submission_relation(submission_id,target_alternative_id,phonological_parameter) VALUES(?,3,'CM_1')",(sid,));self.db.commit()
        plan=plan_lexical_review(self.db,1,2,targets=[(3,'CM_1')])
        new=review_as_new(self.db,sid,relations_resolution='ACCEPTED',access_role='reviewer',review_note='Reviewed')
        self.assert_plan(plan,new)
        self.assertEqual(self.stored(2)[new],'1a');self.assertEqual(self.stored(2)[3],'1b')

    def test_new_rejected_relation_is_excluded(self):
        sid=self.submission()
        self.db.execute("INSERT INTO alternative_submission_relation(submission_id,target_alternative_id,phonological_parameter) VALUES(?,3,'CM_1')",(sid,));self.db.commit()
        plan=plan_lexical_review(self.db,1,2)
        new=review_as_new(self.db,sid,relations_resolution='REJECTED',access_role='reviewer',review_note='Reviewed')
        self.assert_plan(plan,new)
        self.assertEqual(self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0],0)

    def test_manual_and_adjusted_valid_gaps_and_blocked_group_inversion(self):
        for mode in ('manual','adjusted'):
            with self.subTest(mode=mode):
                sid=self.submission()
                self.db.execute("INSERT INTO alternative_submission_relation(submission_id,target_alternative_id,phonological_parameter) VALUES(?,3,'CM_1')",(sid,));self.db.commit()
                before=self.dump()
                with self.assertRaisesRegex(InvalidNomenclatureError,'GROUP_CHRONOLOGY_MISMATCH'):
                    review_as_new(self.db,sid,relations_resolution='ACCEPTED',nomenclature_mode=mode,
                        labels={'new':'2a',3:'2c',4:'1a'},reason='Editorial',access_role='reviewer',review_note='Reviewed')
                self.assertEqual(before,self.dump())
                new=review_as_new(self.db,sid,relations_resolution='ACCEPTED',nomenclature_mode=mode,
                    labels={'new':'1a',3:'1c',4:'2a'},reason='Editorial',access_role='reviewer',review_note='Reviewed')
                self.assertEqual(self.stored(2),{new:'1a',3:'1c',4:'2a'})
                self.assertEqual([tuple(r) for r in self.db.execute('SELECT concept_id,origin,reason FROM renumber_event ORDER BY concept_id')],
                    [(1,'automatic_assisted','Editorial'),(2,'manual','Editorial')])
                history=[tuple(r) for r in self.db.execute('SELECT * FROM renumber_event')]
                changes=[tuple(r) for r in self.db.execute('SELECT * FROM renumber_change ORDER BY renumber_change_id')]
                # A later automatic review must not reinterpret historical events.
                sid2=create_alternative_submission(self.db,1,'EXISTING',proposed_existing_alternative_id=new)
                save_resolution(self.db,sid2,'CONFIRM_REFERENCE',access_role='reviewer')
                self.existing(sid2,new)
                self.assertEqual(history,[tuple(r) for r in self.db.execute('SELECT * FROM renumber_event WHERE renumber_event_id<=2')])
                self.assertEqual(changes,[tuple(r) for r in self.db.execute('SELECT * FROM renumber_change WHERE renumber_event_id<=2 ORDER BY renumber_change_id')])
                self.tearDown();self.setUp()

    def test_immediate_new_preview_and_close_share_virtual_plan(self):
        operation=alternative_operation(1,dict(proposal_kind='EXISTING',proposed_existing_alternative_id=1),
            dict(decision='new',concept_resolution=dict(action='USE_EXISTING',concept_id=2,note='Reviewed')),
            actor_context=dict(access_role='reviewer'),review_note='Reviewed')
        before=self.dump();maps=[]
        def inspect(db):
            result=operation(db);maps.append((self.stored(1),self.stored(2)));return result
        preview_operation(self.db,inspect);self.assertEqual(before,self.dump())
        confirm_operation(self.db,inspect);self.assertEqual(maps[0],maps[1])
        self.tearDown();self.setUp()
        sid=self.submission();review_as_new(self.db,sid,access_role='reviewer',review_note='Reviewed')
        self.assertEqual(maps[0],(self.stored(1),self.stored(2)))

    def test_existing_same_assignment_revalidates_without_duplicate_event(self):
        sid=self.submission(1)
        self.existing(sid,1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM renumber_event').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM assignment').fetchone()[0],4)

    def test_existing_capacity_failure_in_origin_blocks_destination(self):
        for aid in range(5,30):
            self.db.execute("INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,1,NULL)",(aid,))
            self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,?,'CM_1')",(aid,))
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        self.db.commit();sid=self.submission();before=self.dump()
        with self.assertRaisesRegex(InvalidNomenclatureError,'VARIANT_CAPACITY_EXCEEDED'):self.existing(sid)
        self.assertEqual(before,self.dump())

    def test_materialization_mismatch_rolls_back(self):
        sid=self.submission()
        # A trigger changes actual evidence after planning: no replacement map
        # may be silently calculated and applied instead of the reviewed map.
        self.db.execute("CREATE TRIGGER change_year AFTER INSERT ON assignment BEGIN UPDATE occurrence SET occurrence_year=2099 WHERE occurrence_id=NEW.occurrence_id; END")
        self.db.commit();before=self.dump()
        with self.assertRaisesRegex(InvalidNomenclatureError,'GROUP_CHRONOLOGY_MISMATCH'):
            review_as_new(self.db,sid,access_role='reviewer',review_note='Reviewed')
        self.assertEqual(before,self.dump())


if __name__=='__main__':unittest.main()
