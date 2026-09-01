import sqlite3
import unittest

import database
from alternative_structural import (
    StructuralAlternativeError, retire_preview, apply_retire, merge_preview,
    apply_merge, split_preview, apply_split, move_preview, apply_move,
)


class StructuralAlternativeTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON");database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',1900,1900,'known')")
        self.db.executemany("INSERT INTO concept(preferred_label) VALUES(?)",[("ORIGIN",),("DESTINATION",)])
        self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1a",),("2a",),("3a",)])
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        for index,alt in enumerate((1,1,2),1):
            self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",(f"O{index}",1900+index))
            self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)",(index,))
            self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",(index,alt))
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count_not_applicable,free_permutation) VALUES(1,1,'N/A')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,3,'MOV_M1')")
        self.db.commit();self.actor={"access_role":"reviewer","collaborator_id":None}

    def tearDown(self):self.db.close()

    def test_retire_requires_complete_resolution_and_preserves_history(self):
        with self.assertRaises(StructuralAlternativeError):retire_preview(self.db,1,{1:2})
        preview=retire_preview(self.db,1,{1:2,2:"unassigned"})
        self.assertEqual(len(preview["occurrences"]),2);self.assertIsNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        apply_retire(self.db,1,{1:2,2:"unassigned"},reason="retiro documentado",actor=self.actor)
        self.assertIsNotNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT is_current FROM alternative_morphology WHERE alternative_id=1").fetchone()[0],0)

    def test_merge_union_reassigns_deduplicates_and_keeps_target_morphology(self):
        self.db.execute("INSERT INTO alternative_morphology(alternative_id,component_count,free_permutation) VALUES(2,2,'NO')")
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(2,3,'MOV_M1')");self.db.commit()
        preview=merge_preview(self.db,1,2,"union");self.assertEqual(preview["relations_created"],[])
        apply_merge(self.db,1,2,"union",reason="misma alternativa",actor=self.actor)
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE alternative_id=2 AND is_current=1").fetchone()[0],3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id=2 AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE alternative_low_id=2 AND alternative_high_id=3 AND phonological_parameter='MOV_M1' AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='alternative_merged'").fetchone()[0],1)

    def test_split_creates_new_ids_without_morphology_or_relations(self):
        ids,event=apply_split(self.db,1,{1:1,2:2},2,reason="dos formas",actor=self.actor)
        self.assertEqual(len(ids),2);self.assertTrue(all(item>4 for item in ids))
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id IN (?,?)",ids).fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id IN (?,?) OR alternative_high_id IN (?,?))",(*ids,*ids)).fetchone()[0],0)
        labels=[r[0] for r in self.db.execute("SELECT working_label FROM alternative WHERE alternative_id IN (?,?)",ids)]
        self.assertTrue(all(label[-1:].isalpha() and label[:-1].isdigit() for label in labels))
        with self.assertRaises(StructuralAlternativeError):split_preview(self.db,2,{3:1},1)

    def test_move_preserves_identity_assignment_context_morphology(self):
        assignment=self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=1 AND is_current=1").fetchone()[0]
        context=tuple(self.db.execute("SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=1 AND is_current=1").fetchone())
        apply_move(self.db,1,2,reason="reclasificaciÃ³n",actor=self.actor)
        self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=1 AND is_current=1").fetchone()[0],assignment)
        self.assertEqual(tuple(self.db.execute("SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=1 AND is_current=1").fetchone()),context)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id=1 AND is_current=1").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],0)
        with self.assertRaises(StructuralAlternativeError):move_preview(self.db,1,2)

    def test_rollback_is_atomic(self):
        self.db.execute("CREATE TRIGGER fail_activity BEFORE INSERT ON activity_event WHEN NEW.event_type='alternative_retired' BEGIN SELECT RAISE(ABORT,'synthetic');END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):apply_retire(self.db,1,{1:2,2:2},reason="fallar",actor=self.actor)
        self.assertIsNone(self.db.execute("SELECT retired_at FROM alternative WHERE alternative_id=1").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE alternative_id=1 AND is_current=1").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1 AND (alternative_low_id=1 OR alternative_high_id=1)").fetchone()[0],2)


if __name__=="__main__":unittest.main()
