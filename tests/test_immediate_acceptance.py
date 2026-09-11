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

    def test_shared_proposal_requires_local_decision_and_never_changes_global_state(self):
        self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('C','rejected')")
        self.db.execute("UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1")
        self.db.commit()
        proposal={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology":{"component_count_not_applicable":True}}
        decision={"decision":"existing","alternative_id":1}
        before='\n'.join(self.db.iterdump())
        with self.assertRaises(ImmediateAcceptanceError):
            confirm_operation(self.db,alternative_operation(2,proposal,decision,actor_context=self.actor))
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
        decision['concept_resolution']={"action":"USE_EXISTING","concept_id":1}
        from alternative_workflow import AlternativeWorkflowError
        with self.assertRaises(AlternativeWorkflowError):
            confirm_operation(self.db,alternative_operation(2,proposal,decision,actor_context=self.actor))
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
        # Proposed groups cannot be discarded implicitly.
        decision['morphology_resolution']='REJECTED'
        result=confirm_operation(self.db,alternative_operation(2,proposal,decision,actor_context=self.actor,review_note='Existing form confirmed'))['result']
        self.assertEqual(('rejected',None),tuple(self.db.execute('SELECT status,resolved_concept_id FROM concept_proposal').fetchone()))
        self.assertEqual(1,self.db.execute('SELECT concept_id FROM submission_concept_resolution WHERE submission_id=?',(result['submission_id'],)).fetchone()[0])
        self.assertEqual(1,self.db.execute('SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=1 AND is_current=1').fetchone()[0])

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

    def test_explicit_morphology_rejection_requires_note_and_is_not_pending(self):
        from alternative_workflow import AlternativeWorkflowError
        proposal={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology":{"component_count":None,"component_count_not_applicable":True,"free_permutation":"N/A","components":[]}}
        decision={"decision":"new","approve_morphology":False,"nomenclature_mode":"automatic"}
        operation=alternative_operation(2,proposal,decision,actor_context=self.actor)
        before='\n'.join(self.db.iterdump())
        with self.assertRaises(AlternativeWorkflowError):preview_operation(self.db,operation)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
        operation=alternative_operation(2,proposal,decision,actor_context=self.actor,review_note='Morphology rejected')
        preview=preview_operation(self.db,operation)
        self.assertEqual([],preview['non_blocking']);self.assertEqual(before,'\n'.join(self.db.iterdump()))
        result=confirm_operation(self.db,operation)
        self.assertEqual([],result['non_blocking'])
        self.assertEqual('REJECTED',self.db.execute('SELECT morphology_resolution FROM submission_lexical_decision').fetchone()[0])

    def test_new_can_materialize_morphology_and_unsure_requires_decision(self):
        proposal={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology":{"component_count":None,"component_count_not_applicable":True,"free_permutation":"N/A","components":[]}}
        result=confirm_operation(self.db,alternative_operation(2,proposal,{"decision":"new","approve_morphology":True,"nomenclature_mode":"automatic"},actor_context=self.actor))["result"]
        self.assertEqual(result["submission_id"],self.db.execute("SELECT created_from_submission_id FROM alternative_morphology").fetchone()[0])
        unsure={"proposal_kind":"UNSURE","analysis_note":"Revisar"}
        with self.assertRaisesRegex(ImmediateAcceptanceError,"Es necesario definir si la propuesta se resuelve como alternativa existente o nueva"):preview_operation(self.db,alternative_operation(1,unsure,{"decision":""},actor_context=self.actor))
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
        with self.assertRaisesRegex(ImmediateAcceptanceError,"Para continuar, es necesario justificar la aprobación en la nota de revisión"):run_normal_review(self.db,invalid,"")
        self.assertEqual("2",self.db.execute("SELECT working_label FROM alternative WHERE alternative_id=2").fetchone()[0])
        run_normal_review(self.db,invalid,"Aceptación temporal documentada");self.assertEqual(1,self.db.execute("SELECT count(*) FROM conflict WHERE severity='blocking'").fetchone()[0])

    def test_concept_immediate_preserves_resolved_proposal_and_no_assignment(self):
        evidence={"source_id":1,"original_gloss":"CONCEPT","occurrence_year":2002}
        operation=concept_registration_operation(evidence,"NUEVO",{"action":"new","label":"NUEVO"},actor_context=self.actor)
        preview_operation(self.db,operation);self.assertEqual(2,self.db.execute("SELECT count(*) FROM occurrence").fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM concept_proposal").fetchone()[0])
        result=confirm_operation(self.db,operation)["result"]
        proposal=self.db.execute("SELECT status,resolved_concept_id FROM concept_proposal WHERE concept_proposal_id=?",(result["concept_proposal_id"],)).fetchone();self.assertEqual(("resolved",result["concept_id"]),tuple(proposal));self.assertEqual(result["concept_proposal_id"],self.db.execute("SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=?",(result["occurrence_id"],)).fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=?",(result["occurrence_id"],)).fetchone()[0])


    def grouped_proposal(self):
        return {"proposal_kind": "NEW", "phonological_relation_answer": "YES",
                "relations": [{"target_alternative_id": 1, "phonological_parameter": "CM_1"}],
                "morphology": {"component_count_not_applicable": True}}

    def test_explicit_groups_materialize_and_preserve_original_proposal(self):
        import copy
        proposal = self.grouped_proposal()
        original = copy.deepcopy(proposal)
        operation = alternative_operation(2, proposal, {
            "decision": "new", "relations_resolution": "ACCEPTED",
            "morphology_resolution": "ACCEPTED"}, actor_context=self.actor)
        before = '\n'.join(self.db.iterdump())
        preview_operation(self.db, operation)
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        result = confirm_operation(self.db, operation)['result']
        row = self.db.execute('SELECT * FROM submission_lexical_decision').fetchone()
        self.assertEqual(('CREATE_NEW', 'CREATED', 'ACCEPTED', 'ACCEPTED', 'C', 'Reviewer'),
                         tuple(row[k] for k in ('decision_action', 'assignment_effect',
                               'relations_resolution', 'morphology_resolution',
                               'concept_label_snapshot', 'collaborator_name_snapshot')))
        self.assertIsNotNone(row['morphology_result_id'])
        self.assertEqual(1, self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])
        self.assertEqual(('NEW', 'YES'), tuple(self.db.execute(
            'SELECT proposal_kind,phonological_relation_answer FROM alternative_submission').fetchone()))
        self.assertEqual(1, self.db.execute('SELECT count(*) FROM alternative_submission_relation').fetchone()[0])
        self.assertEqual(1, self.db.execute('SELECT count(*) FROM alternative_submission_morphology').fetchone()[0])
        self.assertEqual(original, proposal)
        snapshot = dict(row)
        self.db.execute("UPDATE concept SET preferred_label='Changed'")
        self.db.execute("UPDATE alternative SET working_label='Changed' WHERE alternative_id=?", (result['alternative_id'],))
        self.assertEqual(snapshot, dict(self.db.execute('SELECT * FROM submission_lexical_decision').fetchone()))

    def test_omitted_groups_and_rejected_groups_without_note_rollback(self):
        from alternative_workflow import AlternativeWorkflowError
        for action in ('new', 'existing'):
            for relations, morphology in ((None, None), ('REJECTED', None),
                                          (None, 'REJECTED'), ('REJECTED', 'REJECTED')):
                with self.subTest(action=action, relations=relations, morphology=morphology):
                    before = '\n'.join(self.db.iterdump())
                    operation = alternative_operation(2, self.grouped_proposal(), {
                        'decision': action, 'alternative_id': 1,
                        'relations_resolution': relations, 'morphology_resolution': morphology},
                        actor_context=self.actor)
                    with self.assertRaises(AlternativeWorkflowError):
                        confirm_operation(self.db, operation)
                    self.assertEqual(before, '\n'.join(self.db.iterdump()))

    def test_existing_rejects_groups_without_modifying_shared_canon(self):
        from alternative_workflow import AlternativeWorkflowError
        tables = ('alternative', 'alternative_relation', 'alternative_morphology')
        before = {t: [tuple(r) for r in self.db.execute('SELECT * FROM ' + t)] for t in tables}
        decision = {'decision': 'existing', 'alternative_id': 1,
                    'relations_resolution': 'REJECTED', 'morphology_resolution': 'REJECTED'}
        for override in ({'relation_policy': 'union'}, {'relations_resolution': 'ACCEPTED'},
                         {'morphology_resolution': 'ACCEPTED'}):
            dump = '\n'.join(self.db.iterdump())
            with self.assertRaises(AlternativeWorkflowError):
                confirm_operation(self.db, alternative_operation(2, self.grouped_proposal(),
                    dict(decision, **override), actor_context=self.actor, review_note='Reviewed'))
            self.assertEqual(dump, '\n'.join(self.db.iterdump()))
        confirm_operation(self.db, alternative_operation(2, self.grouped_proposal(), decision,
                          actor_context=self.actor, review_note='Reviewed'))
        self.assertEqual(before, {t: [tuple(r) for r in self.db.execute('SELECT * FROM ' + t)] for t in tables})
        self.assertEqual(('USE_EXISTING', 'REJECTED', 'REJECTED'), tuple(self.db.execute(
            'SELECT decision_action,relations_resolution,morphology_resolution FROM submission_lexical_decision').fetchone()))

    def test_changed_proposals_require_note_and_record_assignment_effect(self):
        from alternative_workflow import AlternativeWorkflowError
        cases = [('EXISTING', 'new', 1, 'REPLACED'), ('UNSURE', 'new', 2, 'CREATED'),
                 ('UNSURE', 'existing', 2, 'CREATED'), ('EXISTING', 'existing', 1, 'REUSED')]
        for kind, action, occurrence, effect in cases:
            with self.subTest(kind=kind, action=action):
                proposal = {'proposal_kind': kind, 'analysis_note': 'Uncertain',
                            'proposed_existing_alternative_id': 1 if kind == 'EXISTING' else None}
                decision = {'decision': action, 'alternative_id': 1}
                before = '\n'.join(self.db.iterdump())
                if kind == 'UNSURE' or action == 'new':
                    with self.assertRaises(AlternativeWorkflowError):
                        confirm_operation(self.db, alternative_operation(occurrence, proposal, decision, actor_context=self.actor))
                    self.assertEqual(before, '\n'.join(self.db.iterdump()))
                def checked(connection):
                    result = alternative_operation(occurrence, proposal, decision,
                        actor_context=self.actor, review_note='Reviewed')(connection)
                    row = connection.execute('SELECT * FROM submission_lexical_decision WHERE submission_id=?',
                                             (result['submission_id'],)).fetchone()
                    self.assertEqual(effect, row['assignment_effect'])
                    self.assertEqual(('NOT_PROPOSED', 'NOT_PROPOSED'), (row['relations_resolution'], row['morphology_resolution']))
                    return result
                preview_operation(self.db, checked)
                self.assertEqual(before, '\n'.join(self.db.iterdump()))

    def test_failure_after_lexical_materialization_rolls_back_entire_operation(self):
        from unittest.mock import patch
        operation = alternative_operation(2, self.grouped_proposal(), {
            'decision': 'new', 'relations_resolution': 'ACCEPTED',
            'morphology_resolution': 'ACCEPTED'}, actor_context=self.actor)
        before = '\n'.join(self.db.iterdump())
        with patch('alternative_workflow.detect_conflicts_after_change', side_effect=RuntimeError('late failure')):
            with self.assertRaisesRegex(RuntimeError, 'late failure'):
                confirm_operation(self.db, operation)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(before, '\n'.join(self.db.iterdump()))


    def test_new_rejected_relations_and_morphology_do_not_materialize(self):
        result = confirm_operation(self.db, alternative_operation(2, self.grouped_proposal(), {
            'decision': 'new', 'relations_resolution': 'REJECTED',
            'morphology_resolution': 'REJECTED'}, actor_context=self.actor,
            review_note='Reject proposed groups'))
        self.assertEqual([], result['non_blocking'])
        for table in ('alternative_relation', 'alternative_morphology'):
            self.assertEqual(0, self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0])
        self.assertEqual(('REJECTED', 'REJECTED'), tuple(self.db.execute(
            'SELECT relations_resolution,morphology_resolution FROM submission_lexical_decision').fetchone()))

    def test_existing_a_to_b_requires_note_and_replaces_assignment(self):
        from alternative_workflow import AlternativeWorkflowError
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')")
        self.db.commit()
        proposal = {'proposal_kind': 'EXISTING', 'proposed_existing_alternative_id': 1}
        decision = {'decision': 'existing', 'alternative_id': 2}
        before = '\n'.join(self.db.iterdump())
        with self.assertRaises(AlternativeWorkflowError):
            confirm_operation(self.db, alternative_operation(1, proposal, decision, actor_context=self.actor))
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        confirm_operation(self.db, alternative_operation(1, proposal, decision,
                          actor_context=self.actor, review_note='Switch to B'))
        row = self.db.execute('SELECT * FROM submission_lexical_decision').fetchone()
        self.assertEqual(('REPLACED', 1, 2), (row['assignment_effect'], row['assignment_before_id'], row['resolved_alternative_id']))
        self.assertNotEqual(row['assignment_before_id'], row['assignment_result_id'])
        self.assertEqual(1, self.db.execute('SELECT proposed_existing_alternative_id FROM alternative_submission').fetchone()[0])
