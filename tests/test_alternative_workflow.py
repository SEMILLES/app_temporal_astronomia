from submission_concept_resolution import save_resolution, current_resolution
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
        self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1a",),("2a",),("3a",)])
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        for oid,aid in ((1,1),(2,2),(3,3),(4,4)): self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",(oid,aid))
        self.db.commit()

    def tearDown(self): self.db.close(); self.tmp.cleanup()

    def create(self, occurrence=5, kind="NEW", **kwargs):
        if kind == "NEW" and "morphology" not in kwargs:
            kwargs["morphology"]={"component_count_not_applicable":True}
        sid = create_alternative_submission(self.db,occurrence,kind,**kwargs)
        if self.db.execute("SELECT reference_concept_id FROM alternative_submission WHERE submission_id=?",(sid,)).fetchone()[0] is not None:
            save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer')
        return sid

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
        sid=self.create(kind="EXISTING",proposed_existing_alternative_id=1); resolved=review_as_existing(self.db,sid,2, access_role="reviewer", review_note="Revision documentada")
        self.assertEqual(resolved,2); current=self.db.execute("SELECT * FROM assignment WHERE occurrence_id=5 AND is_current=1").fetchone()
        self.assertEqual((current["alternative_id"],current["created_from_submission_id"]),(2,sid)); sub=self.db.execute("SELECT s.status,s.resolution,a.resolved_alternative_id FROM submission s JOIN alternative_submission a USING(submission_id) WHERE submission_id=?",(sid,)).fetchone(); self.assertEqual(tuple(sub),("resolved","accepted",2))

    def test_review_existing_supersedes_previous(self):
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(5,1)"); self.db.commit(); old=self.db.execute("SELECT assignment_id FROM assignment WHERE occurrence_id=5").fetchone()[0]
        sid=self.create(kind="EXISTING",proposed_existing_alternative_id=2); review_as_existing(self.db,sid,2, access_role="reviewer", review_note="Revision documentada"); rows=self.db.execute("SELECT * FROM assignment WHERE occurrence_id=5 ORDER BY assignment_id").fetchall()
        self.assertEqual([r["is_current"] for r in rows],[0,1]); self.assertEqual(rows[1]["supersedes_assignment_id"],old)

    def test_reject_changes_no_canonical_state(self):
        sid=self.create(phonological_relation_answer="NO"); counts=[self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("alternative","assignment","alternative_relation")]
        reject_alternative_submission(self.db,sid,review_note="No", access_role="reviewer"); self.assertEqual([self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("alternative","assignment","alternative_relation")],counts); self.assertIsNone(self.db.execute("SELECT resolved_alternative_id FROM alternative_submission WHERE submission_id=?",(sid,)).fetchone()[0])

    def test_review_existing_relation_preserve_requires_explicit_rejection_and_no_union(self):
        relation={"target_alternative_id":2,"phonological_parameter":"CM_1"}
        sid=self.create(phonological_relation_answer="YES",relations=[relation])
        with self.assertRaises(AlternativeWorkflowError):
            review_as_existing(self.db,sid,1,relation_policy="union",access_role="reviewer")
        with self.assertRaises(AlternativeWorkflowError):
            review_as_existing(self.db,sid,1,access_role="reviewer",review_note="Reason")
        review_as_existing(self.db,sid,1,access_role="reviewer",review_note="Reason",
                           relations_resolution="REJECTED",morphology_resolution="REJECTED")
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],0)

    def test_self_relation_on_review_is_rejected(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}])
        with self.assertRaises(AlternativeWorkflowError): review_as_existing(self.db,sid,1,relation_policy="union", access_role="reviewer", review_note="Revision documentada")

    def test_relation_target_retired_after_submission_blocks_materialization(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":2,"phonological_parameter":"CM_1"}]); self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=2"); self.db.commit()
        with self.assertRaises(AlternativeWorkflowError): review_as_existing(self.db,sid,1,relation_policy="union", access_role="reviewer", review_note="Revision documentada")

    def test_review_new_creates_label_assignment_event_and_no_relation_default(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}])
        new=review_as_new(self.db,sid,approve_relations=False,nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada")
        row=self.db.execute("SELECT concept_id,working_label,retired_at,original_code FROM alternative WHERE alternative_id=?",(new,)).fetchone(); self.assertEqual(row[0],1); self.assertTrue(row[1]); self.assertIsNone(row[2]); self.assertIsNone(row[3])
        self.assertEqual(self.db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=5 AND is_current=1").fetchone()[0],new); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],0); self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],1)

    def test_review_new_approved_relation_and_rollback(self):
        sid=self.create(phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}]); new=review_as_new(self.db,sid,approve_relations=True,nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada"); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE alternative_low_id=? OR alternative_high_id=?",(new,new)).fetchone()[0],1)
        sid=self.create(phonological_relation_answer="NO"); before=self.db.execute("SELECT count(*) FROM alternative").fetchone()[0]; self.db.execute("CREATE TRIGGER fail_resolve BEFORE UPDATE ON submission WHEN NEW.status='resolved' BEGIN SELECT RAISE(ABORT,'synthetic'); END"); self.db.commit()
        labels=dict(self.db.execute("SELECT alternative_id,working_label FROM alternative WHERE concept_id=1 AND retired_at IS NULL")); labels[new+1]="9a"
        with self.assertRaises(sqlite3.IntegrityError): review_as_new(self.db,sid,nomenclature_mode="manual",labels=labels,reason="Manual", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada")
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative").fetchone()[0],before); self.assertEqual(self.db.execute("SELECT status FROM submission WHERE submission_id=?",(sid,)).fetchone()[0],"pending")

    def test_concept_proposal_resolution_new_existing_and_independence(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('THREE','pending')").lastrowid
        for oid in (5,1): self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=?",(oid,)); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(?,?)",(oid,proposal))
        self.db.commit(); sid=self.create(phonological_relation_answer="NO"); save_resolution(self.db,sid,"CREATE_NEW",label="THREE",access_role="reviewer"); new=review_as_new(self.db,sid,nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada")
        resolved=self.db.execute("SELECT status,resolved_concept_id FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone(); self.assertEqual(tuple(resolved),("pending",None)); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],current_resolution(self.db,sid)["concept_id"]); self.assertIsNone(self.db.execute("SELECT concept_proposal_id FROM occurrence_concept_reference WHERE occurrence_id=5 AND is_current=1").fetchone()[0]); self.assertEqual(self.db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=1 AND created_from_submission_id=?",(sid,)).fetchone()[0],0)

    def test_pending_concept_can_resolve_existing_and_cannot_accept_unresolved(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('P','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO")
        with self.assertRaises(AlternativeWorkflowError): review_as_new(self.db,sid,nomenclature_mode="manual",labels={},reason="x", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada")
        save_resolution(self.db,sid,"USE_EXISTING",concept_id=1,note="Identificación revisada",access_role="reviewer"); new=review_as_new(self.db,sid,nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada"); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],1)

    def test_global_reject_action_is_replaced_by_local_existing_resolution(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('REJECT-ME','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO")
        with self.assertRaises(AlternativeWorkflowError): review_as_new(self.db,sid,concept_resolution={"action":"reject"},nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada")
        save_resolution(self.db,sid,"USE_EXISTING",concept_id=1,note="Identificación revisada",access_role="reviewer"); new=review_as_new(self.db,sid,nomenclature_mode="automatic", access_role="reviewer", morphology_resolution="ACCEPTED", review_note="Revision documentada"); self.assertEqual(self.db.execute("SELECT status FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone()[0],"pending"); self.assertEqual(self.db.execute("SELECT concept_id FROM alternative WHERE alternative_id=?",(new,)).fetchone()[0],1)

    def test_reject_submission_does_not_incidentally_resolve_concept_proposal(self):
        proposal=self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('STAY-PENDING','pending')").lastrowid; self.db.execute("UPDATE occurrence_concept_reference SET is_current=0 WHERE occurrence_id=5"); self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_proposal_id) VALUES(5,?)",(proposal,)); self.db.commit(); sid=self.create(phonological_relation_answer="NO"); save_resolution(self.db,sid,"USE_EXISTING",concept_id=1,note="Identificación revisada",access_role="reviewer"); reject_alternative_submission(self.db,sid, access_role="reviewer", review_note="Revision documentada"); self.assertEqual(self.db.execute("SELECT status FROM concept_proposal WHERE concept_proposal_id=?",(proposal,)).fetchone()[0],"pending")


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
    def test_tie_and_missing_use_stable_registration_order(self):
        a,_=self.add(2000);b,_=self.add(2000);p=calculate_nomenclature_preview(self.db,1);self.assertTrue(p["conclusive"]);self.assertEqual(p["suggestions"],{a:"1a",b:"2a"})
        self.db.execute("DELETE FROM assignment");self.db.execute("DELETE FROM alternative");self.db.commit();a,_=self.add(None,source=3);b,_=self.add(None,source=3);self.assertEqual(calculate_nomenclature_preview(self.db,1)["suggestions"],{a:"1a",b:"2a"})
    def test_extra_edge_joins_groups_and_affects_whole_concept(self):
        a,_=self.add(2000,label="1a");b,_=self.add(2001,label="2a");c,_=self.add(2002,label="3a");p=calculate_nomenclature_preview(self.db,1,extra_edges=[(a,c)]);self.assertEqual(p["suggestions"],{a:"1a",c:"1b",b:"2a"})

    def test_virtual_new_alternative_preview_does_not_write(self):
        a,_=self.add(2000,label="1a"); oid=self.db.execute("INSERT INTO occurrence(source_id,occurrence_year) VALUES(1,2001)").lastrowid; self.db.commit(); before=self.db.execute("SELECT count(*) FROM alternative").fetchone()[0]
        preview=calculate_nomenclature_preview(self.db,1,extra_edges=[("new",a)],virtual_occurrences={"new":oid})
        self.assertEqual(preview["suggestions"],{a:"1a","new":"1b"}); self.assertEqual(self.db.execute("SELECT count(*) FROM alternative").fetchone()[0],before)

    def test_singleton_groups_always_keep_letters(self):
        a,_=self.add(2000,label="1a");b,_=self.add(2001,label="2a")
        preview=calculate_nomenclature_preview(self.db,1)
        self.assertEqual(preview["suggestions"],{a:"1a",b:"2a"})
        self.assertEqual(preview,calculate_nomenclature_preview(self.db,1))
        self.assertIsNone(apply_nomenclature(self.db,1,preview["suggestions"],origin="automatic_assisted"))

    def test_created_at_then_id_break_ties_between_groups(self):
        a,_=self.add(2000);b,_=self.add(2000)
        self.db.execute("UPDATE alternative SET created_at='2025-01-02 00:00:00' WHERE alternative_id=?",(a,))
        self.db.execute("UPDATE alternative SET created_at='2025-01-01 00:00:00' WHERE alternative_id=?",(b,));self.db.commit()
        self.assertEqual(calculate_nomenclature_preview(self.db,1)["suggestions"],{b:"1a",a:"2a"})
        self.db.execute("UPDATE alternative SET created_at='2025-01-01 00:00:00'");self.db.commit()
        self.assertEqual(calculate_nomenclature_preview(self.db,1)["suggestions"],{a:"1a",b:"2a"})
        self.db.execute("UPDATE alternative SET created_at=CASE alternative_id WHEN ? THEN 'invalid-z' ELSE 'invalid-a' END",(a,));self.db.commit()
        self.assertEqual(calculate_nomenclature_preview(self.db,1)["suggestions"],{a:"1a",b:"2a"})

    def test_registration_order_breaks_tie_inside_group(self):
        a,_=self.add(2000);b,_=self.add(2000)
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,?)",(a,b,"CM_1"));self.db.commit()
        self.assertEqual(calculate_nomenclature_preview(self.db,1)["suggestions"],{a:"1a",b:"1b"})
    def test_manual_reason_duplicate_event_and_source_year_not_written(self):
        a,oid=self.add(None,source=1,label="9a");b,_=self.add(2001,label="8a")
        with self.assertRaises(InvalidNomenclatureError): apply_nomenclature(self.db,1,{a:"1a",b:"2a"},origin="manual")
        with self.assertRaises(InvalidNomenclatureError): apply_nomenclature(self.db,1,{a:"1a",b:"1a"},origin="manual",reason="x")
        with self.assertRaises(InvalidNomenclatureError): apply_nomenclature(self.db,1,{a:"1",b:"2a"},origin="manual",reason="Sin letra")
        event=apply_nomenclature(self.db,1,{a:"1a",b:"2a"},origin="manual",reason="Cronología");self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],1);self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_change WHERE renumber_event_id=?",(event,)).fetchone()[0],2);self.assertIsNone(self.db.execute("SELECT occurrence_year FROM occurrence WHERE occurrence_id=?",(oid,)).fetchone()[0])

    def test_manual_labels_must_preserve_connected_group(self):
        a,_=self.add(2000,label="1a");b,_=self.add(2001,label="2a")
        with self.assertRaises(InvalidNomenclatureError):
            apply_nomenclature(self.db,1,{a:"1",b:"2"},origin="manual",reason="No agrupa",required_edges=[(a,b)])
        apply_nomenclature(self.db,1,{a:"1a",b:"1b"},origin="manual",reason="Agrupación explícita",required_edges=[(a,b)])


if __name__=="__main__": unittest.main()


class FinalLexicalWorkflowTests(unittest.TestCase):
    setUp = AlternativeWorkflowTests.setUp
    tearDown = AlternativeWorkflowTests.tearDown
    create = AlternativeWorkflowTests.create

    def existing(self, sid, target=1, **kwargs):
        return review_as_existing(self.db, sid, target, access_role='reviewer', **kwargs)

    def reject(self, sid, **kwargs):
        return reject_alternative_submission(self.db, sid, access_role='reviewer', **kwargs)

    def decision(self, sid):
        return self.db.execute('SELECT * FROM submission_lexical_decision WHERE submission_id=?',(sid,)).fetchone()

    def dump(self):
        return '\n'.join(self.db.iterdump())

    def canonical(self):
        tables=('alternative','alternative_relation','alternative_morphology','alternative_component','renumber_event','renumber_change','occurrence_concept_reference','submission_concept_resolution')
        return {t:[tuple(r) for r in self.db.execute(f'SELECT * FROM {t}')] for t in tables}

    def proposal(self, sid):
        row=dict(self.db.execute('SELECT * FROM alternative_submission WHERE submission_id=?',(sid,)).fetchone())
        row.pop('resolved_alternative_id')
        return (row, *[[tuple(r) for r in self.db.execute(f'SELECT * FROM {t} WHERE submission_id=?',(sid,))] for t in ('alternative_submission_relation','alternative_submission_morphology','alternative_submission_component')])

    def test_existing_same_reuses_original_provenance_without_note(self):
        sid=self.create(occurrence=1,kind='EXISTING',proposed_existing_alternative_id=1)
        before=tuple(self.db.execute('SELECT * FROM assignment WHERE assignment_id=1').fetchone())
        proposal=self.proposal(sid)
        self.existing(sid,review_note=' \t ')
        self.assertEqual(before,tuple(self.db.execute('SELECT * FROM assignment WHERE assignment_id=1').fetchone()))
        row=self.decision(sid)
        self.assertEqual(('USE_EXISTING',1,1,'REUSED','NOT_PROPOSED','NOT_PROPOSED'),tuple(row[k] for k in ('decision_action','assignment_before_id','assignment_result_id','assignment_effect','relations_resolution','morphology_resolution')))
        self.assertEqual(proposal,self.proposal(sid))

    def test_existing_different_requires_note_and_replaces(self):
        sid=self.create(occurrence=1,kind='EXISTING',proposed_existing_alternative_id=1)
        before=self.dump()
        with self.assertRaises(AlternativeWorkflowError):self.existing(sid,2,review_note=' \n ')
        self.assertEqual(before,self.dump())
        self.existing(sid,2,review_note='Otra forma')
        row=self.decision(sid)
        self.assertEqual('REPLACED',row['assignment_effect'])
        self.assertEqual(1,row['assignment_before_id'])
        self.assertNotEqual(1,row['assignment_result_id'])

    def test_new_and_unsure_require_note(self):
        for kind,oid in [('NEW',5),('UNSURE',1)]:
            sid=self.create(occurrence=oid,kind=kind,phonological_relation_answer='NO',analysis_note='Duda')
            options={'morphology_resolution':'REJECTED'} if kind=='NEW' else {}
            before=self.dump()
            with self.assertRaises(AlternativeWorkflowError):self.existing(sid,**options)
            self.assertEqual(before,self.dump())
            self.existing(sid,review_note='Forma existente',**options)
            self.assertEqual('USE_EXISTING',self.decision(sid)['decision_action'])

    def test_no_assignment_creates(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        self.existing(sid)
        row=self.decision(sid)
        self.assertEqual('CREATED',row['assignment_effect'])
        self.assertIsNone(row['assignment_before_id'])
        self.assertIsNotNone(row['assignment_result_id'])

    def test_wrong_concept_retired_missing_and_role_rejected_without_effects(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=2");self.db.commit()
        before=self.dump()
        for target in (4,2,999):
            with self.subTest(target=target),self.assertRaises(AlternativeWorkflowError):self.existing(sid,target,review_note='Change')
            self.assertEqual(before,self.dump())
        for role in ('analyst',None):
            with self.assertRaises(AlternativeWorkflowError):review_as_existing(self.db,sid,1,access_role=role)
            self.assertEqual(before,self.dump())

    def test_proposed_groups_rejected_preserve_canonical_destination(self):
        self.db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,3,'CM_1')")
        self.db.execute('INSERT INTO alternative_morphology(alternative_id) VALUES(1)');self.db.commit()
        sid=self.create(phonological_relation_answer='YES',relations=[{'target_alternative_id':2,'phonological_parameter':'CM_1'}])
        canon=self.canonical();proposal=self.proposal(sid)
        with self.assertRaises(AlternativeWorkflowError):self.existing(sid,relations_resolution='REJECTED',morphology_resolution='REJECTED')
        self.existing(sid,relations_resolution='REJECTED',morphology_resolution='REJECTED',review_note='Conservar destino')
        self.assertEqual(canon,self.canonical())
        self.assertEqual(proposal,self.proposal(sid))
        self.assertEqual(('REJECTED','REJECTED'),tuple(self.decision(sid)[k] for k in ('relations_resolution','morphology_resolution')))

    def test_each_omitted_group_and_union_leave_pending_with_no_effects(self):
        sid=self.create(phonological_relation_answer='YES',relations=[{'target_alternative_id':2,'phonological_parameter':'CM_1'}])
        before=self.dump()
        for options in ({},{'relations_resolution':'REJECTED'},{'morphology_resolution':'REJECTED'},
                        {'relations_resolution':'ACCEPTED','morphology_resolution':'REJECTED'},
                        {'relations_resolution':'REJECTED','morphology_resolution':'ACCEPTED'},
                        {'relation_policy':'union','relations_resolution':'REJECTED','morphology_resolution':'REJECTED'}):
            with self.subTest(options=options),self.assertRaises(AlternativeWorkflowError):self.existing(sid,review_note='Reason',**options)
            self.assertEqual(before,self.dump())
            self.assertIsNone(self.decision(sid))

    def test_reject_rest_preserves_assignment_concept_and_all_canonical_state(self):
        sid=self.create(occurrence=1,phonological_relation_answer='YES',relations=[{'target_alternative_id':2,'phonological_parameter':'CM_1'}])
        canon=self.canonical();proposal=self.proposal(sid)
        assignments=[tuple(r) for r in self.db.execute('SELECT * FROM assignment')]
        self.reject(sid,review_note=' Resto rechazado ')
        row=self.decision(sid)
        self.assertEqual(('REJECT_REST',1,1,'UNCHANGED','REJECTED','REJECTED'),tuple(row[k] for k in ('decision_action','assignment_before_id','assignment_result_id','assignment_effect','relations_resolution','morphology_resolution')))
        self.assertIsNone(row['resolved_alternative_id']);self.assertIsNone(row['alternative_label_snapshot'])
        self.assertEqual(canon,self.canonical());self.assertEqual(proposal,self.proposal(sid))
        self.assertEqual(assignments,[tuple(r) for r in self.db.execute('SELECT * FROM assignment')])
        self.assertEqual(('resolved','rejected','Resto rechazado'),tuple(self.db.execute('SELECT status,resolution,review_note FROM submission WHERE submission_id=?',(sid,)).fetchone()))

    def test_reject_without_assignment_or_groups_and_note_required(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        before=self.dump()
        for note in (None,'',' \t\n'):
            with self.assertRaises(AlternativeWorkflowError):self.reject(sid,review_note=note)
            self.assertEqual(before,self.dump())
        self.reject(sid,review_note='No')
        row=self.decision(sid)
        self.assertEqual((None,None,'UNCHANGED','NOT_PROPOSED','NOT_PROPOSED'),tuple(row[k] for k in ('assignment_before_id','assignment_result_id','assignment_effect','relations_resolution','morphology_resolution')))

    def test_insert_failure_rolls_back_both_paths_and_keeps_saved_concept(self):
        sid=self.create(occurrence=1,kind='EXISTING',proposed_existing_alternative_id=1)
        self.db.execute("CREATE TRIGGER fail_lexical BEFORE INSERT ON submission_lexical_decision BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        before=self.dump()
        for operation in (lambda:self.existing(sid,2,review_note='Change'),lambda:self.reject(sid,review_note='No')):
            with self.assertRaises(sqlite3.IntegrityError):operation()
            self.assertEqual(before,self.dump())
            self.assertIsNotNone(current_resolution(self.db,sid))
        self.db.execute('BEGIN')
        self.db.execute("UPDATE source SET source_name='Outer' WHERE source_id=1")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.existing(sid,2,review_note='Change')
        self.assertTrue(self.db.in_transaction);self.assertEqual(before,self.dump());self.db.rollback()

    def test_closure_failure_rolls_back_decision_and_assignment(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        self.db.execute("CREATE TRIGGER fail_close BEFORE UPDATE ON submission WHEN NEW.status='resolved' BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        before=self.dump()
        for operation in (lambda:self.existing(sid),lambda:self.reject(sid,review_note='No')):
            with self.assertRaises(sqlite3.IntegrityError):operation()
            self.assertEqual(before,self.dump())

    def test_double_attempt_does_not_duplicate_either_path(self):
        for oid,reject in [(1,False),(5,True)]:
            sid=self.create(occurrence=oid,kind='EXISTING',proposed_existing_alternative_id=1)
            operation=(lambda:self.reject(sid,review_note='No')) if reject else (lambda:self.existing(sid))
            operation();before=self.dump()
            with self.assertRaises(AlternativeWorkflowError):operation()
            self.assertEqual(before,self.dump())

    def test_new_blocking_conflict_requires_note_and_rolls_back(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        self.db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=2");self.db.commit()
        before=self.dump()
        from unittest.mock import patch
        from conflicts import detect_conflicts_after_change
        def detect_blocking(connection, *args, **kwargs):
            return detect_conflicts_after_change(connection, 'alternative', 2)
        with patch('alternative_workflow.detect_conflicts_after_change', side_effect=detect_blocking):
            with self.assertRaises(AlternativeWorkflowError):self.existing(sid)
            self.assertEqual(before,self.dump())
            self.existing(sid,review_note='Aceptacion documentada del conflicto')
        self.assertIsNotNone(self.decision(sid))
        self.assertGreater(self.db.execute("SELECT count(*) FROM conflict WHERE severity='blocking'").fetchone()[0],0)


class CreateNewLexicalWorkflowTests(unittest.TestCase):
    setUp = AlternativeWorkflowTests.setUp
    tearDown = AlternativeWorkflowTests.tearDown
    create = AlternativeWorkflowTests.create
    dump = FinalLexicalWorkflowTests.dump
    proposal = FinalLexicalWorkflowTests.proposal
    decision = FinalLexicalWorkflowTests.decision

    def new(self, sid, **kwargs):
        return review_as_new(self.db, sid, access_role='reviewer', **kwargs)

    def full_proposal(self, occurrence=5):
        return self.create(occurrence=occurrence,phonological_relation_answer='YES',
            relations=[{'target_alternative_id':1,'phonological_parameter':'CM_1'}],
            morphology={'component_count':2,'free_permutation':'NO',
                        'components':[{'position':1,'component_alternative_id':2}]})

    def test_legacy_new_without_groups_optional_note_and_final_snapshot(self):
        sid=self.create(phonological_relation_answer='NO')
        self.db.execute('DELETE FROM alternative_submission_morphology WHERE submission_id=?',(sid,))
        self.db.execute('UPDATE alternative_submission SET is_legacy=1 WHERE submission_id=?',(sid,));self.db.commit()
        before=self.proposal(sid)
        aid=self.new(sid,review_note=' \n ')
        row=self.decision(sid)
        label=self.db.execute('SELECT working_label FROM alternative WHERE alternative_id=?',(aid,)).fetchone()[0]
        self.assertEqual(('CREATE_NEW','CREATED',None,'NOT_PROPOSED','NOT_PROPOSED',label),tuple(row[k] for k in ('decision_action','assignment_effect','assignment_before_id','relations_resolution','morphology_resolution','alternative_label_snapshot')))
        self.assertEqual(before,self.proposal(sid))
        self.db.execute("UPDATE alternative SET working_label='99z' WHERE alternative_id=?",(aid,))
        self.assertEqual(label,self.decision(sid)['alternative_label_snapshot'])

    def test_accepted_groups_materialize_with_provenance_and_group_nomenclature(self):
        sid=self.full_proposal();before=self.proposal(sid)
        aid=self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED')
        row=self.decision(sid)
        self.assertEqual(('ACCEPTED','ACCEPTED'),tuple(row[k] for k in ('relations_resolution','morphology_resolution')))
        relation=self.db.execute('SELECT * FROM alternative_relation WHERE created_from_submission_id=?',(sid,)).fetchone()
        self.assertEqual({1,aid},{relation['alternative_low_id'],relation['alternative_high_id']})
        morphology=self.db.execute('SELECT * FROM alternative_morphology WHERE alternative_morphology_id=?',(row['morphology_result_id'],)).fetchone()
        self.assertEqual((aid,sid,2),(morphology['alternative_id'],morphology['created_from_submission_id'],morphology['component_count']))
        self.assertEqual(1,self.db.execute('SELECT count(*) FROM alternative_component WHERE alternative_morphology_id=?',(row['morphology_result_id'],)).fetchone()[0])
        labels=dict(self.db.execute('SELECT alternative_id,working_label FROM alternative'))
        self.assertEqual(labels[aid][:-1],labels[1][:-1]);self.assertNotEqual(labels[aid],labels[1])
        self.assertEqual(labels[aid],row['alternative_label_snapshot'])
        self.assertEqual(before,self.proposal(sid))
        self.assertEqual(aid,self.db.execute('SELECT resolved_alternative_id FROM alternative_submission WHERE submission_id=?',(sid,)).fetchone()[0])

    def test_rejected_relations_need_review_note_and_do_not_group(self):
        sid=self.full_proposal();before=self.dump()
        with self.assertRaises(AlternativeWorkflowError):
            self.new(sid,relations_resolution='REJECTED',morphology_resolution='ACCEPTED',reason='Nomenclature reason only',review_note=' \t ')
        self.assertEqual(before,self.dump())
        aid=self.new(sid,relations_resolution='REJECTED',morphology_resolution='ACCEPTED',review_note='No relation')
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])
        labels=dict(self.db.execute('SELECT alternative_id,working_label FROM alternative'))
        self.assertNotEqual(labels[aid][:-1],labels[1][:-1])
        self.assertEqual('REJECTED',self.decision(sid)['relations_resolution'])

    def test_unresolved_groups_produce_no_writes(self):
        sid=self.full_proposal();before=self.dump()
        from unittest.mock import patch
        for options in ({},{'relations_resolution':'ACCEPTED'},{'morphology_resolution':'ACCEPTED'}):
            with self.subTest(options=options),patch('alternative_workflow.apply_nomenclature') as nomenclature:
                with self.assertRaises(AlternativeWorkflowError):self.new(sid,review_note='Reason',**options)
                nomenclature.assert_not_called()
                self.assertEqual(before,self.dump())
        self.assertIsNone(self.decision(sid))

    def test_explicit_morphology_rejection_no_pending_conflict(self):
        from conflict_rules import detect_pending_morphology
        from conflicts import run_global_conflict_validation
        sid=self.create(phonological_relation_answer='NO');before=self.dump()
        with self.assertRaises(AlternativeWorkflowError):self.new(sid,morphology_resolution='REJECTED')
        self.assertEqual(before,self.dump())
        aid=self.new(sid,morphology_resolution='REJECTED',review_note='Morphology rejected')
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM alternative_morphology WHERE alternative_id=?',(aid,)).fetchone()[0])
        self.assertIsNone(self.decision(sid)['morphology_result_id'])
        self.assertEqual([],detect_pending_morphology(self.db))
        run_global_conflict_validation(self.db)
        self.assertEqual(0,self.db.execute("SELECT count(*) FROM conflict WHERE rule_code='PENDING_MORPHOLOGY'").fetchone()[0])

    def test_existing_and_unsure_to_new_without_morphology_require_note(self):
        for kind,oid in [('EXISTING',5),('UNSURE',1)]:
            options={'proposed_existing_alternative_id':1} if kind=='EXISTING' else {'analysis_note':'Uncertain'}
            sid=self.create(occurrence=oid,kind=kind,**options);before=self.dump()
            with self.assertRaises(AlternativeWorkflowError):self.new(sid)
            self.assertEqual(before,self.dump())
            aid=self.new(sid,review_note='Different form')
            row=self.decision(sid)
            self.assertEqual('NOT_PROPOSED',row['morphology_resolution']);self.assertIsNone(row['morphology_result_id'])
            self.assertEqual(0,self.db.execute('SELECT count(*) FROM alternative_morphology WHERE alternative_id=?',(aid,)).fetchone()[0])
            self.assertEqual('CREATED' if oid==5 else 'REPLACED',row['assignment_effect'])
            if oid==1:
                self.assertEqual(1,row['assignment_before_id']);self.assertNotEqual(1,row['assignment_result_id'])

    def test_legacy_existing_with_real_morphology_proposal(self):
        sid=self.create(kind='EXISTING',proposed_existing_alternative_id=1)
        self.db.execute('UPDATE alternative_submission SET is_legacy=1 WHERE submission_id=?',(sid,))
        self.db.execute("INSERT INTO alternative_submission_morphology(submission_id,component_count,free_permutation) VALUES(?,1,'N/A')",(sid,));self.db.commit()
        before=self.proposal(sid)
        self.new(sid,morphology_resolution='ACCEPTED',review_note='New form from legacy proposal')
        self.assertIsNotNone(self.decision(sid)['morphology_result_id']);self.assertEqual(before,self.proposal(sid))

    def test_saved_local_concept_is_used(self):
        sid=self.create(phonological_relation_answer='NO')
        resolution=save_resolution(self.db,sid,'USE_EXISTING',concept_id=2,note='Other concept',access_role='reviewer')
        aid=self.new(sid,morphology_resolution='ACCEPTED')
        row=self.decision(sid)
        self.assertEqual((2,resolution,'TWO'),tuple(row[k] for k in ('concept_id_at_decision','concept_resolution_id','concept_label_snapshot')))
        self.assertEqual(2,self.db.execute('SELECT concept_id FROM alternative WHERE alternative_id=?',(aid,)).fetchone()[0])

    def test_failure_at_each_materialization_step_rolls_back_everything(self):
        sid=self.full_proposal(occurrence=1)
        for table in ('submission_lexical_decision','alternative_morphology','alternative_component','alternative_relation','renumber_change','activity_event'):
            with self.subTest(table=table):
                self.db.execute(f"CREATE TRIGGER fail_step BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
                before=self.dump()
                with self.assertRaises(sqlite3.IntegrityError):
                    self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED')
                self.assertEqual(before,self.dump())
                self.assertIsNotNone(current_resolution(self.db,sid))
                self.db.execute('DROP TRIGGER fail_step');self.db.commit()

    def test_closure_and_conflict_failure_roll_back_decision_and_results(self):
        sid=self.full_proposal();options={'relations_resolution':'ACCEPTED','morphology_resolution':'ACCEPTED'}
        self.db.execute("CREATE TRIGGER fail_close BEFORE UPDATE ON submission WHEN NEW.status='resolved' BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.new(sid,**options)
        self.assertEqual(before,self.dump())
        self.db.execute('DROP TRIGGER fail_close');self.db.commit();before=self.dump()
        from unittest.mock import patch
        with patch('alternative_workflow.detect_conflicts_after_change',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.new(sid,**options)
        self.assertEqual(before,self.dump())

    def test_outer_savepoint_keeps_prior_work_on_failure(self):
        sid=self.full_proposal()
        self.db.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON submission_lexical_decision BEGIN SELECT RAISE(ABORT,'synthetic'); END");self.db.commit()
        self.db.execute('BEGIN');self.db.execute("UPDATE source SET source_name='Outer' WHERE source_id=1")
        before=self.dump()
        with self.assertRaises(sqlite3.IntegrityError):self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED')
        self.assertTrue(self.db.in_transaction);self.assertEqual(before,self.dump());self.db.rollback()
        self.assertIsNotNone(current_resolution(self.db,sid))

    def test_double_attempt_does_not_duplicate_results(self):
        sid=self.full_proposal()
        self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED');before=self.dump()
        with self.assertRaises(AlternativeWorkflowError):self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED')
        self.assertEqual(before,self.dump())

    def test_invalid_and_contradictory_group_decisions_are_rejected(self):
        sid=self.full_proposal();before=self.dump()
        for options in ({'relations_resolution':'NOT_PROPOSED'}, {'relations_resolution':'ACCEPTED','approve_relations':False}, {'approve_relations':'yes'}):
            with self.assertRaises(AlternativeWorkflowError):self.new(sid,morphology_resolution='ACCEPTED',**options)
            self.assertEqual(before,self.dump())

    def test_retired_or_unresolved_relation_target_rolls_back_when_accepted(self):
        sid=self.full_proposal()
        self.db.execute('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1');self.db.commit()
        before=self.dump()
        with self.assertRaises(AlternativeWorkflowError):self.new(sid,relations_resolution='ACCEPTED',morphology_resolution='ACCEPTED')
        self.assertEqual(before,self.dump())
        self.new(sid,relations_resolution='REJECTED',morphology_resolution='ACCEPTED',review_note='Retired target rejected')

    def test_reviewer_role_required_without_canonical_effects(self):
        sid=self.create(phonological_relation_answer='NO');before=self.dump()
        for role in ('analyst',None):
            with self.assertRaises(AlternativeWorkflowError):review_as_new(self.db,sid,morphology_resolution='ACCEPTED',access_role=role)
            self.assertEqual(before,self.dump())

    def test_blocking_conflict_requires_review_note_not_nomenclature_reason(self):
        from unittest.mock import patch
        from conflicts import detect_conflicts_after_change
        sid=self.create(phonological_relation_answer='NO')
        # A blocking finding produced during the lexical close must be justified.
        def blocking_detector(connection,*args,**kwargs):
            connection.execute("UPDATE alternative SET working_label='duplicate' WHERE alternative_id IN (1,2)")
            return detect_conflicts_after_change(connection,'alternative',1)
        before=self.dump()
        with patch('alternative_workflow.detect_conflicts_after_change',side_effect=blocking_detector):
            with self.assertRaises(AlternativeWorkflowError):self.new(sid,morphology_resolution='ACCEPTED',reason='Nomenclature only')
            self.assertEqual(before,self.dump())
            self.new(sid,morphology_resolution='ACCEPTED',review_note='Documented conflict')
        self.assertGreater(self.db.execute("SELECT count(*) FROM conflict WHERE severity='blocking'").fetchone()[0],0)
