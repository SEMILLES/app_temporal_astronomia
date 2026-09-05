import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

import database
from catalog_projection import build_catalog_projection
from catalog_publication import publish_catalog
from source_forms import SOURCE_FIELDS
from source_structural import (SourceStructuralError, active_source, apply_distribution,
                               preview_distribution, source_template)
from tests.test_source_retirement import seed
from tests import test_collaboration_activity as roles


def split(mode="keep", distribution=None, new_sources=None):
    return {"kind":"split", "mode":mode, "source_ids":[1],
            "distribution": distribution or {"1":"source:1", "2":"source:2", "3":"source:2"},
            "new_sources":new_sources or {}}


def merge(template=None, form=None):
    return {"kind":"merge", "source_ids":[1,2], "template_source_id":template,
            "new_sources":{"new:1":form or {"source_name":"Merged", "source_type":"OTRO"}}}


class SourceDistributionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:"); self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON"); database.crear_esquema(self.db); seed(self.db)
        self.db.execute("INSERT INTO source(source_name,source_type) VALUES('Third','OTRO')")
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year,source_detail_1_status,source_detail_2_status) VALUES(2,'B evidence',2020,'UNKNOWN','NA')")
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(4,1)")
        self.db.commit()

    def tearDown(self): self.db.close()

    def apply(self, spec, role="reviewer", reason="Reviewed structural operation", **kwargs):
        plan = preview_distribution(self.db, spec)
        return apply_distribution(self.db, spec, fingerprint=plan["fingerprint"], reason=reason,
                                  actor={"access_role":role}, **kwargs)

    def row(self, sql): return tuple(self.db.execute(sql).fetchone())

    def table(self, name): return [dict(r) for r in self.db.execute(f"SELECT * FROM {name}")]

    def assert_invariants(self):
        self.assertEqual(self.row("SELECT count(*) FROM occurrence WHERE source_id IS NULL"), (0,))
        self.assertEqual(self.row("SELECT count(*) FROM occurrence o JOIN source s USING(source_id) WHERE s.retired_at IS NOT NULL"), (0,))
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.row("PRAGMA integrity_check"), ("ok",))

    def test_split_keep_and_replace_by_both_roles(self):
        for mode in ("keep", "replace"):
            for role in ("reviewer", "master"):
                with self.subTest(mode=mode, role=role):
                    spec = split(mode, {"1":"source:1" if mode=="keep" else "source:3", "2":"source:2", "3":"source:2"})
                    fixture = sqlite3.connect(":memory:"); fixture.row_factory = sqlite3.Row
                    self.db.backup(fixture)
                    plan = preview_distribution(fixture, spec)
                    apply_distribution(fixture, spec, fingerprint=plan["fingerprint"], reason="Split", actor={"access_role":role})
                    self.assertEqual(fixture.execute("SELECT retired_at IS NOT NULL FROM source WHERE source_id=1").fetchone()[0], mode=="replace")
                    self.assertEqual(fixture.execute("SELECT count(*) FROM occurrence WHERE source_id=1").fetchone()[0], 1 if mode=="keep" else 0)
                    self.assertEqual(fixture.execute("SELECT count(*) FROM occurrence").fetchone()[0], 4)
                    fixture.close()

    def test_merge_by_both_roles_retires_both_and_creates_new(self):
        for role in ("reviewer", "master"):
            with self.subTest(role=role):
                fixture = sqlite3.connect(":memory:"); fixture.row_factory=sqlite3.Row; self.db.backup(fixture)
                spec = merge(); plan = preview_distribution(fixture, spec)
                apply_distribution(fixture, spec, fingerprint=plan["fingerprint"], reason="Merge", actor={"access_role":role})
                self.assertEqual(fixture.execute("SELECT count(*) FROM source WHERE retired_at IS NOT NULL").fetchone()[0], 2)
                self.assertEqual(fixture.execute("SELECT count(*) FROM occurrence WHERE source_id IN (1,2)").fetchone()[0], 0)
                self.assertEqual(fixture.execute("SELECT count(*) FROM occurrence WHERE source_id=4").fetchone()[0], 4)
                self.assertEqual(fixture.execute("SELECT analyst_protected FROM source WHERE source_id=4").fetchone()[0], 1)
                fixture.close()

    def test_analyst_and_whitespace_reason_rejected_for_both(self):
        for spec in (split(),merge()):
            for reason in (None,"","  \t "):
                with self.subTest(kind=spec["kind"],reason=reason), self.assertRaises(SourceStructuralError): self.apply(spec,reason=reason)
            with self.assertRaises(SourceStructuralError): self.apply(spec,role="analyst")
        self.assertEqual(len(self.table("occurrence_revision")),0)

    def test_split_requires_changes_complete_single_assignment_and_two_replace_targets(self):
        invalid = [split(distribution={"1":"source:1","2":"source:1","3":"source:1"}),
                   split(distribution={"1":"source:2"}),
                   split(distribution={"1":"source:2","2":"","3":"source:1"}),
                   split(distribution={"1":["source:1","source:2"],"2":"source:2","3":"source:2"}),
                   split(distribution={"1":"source:2","2":"source:2","3":"source:2","4":"source:2"}),
                   split("replace",{"1":"source:2","2":"source:2","3":"source:2"}),
                   split("replace"),split("invalid")]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(SourceStructuralError): preview_distribution(self.db,spec)

    def test_retired_origins_or_destinations_rejected(self):
        self.db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP WHERE source_id=3"); self.db.commit()
        with self.assertRaises(SourceStructuralError): preview_distribution(self.db,split(distribution={"1":"source:3","2":"source:2","3":"source:2"}))
        for kind in ("split","merge"):
            spec=split() if kind=="split" else merge(); spec["source_ids"]=[3] if kind=="split" else [1,3]
            with self.assertRaises(SourceStructuralError): preview_distribution(self.db,spec)

    def test_merge_duplicate_origins_existing_target_and_bad_template_rejected(self):
        for ids in ([1,1],[1,"1"],[1],[1,2,3]):
            spec=merge();spec["source_ids"]=ids
            with self.subTest(ids=ids),self.assertRaises(SourceStructuralError): preview_distribution(self.db,spec)
        spec=merge();spec["new_sources"]={"source:2":{}}
        with self.assertRaises(SourceStructuralError): preview_distribution(self.db,spec)
        with self.assertRaises(SourceStructuralError): preview_distribution(self.db,merge(3))

    def test_split_details_statuses_identity_grammar_assignments_and_unmoved_row_unchanged(self):
        original=self.table("occurrence")
        related={t:self.table(t) for t in ("occurrence_grammar","assignment","alternative")}
        self.apply(split())
        current=self.table("occurrence"); history={r["occurrence_id"]:r for r in self.table("occurrence_revision")}
        self.assertEqual(current[0],original[0]);self.assertEqual(current[3],original[3])
        self.assertEqual(set(history),{2,3})
        for before,after in zip(original,current):
            for field,value in before.items():
                if field not in ("source_id","updated_at"):self.assertEqual(after[field],value)
                if before["occurrence_id"] in history and field in history[before["occurrence_id"]]: self.assertEqual(history[before["occurrence_id"]][field],value)
        for table,rows in related.items():self.assertEqual(self.table(table),rows)
        self.assert_invariants()

    def test_merge_preserves_all_evidence_and_revisions_of_both_origins(self):
        original=self.table("occurrence");related={t:self.table(t) for t in ("occurrence_grammar","assignment","alternative")}
        self.apply(merge())
        history={r["occurrence_id"]:r for r in self.table("occurrence_revision")}
        self.assertEqual(set(history),{1,2,3,4})
        for before,after in zip(original,self.table("occurrence")):
            for field,value in before.items():
                if field not in ("source_id","updated_at"):self.assertEqual(after[field],value)
                if field in history[before["occurrence_id"]]:self.assertEqual(history[before["occurrence_id"]][field],value)
        for table,rows in related.items():self.assertEqual(self.table(table),rows)
        self.assert_invariants()

    def test_template_copies_all_metadata_and_is_editable_without_parent_schema(self):
        self.db.execute("UPDATE source SET start_year=2018,end_year=2025,end_year_status='known',region_description='Region',characterization='Description',format_original='Video',format_detail='Detail',source_reference='Reference',reported_entry_count=100 WHERE source_id=1");self.db.commit()
        form=source_template(active_source(self.db,1));self.assertEqual(set(form),set(SOURCE_FIELDS))
        form.update(source_name="Copied",region_description="Edited region")
        spec=split(new_sources={"new:1":form},distribution={"1":"source:1","2":"new:1","3":"new:1"})
        self.apply(spec)
        self.assertEqual(self.row("SELECT source_type,start_year,end_year,region_description,characterization,format_original,format_detail,source_reference,reported_entry_count,analyst_protected FROM source WHERE source_id=4"), ("OTRO",2018,2025,"Edited region","Description","Video","Detail","Reference",100,1))
        self.assertNotIn("parent_source_id",{r[1] for r in self.db.execute("PRAGMA table_info(source)")})

    def test_merge_templates_a_b_and_blank_with_edits(self):
        for template_id in (1,2,None):
            form=source_template(active_source(self.db,template_id)) if template_id else {"source_type":"OTRO"}
            form.update(source_name="New final",source_reference="Edited reference")
            plan=preview_distribution(self.db,merge(template_id,form))
            self.assertEqual(plan["groups"][0]["target"]["source_reference"],"Edited reference")
            self.assertEqual(plan["groups"][0]["target"]["source_type"],"MATERIAL_IMPRESO" if template_id==2 else "OTRO")
        self.apply(merge(2,form={**source_template(active_source(self.db,2)),"source_name":"Merged"}))
        self.assertEqual(json.loads(self.table("activity_event")[-1]["comment"])["template_source_id"],2)

    def test_new_targets_require_valid_unique_used_names(self):
        for form in ({"source_name":"New"},{"source_type":"OTRO"},{"source_name":"Origin","source_type":"OTRO"}):
            with self.assertRaises(SourceStructuralError):preview_distribution(self.db,merge(form=form))
        form={"source_name":"New","source_type":"OTRO"}
        with self.assertRaises(SourceStructuralError):preview_distribution(self.db,split(new_sources={"new:1":form}))
        with self.assertRaises(SourceStructuralError):preview_distribution(self.db,split(new_sources={"new:1":form,"new:2":form},distribution={"1":"new:1","2":"new:2","3":"source:1"}))

    def test_preview_is_read_only_and_type_divergence_warns(self):
        before=list(self.db.iterdump());changes=self.db.total_changes
        for spec in (split(),merge()):
            plan=preview_distribution(self.db,spec)
            self.assertTrue(any(g["different_types"] for g in plan["groups"]))
            if spec["kind"]=="merge":self.assertIn("source_type",plan["divergent_fields"])
        self.assertEqual(changes,self.db.total_changes);self.assertEqual(before,list(self.db.iterdump()))
        self.assertFalse(self.db.in_transaction)

    def test_split_conflicts_resolved_independently_per_destination(self):
        self.db.execute("UPDATE source SET start_year=2021,end_year=2023,end_year_status='known' WHERE source_id IN (2,3)");self.db.commit()
        spec=split("replace",{"1":"source:2","2":"source:3","3":"source:3"})
        plan=preview_distribution(self.db,spec)
        self.assertEqual([len(g["conflicts"]) for g in plan["groups"]],[1,1])
        with self.assertRaises(SourceStructuralError):self.apply(spec,resolutions={"source:2":"expand"})
        self.apply(spec,resolutions={"source:2":"expand","source:3":"clear"})
        self.assertEqual(self.row("SELECT start_year,end_year FROM source WHERE source_id=2"),(2019,2023))
        self.assertEqual([r[0] for r in self.db.execute("SELECT occurrence_year FROM occurrence ORDER BY occurrence_id")],[2019,2022,None,2020])
        self.assertEqual(self.row("SELECT count(*) FROM source_revision WHERE source_id=2"),(1,))
        self.assert_invariants()

    def test_merge_period_expand_or_clear_only_outside(self):
        form={"source_name":"Merged","source_type":"OTRO","start_year":"2021","end_year":"2023","end_year_status":"known"}
        for choice in ("expand","clear"):
            fixture=sqlite3.connect(":memory:");fixture.row_factory=sqlite3.Row;self.db.backup(fixture)
            spec=merge(form=form);plan=preview_distribution(fixture,spec)
            self.assertEqual(len(plan["groups"][0]["conflicts"]),3)
            apply_distribution(fixture,spec,fingerprint=plan["fingerprint"],reason="Years",resolutions={"new:1":choice},actor={"access_role":"master"})
            self.assertEqual([r[0] for r in fixture.execute("SELECT occurrence_year FROM occurrence ORDER BY occurrence_id")], [2019,2022,2024,2020] if choice=="expand" else [None,2022,None,None])
            self.assertEqual(tuple(fixture.execute("SELECT start_year,end_year FROM source WHERE source_id=4").fetchone()),(2019,2024) if choice=="expand" else (2021,2023))
            fixture.close()

    def test_unknown_open_and_in_range_periods_keep_years(self):
        for period in ({},{"start_year":"2010","end_year_status":"ongoing"},{"start_year":"2018","end_year":"2025","end_year_status":"known"}):
            plan=preview_distribution(self.db,merge(form={"source_name":"Merged","source_type":"OTRO",**period}))
            self.assertEqual(plan["groups"][0]["conflicts"],[])

    def test_stale_occurrence_period_and_origin_changes_rejected_for_both_operations(self):
        for spec in (split(),merge()):
            for sql in ("UPDATE occurrence SET original_gloss=original_gloss||'x' WHERE occurrence_id=1", "UPDATE source SET source_reference=coalesce(source_reference,'')||'x' WHERE source_id=2", "INSERT INTO occurrence(source_id) VALUES(1)"):
                fixture=sqlite3.connect(":memory:");fixture.row_factory=sqlite3.Row;self.db.backup(fixture)
                plan=preview_distribution(fixture,spec);fixture.execute(sql);fixture.commit()
                with self.assertRaises(SourceStructuralError):apply_distribution(fixture,spec,fingerprint=plan["fingerprint"],reason="Stale",actor={"access_role":"reviewer"})
                fixture.close()

    def test_rollback_partial_move_and_final_event_both_operations(self):
        for spec in (split("replace",{"1":"source:3","2":"new:1","3":"new:1"},{"new:1":{"source_name":"New","source_type":"OTRO"}}),merge()):
            self.db.execute("CREATE TRIGGER fail_move BEFORE UPDATE OF source_id ON occurrence WHEN NEW.occurrence_id=3 BEGIN SELECT RAISE(ABORT,'injected'); END");self.db.commit()
            before=list(self.db.iterdump())
            with self.assertRaises(sqlite3.IntegrityError):self.apply(spec)
            self.assertEqual(before,list(self.db.iterdump()))
            self.db.execute("DROP TRIGGER fail_move");self.db.commit()
            before=list(self.db.iterdump())
            from source_structural import record_activity
            def fail_final(db,event_type,**kwargs):
                if event_type in ("source_split","sources_merged"):raise RuntimeError("injected")
                return record_activity(db,event_type,**kwargs)
            with patch("source_structural.record_activity",side_effect=fail_final),self.assertRaises(RuntimeError):self.apply(spec)
            self.assertEqual(before,list(self.db.iterdump()))

    def test_activity_maps_destinations_periods_revisions_and_retirement(self):
        self.apply(split("replace",{"1":"source:2","2":"source:3","3":"source:3"}))
        event=self.table("activity_event")[-1];payload=json.loads(event["comment"])
        self.assertEqual(event["event_type"],"source_split");self.assertTrue(event["occurred_at"])
        self.assertEqual(payload["retired_source_ids"],[1]);self.assertEqual(payload["mode"],"replace")
        self.assertEqual({m["occurrence_id"]:m["destination_id"] for m in payload["moves"]},{1:2,2:3,3:3})
        self.assertTrue(all(m["occurrence_revision_id"] for m in payload["moves"]))

    def test_historical_publications_immutable_for_split_and_merge(self):
        publish_catalog(self.db,publication_comment="Disposable fixture",actor_context={"access_role":"master"})
        before=self.table("catalog_publication")
        self.apply(split())
        self.assertEqual(before,self.table("catalog_publication"))
        self.apply(merge())
        self.assertEqual(before,self.table("catalog_publication"))
        projected=build_catalog_projection(self.db)["concepts"][0]["alternatives"][0]["occurrences"]
        self.assertTrue(all(o["source"]["source_name"]=="Merged" for o in projected))


