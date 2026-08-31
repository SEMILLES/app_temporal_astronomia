import sqlite3
import unittest

from database import crear_esquema
from conflicts import detect_conflicts_after_change
from immediate_acceptance import (ImmediateAcceptanceError,ImmediateBlockingError,
    alternative_operation,concept_registration_operation,confirm_operation,
    grammar_operation,preview_operation,run_normal_review)


class ImmediateAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',2000,2005,'known')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('C')")
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'OLD',2000)")
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'NEW',2001)")
        self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)")
        self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(2,1)")
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')")
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Reviewer')");self.db.commit()
        self.actor={"collaborator_id":1,"access_role":"reviewer"}
    def tearDown(self):self.db.close()

    def counts(self):
        return tuple(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("submission","occurrence_grammar","activity_event","conflict"))

    def test_grammar_preview_has_no_writes_and_confirm_versions_with_provenance(self):
        operation=grammar_operation(2,{"gender":"FEM-A"},actor_context=self.actor,reviewed_by="Reviewer")
        before=self.counts();preview=preview_operation(self.db,operation);self.assertEqual(before,self.counts());self.assertEqual([],preview["blocking"])
        result=confirm_operation(self.db,operation);sid=result["result"]
        self.assertEqual(("resolved","accepted"),tuple(self.db.execute("SELECT status,resolution FROM submission WHERE submission_id=?",(sid,)).fetchone()))
        self.assertEqual(sid,self.db.execute("SELECT created_from_submission_id FROM occurrence_grammar WHERE occurrence_id=2 AND is_current=1").fetchone()[0])
        self.assertEqual(["grammar_submission_created","grammar_submission_accepted"],[r[0] for r in self.db.execute("SELECT event_type FROM activity_event ORDER BY activity_event_id")])

    def test_alternative_existing_immediate_keeps_history_and_assignment(self):
        operation=alternative_operation(2,{"proposal_kind":"EXISTING","proposed_existing_alternative_id":1},{"decision":"existing","alternative_id":1},actor_context=self.actor)
        self.assertEqual((0,0),tuple(self.db.execute("SELECT count(*) FROM submission UNION ALL SELECT count(*) FROM assignment WHERE occurrence_id=2").fetchall()[i][0] for i in range(2)))
        preview_operation(self.db,operation);self.assertEqual(0,self.db.execute("SELECT count(*) FROM submission").fetchone()[0])
        result=confirm_operation(self.db,operation)["result"];sid=result["submission_id"]
        self.assertEqual(1,self.db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0]);self.assertEqual(1,self.db.execute("SELECT resolved_alternative_id FROM alternative_submission WHERE submission_id=?",(sid,)).fetchone()[0])

    def test_new_nonblocking_pending_morphology_is_allowed(self):
        proposal={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology":{"component_count":None,"component_count_not_applicable":True,"free_permutation":"N/A","components":[]}}
        operation=alternative_operation(2,proposal,{"decision":"new","approve_morphology":False,"nomenclature_mode":"automatic"},actor_context=self.actor)
        preview=preview_operation(self.db,operation);self.assertEqual([],preview["blocking"]);self.assertEqual(["PENDING_MORPHOLOGY"],[c["rule_code"] for c in preview["non_blocking"]]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM submission").fetchone()[0])
        result=confirm_operation(self.db,operation);self.assertEqual(["PENDING_MORPHOLOGY"],[c["rule_code"] for c in result["non_blocking"]]);self.assertEqual(1,self.db.execute("SELECT count(*) FROM conflict WHERE status='open'").fetchone()[0])

    def test_new_can_materialize_morphology_and_unsure_requires_decision(self):
        proposal={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology":{"component_count":None,"component_count_not_applicable":True,"free_permutation":"N/A","components":[]}}
        result=confirm_operation(self.db,alternative_operation(2,proposal,{"decision":"new","approve_morphology":True,"nomenclature_mode":"automatic"},actor_context=self.actor))["result"]
        self.assertEqual(result["submission_id"],self.db.execute("SELECT created_from_submission_id FROM alternative_morphology").fetchone()[0])
        unsure={"proposal_kind":"UNSURE","analysis_note":"Revisar"}
        with self.assertRaisesRegex(ImmediateAcceptanceError,"Debe decidir"):preview_operation(self.db,alternative_operation(1,unsure,{"decision":""},actor_context=self.actor))
        self.assertEqual(1,self.db.execute("SELECT count(*) FROM submission").fetchone()[0])

    def test_blocking_preflight_and_confirm_rollback_everything(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')");self.db.commit()
        def invalid(connection):
            connection.execute("UPDATE alternative SET working_label='1' WHERE alternative_id=2")
            detect_conflicts_after_change(connection,"alternative",2,actor_context=self.actor)
            return 2
        before=[tuple(r) for r in self.db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id")]
        preview=preview_operation(self.db,invalid);self.assertTrue(preview["blocking"]);self.assertEqual(before,[tuple(r) for r in self.db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id")]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM conflict").fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM activity_event").fetchone()[0])
        with self.assertRaises(ImmediateBlockingError):confirm_operation(self.db,invalid)
        self.assertEqual(before,[tuple(r) for r in self.db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id")])

    def test_preexisting_blocking_does_not_block_unrelated_immediate(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,NULL)");detect_conflicts_after_change(self.db,"alternative",2);self.db.commit()
        result=confirm_operation(self.db,grammar_operation(2,{"plural":"REDUP."},actor_context=self.actor));self.assertIsInstance(result["result"],int)

    def test_normal_review_requires_one_comment_for_new_blocking(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')");self.db.commit()
        def invalid(connection):
            connection.execute("UPDATE alternative SET working_label='1' WHERE alternative_id=2");detect_conflicts_after_change(connection,"alternative",2);return 2
        with self.assertRaisesRegex(ImmediateAcceptanceError,"Debe explicar"):run_normal_review(self.db,invalid,"")
        self.assertEqual("2",self.db.execute("SELECT working_label FROM alternative WHERE alternative_id=2").fetchone()[0])
        run_normal_review(self.db,invalid,"Aceptación temporal documentada");self.assertEqual(1,self.db.execute("SELECT count(*) FROM conflict WHERE severity='blocking'").fetchone()[0])

    def test_concept_immediate_preserves_resolved_proposal_and_no_assignment(self):
        evidence={"source_id":1,"original_gloss":"CONCEPT","occurrence_year":2002}
        operation=concept_registration_operation(evidence,"NUEVO",{"action":"new","label":"NUEVO"},actor_context=self.actor)
        preview_operation(self.db,operation);self.assertEqual(2,self.db.execute("SELECT count(*) FROM occurrence").fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM concept_proposal").fetchone()[0])
        result=confirm_operation(self.db,operation)["result"]
        proposal=self.db.execute("SELECT status,resolved_concept_id FROM concept_proposal WHERE concept_proposal_id=?",(result["concept_proposal_id"],)).fetchone();self.assertEqual(("resolved",result["concept_id"]),tuple(proposal));self.assertEqual(result["concept_proposal_id"],self.db.execute("SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=?",(result["occurrence_id"],)).fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=?",(result["occurrence_id"],)).fetchone()[0])
