from tests.form_client import FormClient
import sqlite3
import unittest

import database
from flask import Flask, g
from pathlib import Path
import tempfile
from concept_labels import alternative_display_label, human_concept_label
from routes.alternatives import alternatives_bp
from alternative_admin import (
    AlternativeAdminError, apply_direct_nomenclature, apply_relation_change,
    relation_preview, update_morphology,
)
from alternative_relations import DuplicateCurrentRelationError, SelfRelationError


class AlternativeAdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        database.crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',1900,1900,'known')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('OTHER')")
        for index, year in enumerate((1900, 1910, 1920), 1):
            occurrence = self.db.execute(
                "INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",
                (f'O{index}', year),).lastrowid
            alternative = self.db.execute(
                "INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",
                (f'{index}a',),).lastrowid
            self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)",
                            (occurrence, alternative))
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        self.db.commit()
        self.reviewer = {"access_role": "reviewer", "collaborator_id": None}

    def tearDown(self):
        self.db.close()

    def test_morphology_versions_noop_and_no_fake_submission(self):
        first, changed = update_morphology(self.db, 1, {
            "component_count": None, "component_count_not_applicable": True,
            "free_permutation": "N/A", "components": (), "note": None,
        }, self.reviewer)
        self.assertTrue(changed)
        second, changed = update_morphology(self.db, 1, {
            "component_count": None, "component_count_not_applicable": True,
            "free_permutation": "N/A", "components": (), "note": None,
        }, self.reviewer)
        self.assertEqual(first, second)
        self.assertFalse(changed)
        newest, changed = update_morphology(self.db, 1, {
            "component_count": 2, "component_count_not_applicable": False,
            "free_permutation": "NO", "note": "corrección",
            "components": ({"position": 1, "component_label": "A"},),
        }, self.reviewer)
        self.assertTrue(changed)
        rows = self.db.execute("SELECT * FROM alternative_morphology ORDER BY alternative_morphology_id").fetchall()
        self.assertEqual([row["is_current"] for row in rows], [0, 1])
        self.assertEqual(rows[1]["supersedes_alternative_morphology_id"], first)
        self.assertIsNone(rows[1]["created_from_submission_id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM submission").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='alternative_morphology_updated'").fetchone()[0], 2)

    def test_relation_preview_merge_apply_multiparameter_and_retire_split(self):
        preview = relation_preview(self.db, 1, action="add", target_id=2, parameter="CM_1")
        self.assertEqual(preview["suggestions"][1], "1a")
        self.assertEqual(preview["suggestions"][2], "1b")
        relation_id, event_id = apply_relation_change(
            self.db, 1, action="add", target_id=2, parameter="CM_1",
            labels=preview["suggestions"], actor=self.reviewer)
        self.assertIsNotNone(event_id)
        second, _ = apply_relation_change(
            self.db, 1, action="add", target_id=2, parameter="MOV_M1",
            labels=relation_preview(self.db, 1, action="add", target_id=2,
                                    parameter="MOV_M1")["suggestions"], actor=self.reviewer)
        self.assertNotEqual(relation_id, second)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1").fetchone()[0], 2)
        # Retiring one parameter keeps the edge through the other parameter.
        no_change = relation_preview(self.db, 1, action="retire", relation_id=relation_id)
        self.assertEqual(no_change["changes"], [])
        apply_relation_change(self.db, 1, action="retire", relation_id=relation_id,
                              labels=no_change["suggestions"], actor=self.reviewer)
        split = relation_preview(self.db, 1, action="retire", relation_id=second)
        _, split_event = apply_relation_change(self.db, 1, action="retire", relation_id=second,
                                               labels=split["suggestions"], actor=self.reviewer)
        self.assertIsNotNone(split_event)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=0").fetchone()[0], 2)

    def test_relation_rejections_and_atomic_rollback(self):
        with self.assertRaises(SelfRelationError):
            relation_preview(self.db, 1, action="add", target_id=1, parameter="CM_1")
        with self.assertRaises(AlternativeAdminError):
            relation_preview(self.db, 1, action="add", target_id=4, parameter="CM_1")
        preview = relation_preview(self.db, 1, action="add", target_id=2, parameter="CM_1")
        apply_relation_change(self.db, 1, action="add", target_id=2, parameter="CM_1",
                              labels=preview["suggestions"], actor=self.reviewer)
        with self.assertRaises(DuplicateCurrentRelationError):
            relation_preview(self.db, 2, action="add", target_id=1, parameter="CM_1")
        self.db.execute("CREATE TRIGGER fail_renumber BEFORE INSERT ON renumber_change BEGIN SELECT RAISE(ABORT,'synthetic');END")
        self.db.commit()
        preview = relation_preview(self.db, 2, action="add", target_id=3, parameter="LOC_1")
        before = self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            apply_relation_change(self.db, 2, action="add", target_id=3, parameter="LOC_1",
                                  labels=preview["suggestions"], actor=self.reviewer)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0], before)

    def test_direct_nomenclature_manual_reason_validation_and_noop(self):
        self.assertIsNone(apply_direct_nomenclature(
            self.db, 1, {1: "1a", 2: "2a", 3: "3a"}, mode="automatic",
            reason=None, actor=self.reviewer))
        with self.assertRaises(AlternativeAdminError):
            apply_direct_nomenclature(self.db, 1, {1: "2a", 2: "1a", 3: "3a"},
                                      mode="manual", reason=None, actor=self.reviewer)
        event = apply_direct_nomenclature(
            self.db, 1, {1: "2a", 2: "1a", 3: "3a"}, mode="manual",
            reason="criterio editorial", actor=self.reviewer)
        self.assertIsNotNone(event)
        self.assertEqual(self.db.execute("SELECT origin FROM renumber_event WHERE renumber_event_id=?", (event,)).fetchone()[0], "manual")
        self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_change WHERE renumber_event_id=?", (event,)).fetchone()[0], 2)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])


class AlternativeAdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "admin.sqlite"
        self.old_path = database.BASE_DATOS
        database.BASE_DATOS = self.path
        db = sqlite3.connect(self.path)
        database.crear_esquema(db)
        db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)", [('1a',), ('2a',)])
        db.execute("INSERT INTO alternative_morphology(alternative_id,component_count_not_applicable,free_permutation) VALUES(1,1,'N/A')")
        db.commit(); db.close()
        root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(root / "templates")); app.testing = True
        app.jinja_env.filters.update(alternative_display_label=alternative_display_label,
                                     human_concept_label=human_concept_label)
        app.register_blueprint(alternatives_bp)
        self.role = "reviewer"
        @app.before_request
        def role_context():
            g.current_access_role = self.role
        self.client = FormClient(app.test_client())

    def tearDown(self):
        database.BASE_DATOS = self.old_path
        self.temp.cleanup()

    def test_reviewer_and_master_manage_but_analyst_cannot_get_or_post(self):
        for role in ("reviewer", "master"):
            self.role = role
            response = self.client.get("/alternativas/1/gestionar")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Morfología vigente", response.get_data(as_text=True))
        self.role = "analyst"
        self.assertEqual(self.client.get("/alternativas/1/gestionar").status_code, 404)
        self.assertEqual(self.client.post("/alternativas/1/gestionar", data={"action": "morphology"}).status_code, 404)
        self.assertEqual(self.client.post("/alternativas/1/gestionar", data={"action": "confirm_move", "destination_concept_id": 2, "confirm": "yes", "reason": "crafted"}).status_code, 404)

    def test_route_morphology_noop_and_update_without_submission(self):
        no_op = self.client.post("/alternativas/1/gestionar", data={
            "action": "morphology", "confirm": "yes", "component_count": "N/A",
            "free_permutation": "N/A",
        })
        self.assertEqual(no_op.status_code, 200)
        self.assertIn("No hay cambios.", no_op.get_data(as_text=True))
        changed = self.client.post("/alternativas/1/gestionar", data={
            "action": "morphology", "confirm": "yes", "component_count": "2",
            "free_permutation": "NO", "component_1_position": "1",
            "component_1_label": "BASE",
        })
        self.assertEqual(changed.status_code, 200)
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("SELECT count(*) FROM alternative_morphology").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT count(*) FROM submission").fetchone()[0], 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
