import importlib.util
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from catalog_projection import build_catalog_projection
from catalog_publication import publish_catalog
from occurrence_registration import complete_registration, save_draft
from source_structural import SourceStructuralError, apply_retirement, preview_retirement
from source_retirement_schema import TRIGGERS
from tests import test_collaboration_activity as roles

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration019", ROOT / "migrations/019_source_retirement.py")
migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)


def seed(db):
    db.execute("INSERT INTO source(source_name,source_type,analyst_protected) VALUES('Origin','OTRO',0)")
    db.execute("INSERT INTO source(source_name,source_type) VALUES('Target','MATERIAL_IMPRESO')")
    db.execute("INSERT INTO concept(preferred_label) VALUES('C')")
    db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
    for year, detail, status in ((2019, "02:30", "VALUE"), (2022, None, "NA"), (2024, None, "UNKNOWN")):
        oid = db.execute("""INSERT INTO occurrence(source_id,original_gloss,occurrence_year,
            source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status,
            usage_examples_present,grammatical_info_present,grammatical_note,
            legacy_source_detail_1,legacy_source_detail_2,source_locator,provenance_note)
            VALUES(1,'G',?,'  Literal  ',?,'VALUE',?,1,1,'Note','Legacy','N/A','Locator','Provenance')""", (year, detail, status)).lastrowid
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,1)", (oid,))
        db.execute("INSERT INTO occurrence_grammar(occurrence_id,grammar_note) VALUES(?,'Structured')", (oid,))
    db.commit()


