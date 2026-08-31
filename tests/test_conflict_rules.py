import sqlite3
import unittest
from database import crear_esquema
from conflict_rules import RULES, detect_all

class ConflictRuleTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('S')");self.db.execute("INSERT INTO concept(preferred_label) VALUES('C')")
        for gloss in ("uno","dos"):
            self.db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,?)",(gloss,))
    def tearDown(self):self.db.close()
    def codes(self):return {f.rule_code for f in detect_all(self.db)}
    def test_duplicate_missing_assignment_and_relation_rules(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')");self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')")
        self.assertIn("DUPLICATE_WORKING_LABEL",self.codes())
        self.db.execute("UPDATE alternative SET working_label=NULL WHERE alternative_id=2")
        self.assertIn("MISSING_WORKING_LABEL_ACTIVE_ALTERNATIVE",self.codes())
        self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1")
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'vocal')")
        codes=self.codes();self.assertIn("CURRENT_ASSIGNMENT_TO_RETIRED_ALTERNATIVE",codes);self.assertIn("ACTIVE_RELATION_TO_RETIRED_ALTERNATIVE",codes)
    def test_invalid_group_and_narrow_pending_morphology(self):
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')");self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'vocal')")
        self.assertIn("INVALID_PHONOLOGICAL_GROUP_LABELING",self.codes())
        self.assertNotIn("PENDING_MORPHOLOGY",self.codes())
        self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status,resolution,resolved_at) VALUES(1,'ALTERNATIVE','resolved','accepted',CURRENT_TIMESTAMP)")
        self.db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id,phonological_relation_answer,resolved_alternative_id,is_legacy) VALUES(1,'NEW',1,'NO',1,0)")
        self.db.execute("INSERT INTO alternative_submission_morphology(submission_id,component_count,component_count_not_applicable,free_permutation) VALUES(1,NULL,1,'N/A')")
        self.assertIn("PENDING_MORPHOLOGY",self.codes())
        self.assertEqual("non_blocking",RULES["PENDING_MORPHOLOGY"].severity)
