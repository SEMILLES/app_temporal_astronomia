import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from alternative_nomenclature import (
    InconclusiveNomenclatureError, InvalidNomenclatureError,
    apply_nomenclature, calculate_nomenclature_preview, temporal_reference,
)
from alternative_workflow import (
    AlternativeWorkflowError, create_alternative_submission,
    reject_alternative_submission, review_as_existing, review_as_new,
)


class AlternativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"db.sqlite"
        self.db=sqlite3.connect(self.path); self.db.row_factory=sqlite3.Row; self.db.execute("PRAGMA foreign_keys=ON"); database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S1',2000,2000,'known')")
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S2',1990,1995,'range')")
        self.db.executemany("INSERT INTO concept(preferred_label) VALUES(?)",[("ONE",),("TWO",)])
        for gloss,source,year,concept in (("A",1,2001,1),("B",1,2002,1),("C",2,None,1),("OTHER",1,2003,2),("NEW",1,1999,1)):
            oid=self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(?,?,?)",(source,gloss,year)).lastrowid
            self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,?)",(oid,concept))
        self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1",),("2",),("3",)])
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1')")
        for oid,aid in ((1,1),(2,2),(3,3),(4,4)): self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",(oid,aid))
        self.db.commit()

    def tearDown(self): self.db.close(); self.tmp.cleanup()

    def create(self, occurrence=5, kind="NEW", **kwargs):
        if kind == "NEW" and "morphology" not in kwargs:
            kwargs["morphology"]={"component_count_not_applicable":True}
        return create_alternative_submission(self.db,occurrence,kind,**kwargs)

    def test_existing_validation(self):
        sid=self.create(kind="EXISTING",proposed_existing_alternative_id=1); self.assertIsInstance(sid,int)
        self.db.execute("UPDATE submission SET status='resolved',resolution='rejected' WHERE submission_id=?",(sid,)); self.db.commit()
        for kwargs in ({}, {"proposed_existing_alternative_id":4}):
            with self.assertRaises(AlternativeWorkflowError): self.create(kind="EXISTING",**kwargs)
        self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1"); self.db.commit()
        with self.assertRaises(AlternativeWorkflowError): self.create(kind="EXISTING",proposed_existing_alternative_id=1)

    def test_new_relation_rules_and_duplicate_pending(self):
        with self.assertRaises(AlternativeWorkflowError): self.create(phonological_relation_answer="YES")
        relation={"target_alternative_id":1,"phonological_parameter":"CM_1"}
        sid=self.create(phonological_relation_answer="YES",relations=[relation]); self.assertIsInstance(sid,int)
        with self.assertRaises(AlternativeWorkflowError): self.create(phonological_relation_answer="NO")

    def test_relation_target_must_share_context(self):
        with self.assertRaises(AlternativeWorkflowError):
            self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":4,"phonological_parameter":"CM_1"}])

    def test_new_no_and_unsure_answers(self):
        for answer in ("NO","UNSURE"):
            sid=self.create(phonological_relation_answer=answer)
            self.db.execute("UPDATE submission SET status='resolved',resolution='rejected' WHERE submission_id=?",(sid,)); self.db.commit()

    def test_unsure_requires_note(self):
        with self.assertRaises(AlternativeWorkflowError): self.create(kind="UNSURE")
        self.assertIsInstance(self.create(kind="UNSURE",analysis_note="Necesita revisión"),int)

    def test_pending_target_and_relation_uniqueness(self):
        target=self.create(occurrence=1,phonological_relation_answer="NO")
        relation=lambda parameter:{"target_submission_id":target,"phonological_parameter":parameter}
        sid=self.create(relations=[relation("CM_1"),relation("CM_2")],phonological_relation_answer="UNSURE")
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_submission_relation WHERE submission_id=?",(sid,)).fetchone()[0],2)
        self.db.execute("UPDATE submission SET status='resolved',resolution='rejected' WHERE submission_id=?",(sid,)); self.db.commit()
        with self.assertRaises(AlternativeWorkflowError): self.create(relations=[relation("CM_1"),relation("CM_1")],phonological_relation_answer="UNSURE")

    def test_invalid_or_self_target_submission(self):
        grammar=self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'GRAMMAR','pending')").lastrowid; self.db.execute("INSERT INTO grammar_submission(submission_id,gender) VALUES(?,'FEM-A')",(grammar,)); self.db.commit()
        with self.assertRaises(AlternativeWorkflowError): self.create(relations=[{"target_submission_id":grammar,"phonological_parameter":"CM_1"}],phonological_relation_answer="UNSURE")
        predicted=self.db.execute("SELECT seq+1 FROM sqlite_sequence WHERE name='submission'").fetchone()[0]
        with self.assertRaises(AlternativeWorkflowError): self.create(relations=[{"target_submission_id":predicted,"phonological_parameter":"CM_1"}],phonological_relation_answer="UNSURE")

    def test_review_existing_versions_assignment_and_provenance(self):
        sid=self.create(kind="EXISTING",proposed_existing_alternative_id=1); resolved=review_as_existing(self.db,sid,2)
        self.assertEqual(resolved,2); current=self.db.execute("SELECT * FROM assignment WHERE occurrence_id=5 AND is_current=1").fetchone()
        self.assertEqual((current["alternative_id"],current["created_from_submission_id"]),(2,sid)); sub=self.db.execute("SELECT s.status,s.resolution,a.resolved_alternative_id FROM submission s JOIN alternative_submission a USING(submission_id) WHERE submission_id=?",(sid,)).fetchone(); self.assertEqual(tuple(sub),("resolved","accepted",2))

    def test_review_existing_supersedes_previous(self):
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(5,1)"); self.db.commit(); old=self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=5").fetchone()[0]
        sid=self.create(kind="EXISTING",proposed_existing_alternative_id=2); review_as_existing(self.db,sid,2); rows=self.db.execute("SELECT * FROM assignment WHERE occurrence_id=5 ORDER BY assignment_id").fetchall()
        self.assertEqual([r["is_current"] for r in rows],[0,1]); self.assertEqual(rows[1]["supersedes_assignment_id"],old)

    def test_reject_changes_no_canonical_state(self):
        sid=self.create(phonological_relation_answer="NO"); counts=[self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("alternative","assignment","alternative_relation")]
        reject_alternative_submission(self.db,sid,review_note="No"); self.assertEqual([self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("alternative","assignment","alternative_relation")],counts); self.assertIsNone(self.db.execute("SELECT resolved_alternative_id FROM alternative_submission WHERE submission_id=?",(sid,)).fetchone()[0])

    def test_review_existing_relation_preserve_union_duplicate_and_unresolved(self):
        relation={"target_alternative_id":2,"phonological_parameter":"CM_1"}; sid=self.create(phonological_relation_answer="YES",relations=[relation]); review_as_existing(self.db,sid,1,relation_policy="preserve"); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],0)
        sid=self.create(phonological_relation_answer="YES",relations=[relation]); review_as_existing(self.db,sid,1,relation_policy="union"); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],1)
        sid=self.create(phonological_relation_answer="YES",relations=[relation]); review_as_existing(self.db,sid,1,relation_policy="union"); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],1)
        target=self.create(occurrence=1,phonological_relation_answer="NO"); sid=self.create(relations=[{"target_submission_id":target,"phonological_parameter":"CM_2"}],phonological_relation_answer="YES")
        with self.assertRaises(AlternativeWorkflowError): review_as_existing(self.db,sid,1,relation_policy="union")

    def test_self_relation_on_review_is_rejected(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}])
        with self.assertRaises(AlternativeWorkflowError): review_as_existing(self.db,sid,1,relation_policy="union")

    def test_relation_target_retired_after_submission_blocks_materialization(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":2,"phonological_parameter":"CM_1"}]); self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=2"); self.db.commit()
        with self.assertRaises(AlternativeWorkflowError): review_as_existing(self.db,sid,1,relation_policy="union")

    def test_review_new_creates_label_assignment_event_and_no_relation_default(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}])
        new=review_as_new(self.db,sid,approve_relations=False,nomenclature_mode="automatic")
        row=self.db.execute("SELECT concept_id,working_label,retired_at,original_code FROM alternative WHERE alternative_id=?",(new,)).fetchone(); self.assertEqual(row[0],1); self.assertTrue(row[1]); self.assertIsNone(row[2]); self.assertIsNone(row[3])
        self.assertEqual(self.db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=5 AND is_current=1").fetchone()[0],new); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],0); self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],1)

    def test_review_new_approved_relation_and_rollback(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}]); new=review_as_new(self.db,sid,approve_relations=True,nomenclature_mode="automatic"); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE alternative_low_id=? OR alternative_high_id=?",(new,new)).fetchone()[0],1)
        sid=self.create(phonological_relation_answer="NO"); before=self.db.execute("SELECT count(*) FROM alternative").fetchone()[0]; self.db.execute("CREATE TRIGGER fail_resolve BEFORE UPDATE ON submission WHEN NEW.status='resolved' BEGIN SELECT RAISE(ABORT,'synthetic'); END"); self.db.commit()
        labels=dict(self.db.execute("SELECT alternative_id,working_label FROM alternative WHERE concept_id=1 AND retired_at IS NULL")); labels[new+1]="9"
        with self.assertRaises(sqlite3.IntegrityError): review_as_new(self.db,sid,nomenclature_mode="manual",labels=labels,reason="Manual")
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative").fetchone()[0],before); self.assertEqual(self.db.execute("SELECT status FROM submission WHERE submission_id=?",(sid,)).fetchone()[0],"pending")

    def test_concept_proposal_resolution_new_existing_and_independence(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('THREE','pending')").lastrowid
        for oid in (5,1): self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=?",(oid,)); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(?,?)",(oid,proposal))
        self.db.commit(); sid=self.create(phonological_relation_answer="NO"); new=review_as_new(self.db,sid,concept_resolution={"action":"new"},nomenclature_mode="automatic")
        resolved=self.db.execute("SELECT status,resolved_concept_id FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone(); self.assertEqual(resolved[0],"resolved"); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],resolved[1]); self.assertEqual(self.db.execute("SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=5 AND is_current=1").fetchone()[0],proposal); self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=1 AND created_from_submission_id=?",(sid,)).fetchone()[0],0)

    def test_pending_concept_can_resolve_existing_and_cannot_accept_unresolved(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('P','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO")
        with self.assertRaises(AlternativeWorkflowError): review_as_new(self.db,sid,nomenclature_mode="manual",labels={},reason="x")
        new=review_as_new(self.db,sid,concept_resolution={"action":"existing","concept_id":1},nomenclature_mode="automatic"); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],1)

    def test_concept_proposal_can_be_rejected_only_with_explicit_canonical_context(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('REJECT-ME','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO")
        with self.assertRaises(AlternativeWorkflowError): review_as_new(self.db,sid,concept_resolution={"action":"reject"},nomenclature_mode="automatic")
        new=review_as_new(self.db,sid,concept_resolution={"action":"reject","concept_id":1},nomenclature_mode="automatic"); self.assertEqual(self.db.execute("SELECT status FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone()[0],"rejected"); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],1)

    def test_reject_submission_does_not_incidentally_resolve_concept_proposal(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('STAY-PENDING','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO"); reject_alternative_submission(self.db,sid); self.assertEqual(self.db.execute("SELECT status FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone()[0],"pending")


class NomenclatureTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:"); self.db.row_factory=sqlite3.Row; self.db.execute("PRAGMA foreign_keys=ON"); database.crear_esquema(self.db)
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        self.db.executemany("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES(?,?,?,?)",[("single",2000,2000,"known"),("range",1990,1995,"range"),("none",None,None,None)])
    def tearDown(self): self.db.close()
    def add(self,year,source=1,label=None):
        oid=self.db.execute("INSERT INTO occurrence(source_id,occurrence_year) VALUES(?,?)",(source,year)).lastrowid; aid=self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",(label,)).lastrowid; self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",(oid,aid)); self.db.commit(); return aid,oid
    def test_temporal_priority_and_fallback(self):
        self.assertEqual(temporal_reference(2005,2000,2000,"known"),(2005,"occurrence_year")); self.assertEqual(temporal_reference(None,2000,2000,"known"),(2000,"source_single_year")); self.assertEqual(temporal_reference(None,1990,1995,"range"),(1990,"source_range_start"))
    def test_connected_components_and_no_transitive_insert(self):
        a,_=self.add(2000);b,_=self.add(2001);c,_=self.add(2002); self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,?)",(a,b,"CM_1"));self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,?)",(b,c,"CM_1"));self.db.commit(); p=calculate_nomenclature_preview(self.db,1);self.assertEqual(set(p["suggestions"].values()),{"1a","1b","1c"});self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],2)
    def test_tie_missing_and_ids_do_not_break_ties(self):
        self.add(2000);self.add(2000);p=calculate_nomenclature_preview(self.db,1);self.assertFalse(p["conclusive"]);self.assertIn("Empate", " ".join(p["problems"]))
        self.db.execute("DELETE FROM assignment");self.db.execute("DELETE FROM alternative");self.db.commit();self.add(None,source=3);self.assertFalse(calculate_nomenclature_preview(self.db,1)["conclusive"])
    def test_extra_edge_joins_groups_and_affects_whole_concept(self):
        a,_=self.add(2000,label="1");b,_=self.add(2001,label="2");c,_=self.add(2002,label="3");p=calculate_nomenclature_preview(self.db,1,extra_edges=[(a,c)]);self.assertEqual(p["suggestions"],{a:"1a",c:"1b",b:"2"})

    def test_virtual_new_alternative_preview_does_not_write(self):
        a,_=self.add(2000,label="1"); oid=self.db.execute("INSERT INTO occurrence(source_id,occurrence_year) VALUES(1,2001)").lastrowid; self.db.commit(); before=self.db.execute("SELECT count(*) FROM alternative").fetchone()[0]
        preview=calculate_nomenclature_preview(self.db,1,extra_edges=[("new",a)],virtual_occurrences={"new":oid})
        self.assertEqual(preview["suggestions"],{a:"1a","new":"1b"}); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative").fetchone()[0],before)
    def test_manual_reason_duplicate_event_and_source_year_not_written(self):
        a,oid=self.add(None,source=1,label="9");b,_=self.add(2001,label="8")
        with self.assertRaises(InvalidNomenclatureError): apply_nomenclature(self.db,1,{a:"1",b:"2"},origin="manual")
        with self.assertRaises(InvalidNomenclatureError): apply_nomenclature(self.db,1,{a:"1",b:"1"},origin="manual",reason="x")
        event=apply_nomenclature(self.db,1,{a:"1",b:"2"},origin="manual",reason="Cronología");self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],1);self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_change WHERE renumber_event_id=?",(event,)).fetchone()[0],2);self.assertIsNone(self.db.execute("SELECT occurrence_year FROM occurrence WHERE occurrence_id=?",(oid,)).fetchone()[0])

    def test_manual_labels_must_preserve_connected_group(self):
        a,_=self.add(2000,label="1");b,_=self.add(2001,label="2")
        with self.assertRaises(InvalidNomenclatureError):
            apply_nomenclature(self.db,1,{a:"1",b:"2"},origin="manual",reason="No agrupa",required_edges=[(a,b)])
        apply_nomenclature(self.db,1,{a:"1a",b:"1b"},origin="manual",reason="Agrupación explícita",required_edges=[(a,b)])


if __name__=="__main__": unittest.main()
