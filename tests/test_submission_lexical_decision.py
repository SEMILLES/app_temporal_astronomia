import sqlite3
import unittest

import database
from submission_lexical_decision import save_decision, get_decision, LexicalDecisionError, requires_review_note


class LexicalFixture(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('S')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('X')")
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Reviewer')")
        for i in (1, 2):
            self.db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'G')")
            self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)", (str(i),))
            self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(?,'ALTERNATIVE','pending')", (i,))
            self.db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id,proposed_existing_alternative_id) VALUES(?,'EXISTING',1,?)", (i,i))
            self.db.execute("INSERT INTO submission_concept_resolution(submission_id,concept_id,resolution_action,access_role) VALUES(?,1,'CONFIRM_REFERENCE','reviewer')", (i,))
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def save(self, **kwargs):
        values = dict(concept_resolution_id=1, assignment_effect='REUSED', relations_resolution='NOT_PROPOSED', morphology_resolution='NOT_PROPOSED', resolved_alternative_id=1, assignment_before_id=1, assignment_result_id=1, collaborator_id=1, access_role='reviewer')
        values.update(kwargs)
        return save_decision(self.db, 1, values.pop('decision_action', 'USE_EXISTING'), **values)

    def dump(self):
        return '\n'.join(self.db.iterdump())