class SourceDistributionRouteTests(unittest.TestCase):
    def setUp(self):
        roles.RoleAccessTests.setUp(self)
        db=database.conectar();seed(db);db.close()

    tearDown=roles.RoleAccessTests.tearDown

    def post(self,kind,data,role="reviewer"):
        return self.client.post(f"/test-{role}/fuentes/1/{kind}",data=data)

    def split_form(self):
        return {"action":"preview","mode":"keep","occurrence_1":"source:1","occurrence_2":"source:2","occurrence_3":"source:2"}

    def merge_form(self):
        return {"action":"preview","other_source_id":"2","metadata_template":"first","source_name":"New","source_type":"OTRO"}

    def test_analyst_actions_invisible_and_crafted_get_post_denied(self):
        html=self.client.get("/test-analyst/fuentes").get_data(as_text=True)
        for kind,action in (("dividir","Dividir fuente"),("fusionar","Fusionar fuentes")):
            self.assertNotIn(action,html)
            self.assertEqual(self.client.get(f"/test-analyst/fuentes/1/{kind}").status_code,404)
            for form in ({"action":"preview"},{"action":"confirm","confirm":"yes"}):self.assertEqual(self.post(kind,form,"analyst").status_code,404)

    def test_duplicate_distribution_post_rejected(self):
        form=MultiDict(self.split_form());form.add("occurrence_2","source:1")
        self.assertEqual(self.post("dividir",form).status_code,400)

    def test_confirmation_token_required_and_bound_to_operation_role_and_state(self):
        response=self.post("dividir",self.split_form());self.assertEqual(response.status_code,200)
        token=re.search(r'name="token" value="([^"]+)"',response.get_data(as_text=True)).group(1)
        form={"action":"confirm","token":token,"reason":"Confirmed","confirm":"yes"}
        self.assertEqual(self.post("dividir",{**form,"confirm":""}).status_code,400)
        self.assertEqual(self.post("fusionar",form).status_code,400)
        self.assertEqual(self.post("dividir",form,"master").status_code,400)
        self.assertEqual(self.post("dividir",{**form,"token":"forged"}).status_code,400)
        self.assertEqual(self.client.post("/test-reviewer/fuentes/1/retirar",data=form).status_code,400)
        self.assertEqual(self.post("dividir",form).status_code,302)
        self.assertEqual(self.post("dividir",form).status_code,400)
        html=self.client.get("/test-reviewer/fuentes/2/historial").get_data(as_text=True)
        self.assertIn("División de Source",html);self.assertIn("Confirmed",html);self.assertIn("conservada activa",html)

    def test_merge_preview_and_history_of_all_origins_and_new_target(self):
        before=self.path.read_bytes();response=self.post("fusionar",self.merge_form(),"master")
        self.assertEqual(response.status_code,200);self.assertEqual(self.path.read_bytes(),before)
        html=response.get_data(as_text=True);self.assertIn("metadata diferente",html)
        token=re.search(r'name="token" value="([^"]+)"',html).group(1)
        self.assertEqual(self.post("fusionar",{"action":"confirm","token":token,"reason":"Merge reason","confirm":"yes"},"master").status_code,302)
        for sid in (1,2,3):
            html=self.client.get(f"/test-master/fuentes/{sid}/historial").get_data(as_text=True)
            self.assertIn("Fusión de Sources",html);self.assertIn("Merge reason",html)
        self.assertEqual(self.client.get("/test-reviewer/fuentes/1/dividir").status_code,404)