class RetirementMigrationTests(unittest.TestCase):
    def test_migration_preserves_sources_and_installs_guards_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            db = sqlite3.connect(path); database.crear_esquema(db); seed(db)
            for name in TRIGGERS: db.execute(f"DROP TRIGGER {name}")
            for table in ("source", "source_revision"): db.execute(f"ALTER TABLE {table} DROP COLUMN retired_at")
            before = db.execute("SELECT source_id,source_name,analyst_protected FROM source").fetchall()
            db.commit(); db.close()
            self.assertTrue(migration.migrate(path, None))
            self.assertFalse(migration.migrate(path, None))
            db = sqlite3.connect(path)
            try:
                self.assertEqual(before, db.execute("SELECT source_id,source_name,analyst_protected FROM source").fetchall())
                self.assertEqual(db.execute("SELECT count(*) FROM source WHERE retired_at IS NOT NULL").fetchone()[0], 0)
                self.assertTrue(migration.migration_is_complete(db))
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally: db.close()


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:"); self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON"); database.crear_esquema(self.db); seed(self.db)

    def tearDown(self): self.db.close()

    def apply(self, **kwargs):
        target = kwargs.pop("destination_id", 2)
        new = kwargs.pop("new_source", None)
        plan = preview_retirement(self.db, 1, target, new)
        return apply_retirement(self.db, 1, target, new, fingerprint=plan["fingerprint"],
                                reason=kwargs.pop("reason", "Documentary correction"),
                                actor=kwargs.pop("actor", {"access_role": "reviewer"}), **kwargs)

    def period(self, start=2021, end=2023, status="known"):
        self.db.execute("UPDATE source SET start_year=?,end_year=?,end_year_status=? WHERE source_id=2", (start,end,status)); self.db.commit()

    def test_preview_is_read_only_and_warns_different_types(self):
        before = self.db.total_changes
        plan = preview_retirement(self.db, 1, 2)
        self.assertTrue(plan["different_types"]); self.assertEqual(plan["conflicts"], [])
        self.assertEqual(len(plan["occurrences"]), 3)
        self.assertEqual(self.db.total_changes, before); self.assertFalse(self.db.in_transaction)

    def test_move_all_preserves_identity_details_grammar_assignments_and_history(self):
        original = [dict(r) for r in self.db.execute("SELECT * FROM occurrence")]
        related = {t:[tuple(r) for r in self.db.execute(f"SELECT * FROM {t}")] for t in ("assignment", "alternative", "occurrence_grammar")}
        self.apply()
        current = [dict(r) for r in self.db.execute("SELECT * FROM occurrence")]
        history = [dict(r) for r in self.db.execute("SELECT * FROM occurrence_revision")]
        for before, after, revision in zip(original, current, history):
            self.assertEqual(after["source_id"], 2)
            for field in before:
                if field not in ("source_id", "updated_at"): self.assertEqual(after[field], before[field])
                if field in revision: self.assertEqual(revision[field], before[field])
        for table, rows in related.items(): self.assertEqual(rows, [tuple(r) for r in self.db.execute(f"SELECT * FROM {table}")])
        self.assertIsNotNone(self.db.execute("SELECT retired_at FROM source WHERE source_id=1").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM occurrence WHERE source_id=1 OR source_id IS NULL").fetchone()[0], 0)
        event = self.db.execute("SELECT * FROM activity_event WHERE event_type='source_retired'").fetchone()
        payload = json.loads(event["comment"])
        self.assertEqual(payload["occurrence_ids"], [1,2,3]); self.assertEqual(payload["destination_id"], 2)
        self.assertEqual(len(payload["occurrence_revision_ids"]), 3)
        self.assertEqual(event["access_role"], "reviewer"); self.assertTrue(event["occurred_at"])

    def test_master_can_retire(self):
        self.apply(actor={"access_role": "master"})
        self.assertEqual(self.db.execute("SELECT access_role FROM activity_event").fetchone()[0], "master")

    def test_analyst_and_missing_reason_denied(self):
        for reason in (None, "", "   "):
            with self.subTest(reason=reason), self.assertRaises(SourceStructuralError): self.apply(reason=reason)
        with self.assertRaises(SourceStructuralError): self.apply(actor={"access_role": "analyst"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM occurrence WHERE source_id=1").fetchone()[0], 3)

    def test_missing_self_and_retired_destination_rejected(self):
        for target in (None, 1, 999):
            with self.subTest(target=target), self.assertRaises(SourceStructuralError): preview_retirement(self.db, 1, target)
        self.db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP WHERE source_id=2"); self.db.commit()
        with self.assertRaises(SourceStructuralError): preview_retirement(self.db, 1, 2)

    def test_empty_source_retires_without_target(self):
        plan = preview_retirement(self.db, 2)
        apply_retirement(self.db, 2, fingerprint=plan["fingerprint"], reason="Unused", actor={"access_role": "reviewer"})
        self.assertIsNotNone(self.db.execute("SELECT retired_at FROM source WHERE source_id=2").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT count(*) FROM source").fetchone()[0], 2)

    def test_database_prevents_retirement_before_all_evidence_is_moved(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP WHERE source_id=1")
        self.db.rollback()
        self.assertIsNone(self.db.execute("SELECT retired_at FROM source WHERE source_id=1").fetchone()[0])

    def test_confirm_rejects_target_retired_after_preview(self):
        plan = preview_retirement(self.db, 1, 2)
        self.db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP WHERE source_id=2"); self.db.commit()
        with self.assertRaises(SourceStructuralError):
            apply_retirement(self.db, 1, 2, fingerprint=plan["fingerprint"], reason="x", actor={"access_role":"reviewer"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM occurrence WHERE source_id=1").fetchone()[0], 3)

    def test_no_period_and_in_range_preserve_years(self):
        self.period(2018, 2025)
        self.assertEqual(preview_retirement(self.db, 1, 2)["conflicts"], [])
        self.apply()
        self.assertEqual([r[0] for r in self.db.execute("SELECT occurrence_year FROM occurrence")], [2019,2022,2024])

    def test_year_conflicts_require_resolution_and_expand_both_ends(self):
        self.period()
        plan = preview_retirement(self.db, 1, 2)
        self.assertEqual([r["occurrence_year"] for r in plan["conflicts"]], [2019,2024])
        with self.assertRaises(SourceStructuralError): self.apply()
        self.apply(resolution="expand")
        self.assertEqual(tuple(self.db.execute("SELECT start_year,end_year FROM source WHERE source_id=2").fetchone()), (2019,2024))
        self.assertEqual(tuple(self.db.execute("SELECT start_year,end_year FROM source_revision WHERE source_id=2").fetchone()), (2021,2023))
        self.assertEqual([r[0] for r in self.db.execute("SELECT occurrence_year FROM occurrence")], [2019,2022,2024])

    def test_open_period_expansion_preserves_unknown_end(self):
        self.period(2021, None, "ongoing")
        self.apply(resolution="expand")
        self.assertEqual(tuple(self.db.execute("SELECT start_year,end_year,end_year_status FROM source WHERE source_id=2").fetchone()), (2019,None,"ongoing"))

    def test_clear_only_out_of_range_years(self):
        self.period(); self.apply(resolution="clear")
        self.assertEqual([r[0] for r in self.db.execute("SELECT occurrence_year FROM occurrence")], [None,2022,None])
        self.assertEqual([r[0] for r in self.db.execute("SELECT occurrence_year FROM occurrence_revision")], [2019,2022,2024])
        self.assertEqual(self.db.execute("SELECT count(*) FROM source_revision WHERE source_id=2").fetchone()[0], 0)

    def test_new_destination_is_atomic_protected_and_validated(self):
        for new in ({"source_name":"N"}, {"source_name":"Origin","source_type":"OTRO"}):
            with self.assertRaises(SourceStructuralError): preview_retirement(self.db, 1, new_source=new)
        self.apply(destination_id=None, new_source={"source_name":"New", "source_type":"OTRO"})
        self.assertEqual(tuple(self.db.execute("SELECT source_name,analyst_protected FROM source WHERE source_id=3").fetchone()), ("New",1))
        self.assertEqual(self.db.execute("SELECT count(*) FROM occurrence WHERE source_id=3").fetchone()[0], 3)

    def test_rollback_mid_move_and_after_retirement_including_new_destination(self):
        self.db.execute("CREATE TRIGGER injected_failure BEFORE UPDATE OF source_id ON occurrence WHEN NEW.occurrence_id=2 BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError): self.apply(destination_id=None, new_source={"source_name":"New", "source_type":"OTRO"})
        self.assertEqual(before, list(self.db.iterdump()))
        self.db.execute("DROP TRIGGER injected_failure"); self.db.commit()
        before = list(self.db.iterdump())
        with patch("source_structural.record_activity", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError): self.apply()
        self.assertEqual(before, list(self.db.iterdump()))

    def test_stale_preview_rejects_evidence_and_destination_changes(self):
        for sql in ("UPDATE occurrence SET source_detail_1='changed' WHERE occurrence_id=1", "UPDATE source SET start_year=2000 WHERE source_id=2", "INSERT INTO occurrence(source_id) VALUES(1)"):
            plan = preview_retirement(self.db, 1, 2)
            self.db.execute(sql); self.db.commit()
            with self.assertRaisesRegex(SourceStructuralError, "obsoleto"):
                apply_retirement(self.db, 1, 2, fingerprint=plan["fingerprint"], reason="x", actor={"access_role":"master"})

    def test_retired_source_rejects_new_occurrences_drafts_and_direct_sql(self):
        self.apply()
        for operation in (
            lambda: complete_registration(self.db, source_id=1, original_gloss="New", concept_id=1),
            lambda: save_draft(self.db, source_id=1, original_gloss="Draft"),
            lambda: self.db.execute("INSERT INTO occurrence(source_id) VALUES(1)"),
            lambda: self.db.execute("UPDATE occurrence SET source_id=1 WHERE occurrence_id=1"),
        ):
            with self.assertRaises((ValueError, sqlite3.IntegrityError)): operation()
            self.db.rollback()
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_live_catalog_moves_and_published_snapshot_is_immutable(self):
        published = publish_catalog(self.db, publication_comment="Disposable fixture", actor_context={"access_role":"master"})
        before = tuple(self.db.execute("SELECT * FROM catalog_publication").fetchone())
        self.apply()
        self.assertEqual(before, tuple(self.db.execute("SELECT * FROM catalog_publication").fetchone()))
        projection = build_catalog_projection(self.db)
        occurrences = projection["concepts"][0]["alternatives"][0]["occurrences"]
        self.assertTrue(all(o["source"]["source_name"] == "Target" for o in occurrences))
        self.assertIn('Origin', published["snapshot_json"])


class RetirementRouteTests(unittest.TestCase):
    def setUp(self):
        roles.RoleAccessTests.setUp(self)
        from concept_labels import human_concept_label
        self.client.application.jinja_env.filters["human_concept_label"] = human_concept_label
        db = database.conectar(); seed(db); db.close()

    tearDown = roles.RoleAccessTests.tearDown

    def preview(self, role="reviewer", **data):
        return self.client.post(f"/test-{role}/fuentes/1/retirar", data={"action":"preview", "destination_mode":"existing", "destination_id":"2", **data})

    def token(self, response):
        return re.search(r'name="token" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def test_analyst_invisible_and_crafted_get_post_denied(self):
        self.assertNotIn("Retirar / migrar fuente", self.client.get("/test-analyst/fuentes").get_data(as_text=True))
        for path in ("/fuentes/1/retirar", "/fuentes/retiradas", "/fuentes/1/historial"):
            self.assertEqual(self.client.get("/test-analyst"+path).status_code, 404)
        self.assertEqual(self.preview("analyst").status_code, 404)
        self.assertEqual(self.client.post("/test-analyst/fuentes/1/retirar", data={"action":"confirm"}).status_code, 404)

    def test_preview_confirmation_history_and_selector_exclusion(self):
        before = self.path.read_bytes()
        response = self.preview(); self.assertEqual(response.status_code, 200)
        self.assertEqual(before, self.path.read_bytes())
        self.assertIn("conservarán literalmente", response.get_data(as_text=True))
        payload = {"action":"confirm", "token":self.token(response), "reason":"Move", "confirm":"yes", "collaborator_id":"1"}
        self.assertEqual(self.client.post("/test-reviewer/fuentes/1/retirar", data={**payload, "confirm":""}).status_code, 400)
        self.assertEqual(self.client.post("/test-reviewer/fuentes/1/retirar", data={**payload, "token":"forged"}).status_code, 400)
        response = self.client.post("/test-reviewer/fuentes/1/retirar", data=payload, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Diana", response.get_data(as_text=True)); self.assertIn("Move", response.get_data(as_text=True))
        for path in ("/fuentes", "/aportes/nuevo", "/ocurrencias/1/editar"):
            self.assertNotIn("Origin", self.client.get("/test-analyst"+path).get_data(as_text=True))
        for path in ("/fuentes/1/editar", "/fuentes/1/proteccion"):
            self.assertEqual(self.client.get("/test-master"+path).status_code, 404)
        self.assertEqual(self.client.post("/test-master/fuentes/1/actualizar", data={}).status_code, 404)
        self.assertEqual(self.client.post("/test-reviewer/fuentes/1/retirar", data=payload).status_code, 400)

    def test_master_preview_and_conflict_choices(self):
        db = database.conectar(); db.execute("UPDATE source SET start_year=2021,end_year=2023,end_year_status='known' WHERE source_id=2"); db.commit(); db.close()
        response = self.preview("master"); self.assertEqual(response.status_code, 200)
        for text in ("2 occurrences quedan fuera", 'value="expand"', 'value="clear"'):
            self.assertIn(text, response.get_data(as_text=True))
        result = self.client.post("/test-master/fuentes/1/retirar", data={"action":"confirm", "confirm":"yes", "token":self.token(response), "reason":"Expand", "resolution":"expand"})
        self.assertEqual(result.status_code, 302)

    def test_manual_period_edit_blocks_silent_year_conflicts_for_all_authorized_roles(self):
        for role in ("analyst", "reviewer", "master"):
            response = self.client.post(f"/test-{role}/fuentes/1/actualizar", data={"source_name":"Origin", "source_type":"OTRO", "start_year":"2021", "end_year":"2023", "end_year_status":"known"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("No se guardó el periodo", response.get_data(as_text=True))
        db = database.conectar()
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM source_revision").fetchone()[0], 0)
            self.assertEqual([r[0] for r in db.execute("SELECT occurrence_year FROM occurrence")], [2019,2022,2024])
        finally: db.close()