class LexicalDecisionTests(LexicalFixture):
    def test_existing_snapshots_proposal_and_pending_unchanged(self):
        proposal = tuple(self.db.execute('SELECT * FROM alternative_submission WHERE submission_id=1').fetchone())
        self.save(review_note=' \t ')
        row = dict(get_decision(self.db,1))
        self.assertEqual(('X','X-1','Reviewer'), tuple(row[k] for k in ('concept_label_snapshot','alternative_label_snapshot','collaborator_name_snapshot')))
        self.db.execute("UPDATE concept SET preferred_label='Y'")
        self.db.execute("UPDATE alternative SET working_label='9'")
        self.db.execute("UPDATE collaborator SET display_name='Changed'")
        self.assertEqual(row, dict(get_decision(self.db,1)))
        self.assertEqual(proposal, tuple(self.db.execute('SELECT * FROM alternative_submission WHERE submission_id=1').fetchone()))
        self.assertEqual('pending',self.db.execute('SELECT status FROM submission WHERE submission_id=1').fetchone()[0])

    def test_create_new_without_proposed_morphology(self):
        self.db.execute('UPDATE assignment SET is_current=0 WHERE assignment_id=1')
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,2)')
        self.db.commit()
        self.save(decision_action='CREATE_NEW', assignment_effect='REPLACED', assignment_result_id=2, resolved_alternative_id=2, review_note='Nueva forma')
        self.assertEqual('CREATE_NEW',get_decision(self.db,1)['decision_action'])

    def test_reject_rest(self):
        self.save(decision_action='REJECT_REST',assignment_effect='UNCHANGED',resolved_alternative_id=None,review_note='Rechazo')
        self.assertIsNone(get_decision(self.db,1)['alternative_label_snapshot'])

    def test_second_decision_rejected(self):
        self.save()
        before=self.dump()
        with self.assertRaises(LexicalDecisionError): self.save()
        self.assertEqual(before,self.dump())

    def test_role_and_invalid_enums(self):
        for field,value in [('access_role','analyst'),('decision_action','draft'),('assignment_effect','union'),('relations_resolution','bad'),('morphology_resolution','bad')]:
            with self.subTest(field=field), self.assertRaises(LexicalDecisionError): self.save(**{field:value})

    def test_nonlexical_and_closed(self):
        for sql in ["UPDATE submission SET submission_type='GRAMMAR' WHERE submission_id=1", "UPDATE submission SET submission_type='ALTERNATIVE',status='resolved',resolution='accepted' WHERE submission_id=1"]:
            self.db.execute(sql); self.db.commit()
            with self.assertRaises(LexicalDecisionError): self.save()

    def test_foreign_historical_and_missing_resolution(self):
        for identifier in (2,999):
            with self.assertRaises(LexicalDecisionError): self.save(concept_resolution_id=identifier)
        self.db.execute('UPDATE submission_concept_resolution SET is_current=0 WHERE submission_id=1');self.db.commit()
        with self.assertRaises(LexicalDecisionError): self.save()

    def test_note_rules(self):
        for kind, original, action, result, relations, morphology, expected in [
            ('NEW',None,'USE_EXISTING',1,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('EXISTING',1,'USE_EXISTING',2,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('EXISTING',1,'CREATE_NEW',2,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('UNSURE',None,'CREATE_NEW',2,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('UNSURE',None,'USE_EXISTING',2,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('NEW',None,'REJECT_REST',None,'NOT_PROPOSED','NOT_PROPOSED',True),
            ('NEW',None,'CREATE_NEW',2,'REJECTED','NOT_PROPOSED',True),
            ('NEW',None,'CREATE_NEW',2,'NOT_PROPOSED','REJECTED',True),
            ('NEW',None,'CREATE_NEW',2,'ACCEPTED','ACCEPTED',False),
            ('EXISTING',1,'USE_EXISTING',1,'NOT_PROPOSED','NOT_PROPOSED',False)]:
            with self.subTest(kind=kind,action=action,result=result):
                self.assertEqual(expected,requires_review_note(kind,original,action,result,relations,morphology))

    def test_whitespace_note_rejected(self):
        for kind in ('NEW','UNSURE'):
            self.db.execute('UPDATE alternative_submission SET proposal_kind=?,proposed_existing_alternative_id=NULL WHERE submission_id=1',(kind,));self.db.commit()
            with self.assertRaises(LexicalDecisionError): self.save(review_note=' \n\t')

    def test_group_resolution_matches_proposal(self):
        with self.assertRaises(LexicalDecisionError): self.save(relations_resolution='REJECTED',review_note='Reason')
        self.db.execute("INSERT INTO alternative_submission_morphology(submission_id) VALUES(1)");self.db.commit()
        with self.assertRaises(LexicalDecisionError): self.save()
        with self.assertRaises(LexicalDecisionError): self.save(morphology_resolution='REJECTED',review_note=' ')
        self.save(morphology_resolution='REJECTED',review_note='Reason')

    def test_rollback_insertion_and_outer_transaction(self):
        self.db.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON submission_lexical_decision BEGIN SELECT RAISE(ABORT,'failure'); END")
        self.db.commit()
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError): self.save()
        self.assertEqual(before,self.dump())
        self.db.execute('BEGIN')
        self.db.execute("UPDATE concept SET preferred_label='Outer'")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError): self.save()
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(before,self.dump())
        self.db.rollback()
        self.db.execute('DROP TRIGGER fail_decision');self.db.commit()
        before=self.dump()
        self.db.execute('BEGIN');self.save();self.db.rollback()
        self.assertEqual(before,self.dump())

    def test_result_ownership(self):
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(2,2)');self.db.commit()
        with self.assertRaises(LexicalDecisionError): self.save(assignment_result_id=2)
        with self.assertRaises(LexicalDecisionError): self.save(collaborator_id=999)

    def test_created_assignment_and_accepted_morphology(self):
        self.db.execute("UPDATE alternative_submission SET proposal_kind='NEW',proposed_existing_alternative_id=NULL WHERE submission_id=1")
        self.db.execute('DELETE FROM assignment')
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,2)')
        self.db.execute('INSERT INTO alternative_submission_morphology(submission_id) VALUES(1)')
        self.db.execute('INSERT INTO alternative_morphology(alternative_id) VALUES(2)')
        self.db.commit()
        self.save(decision_action='CREATE_NEW',resolved_alternative_id=2,assignment_before_id=None,
                  assignment_result_id=2,assignment_effect='CREATED',morphology_resolution='ACCEPTED',morphology_result_id=1)
        self.assertEqual(1,get_decision(self.db,1)['morphology_result_id'])

    def test_reject_without_assignment(self):
        self.db.execute('DELETE FROM assignment');self.db.commit()
        self.save(decision_action='REJECT_REST',resolved_alternative_id=None,
                  assignment_before_id=None,assignment_result_id=None,assignment_effect='UNCHANGED',review_note='Rejected')
        self.assertIsNone(get_decision(self.db,1)['assignment_result_id'])

    def test_reject_cannot_hide_current_assignment(self):
        with self.assertRaises(LexicalDecisionError):
            self.save(decision_action='REJECT_REST',resolved_alternative_id=None,
                      assignment_before_id=None,assignment_result_id=None,assignment_effect='UNCHANGED',review_note='Rejected')
