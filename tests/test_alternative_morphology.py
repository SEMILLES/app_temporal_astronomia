import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from alternative_morphology import (
    MorphologyValidationError,create_or_replace_alternative_morphology,
    normalize_morphology,submission_morphology,
)
from alternative_workflow import (
    AlternativeWorkflowError,create_alternative_submission,
    reject_alternative_submission,review_as_existing,review_as_new,
)

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("migration011",ROOT/"migrations/011_alternative_morphology.py")
MIGRATION=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MIGRATION)


class MorphologyMigrationTests(unittest.TestCase):
    def test_post010_to_011_is_empty_preserving_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite";db=sqlite3.connect(path);database.crear_esquema(db)
            for table in ("alternative_component","alternative_morphology","alternative_submission_component","alternative_submission_morphology"):db.execute(f"DROP TABLE {table}")
            db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')");db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')");db.commit();before=db.execute("SELECT * FROM alternative").fetchall();db.close()
            self.assertTrue(MIGRATION.migrate(path,Path(raw)/"backup.db"));self.assertFalse(MIGRATION.migrate(path,Path(raw)/"unused.db"))
            db=sqlite3.connect(path);self.assertEqual(db.execute("SELECT * FROM alternative").fetchall(),before)
            for table in MIGRATION.TABLE_COLUMNS:self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0],0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[]);db.close()

    def test_partial_installation_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite";db=sqlite3.connect(path);database.crear_esquema(db);db.execute("DROP TABLE alternative_component");db.commit();db.close()
            with self.assertRaises(RuntimeError):MIGRATION.migrate(path,None)

    def test_fresh_and_migrated_morphology_structures_are_equivalent(self):
        with tempfile.TemporaryDirectory() as raw:
            fresh_path=Path(raw)/"fresh.db";migrated_path=Path(raw)/"migrated.db"
            fresh=sqlite3.connect(fresh_path);database.crear_esquema(fresh);fresh.close()
            migrated=sqlite3.connect(migrated_path);database.crear_esquema(migrated)
            for table in ("alternative_component","alternative_morphology","alternative_submission_component","alternative_submission_morphology"):migrated.execute(f"DROP TABLE {table}")
            migrated.commit();migrated.close();MIGRATION.migrate(migrated_path,None)
            fresh=sqlite3.connect(fresh_path);migrated=sqlite3.connect(migrated_path)
            for table in MIGRATION.TABLE_COLUMNS:
                self.assertEqual(fresh.execute(f"PRAGMA table_info({table})").fetchall(),migrated.execute(f"PRAGMA table_info({table})").fetchall())
                self.assertEqual(fresh.execute(f"PRAGMA foreign_key_list({table})").fetchall(),migrated.execute(f"PRAGMA foreign_key_list({table})").fetchall())
                self.assertEqual({r[1] for r in fresh.execute(f"PRAGMA index_list({table})")},{r[1] for r in migrated.execute(f"PRAGMA index_list({table})")})
            fresh.close();migrated.close()


class MorphologyValidationTests(unittest.TestCase):
    def test_count_states_and_permutation(self):
        self.assertEqual(normalize_morphology()["free_permutation"],None)
        self.assertEqual(normalize_morphology(component_count_not_applicable=True)["free_permutation"],"N/A")
        self.assertEqual(normalize_morphology(component_count=1)["free_permutation"],"N/A")
        for value in ("SIN INFORMACIÓN","SÍ","NO"):
            self.assertEqual(normalize_morphology(component_count=2,free_permutation=value)["free_permutation"],value)
        for kwargs in ({"component_count":0},{"component_count":2},{"component_count_not_applicable":True,"component_count":1},{"component_count":1,"free_permutation":"SÍ"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(MorphologyValidationError):normalize_morphology(**kwargs)

    def test_components_need_unique_positive_positions_but_not_count_equality(self):
        morphology=normalize_morphology(component_count=3,free_permutation="NO",components=[{"position":1,"component_label":"A"},{"position":2,"note":"Reconocible"}]);self.assertEqual(len(morphology["components"]),2)
        self.assertEqual(len(normalize_morphology(component_count=2,free_permutation="SÍ",components=[])["components"]),0)
        for components in ([{"position":0,"note":"x"}],[{"position":1,"note":"x"},{"position":1,"note":"y"}],[{"position":1}]):
            with self.assertRaises(MorphologyValidationError):normalize_morphology(component_count=2,free_permutation="NO",components=components)


class CanonicalMorphologyTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");database.crear_esquema(self.db);self.db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')");self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",[("1",),("2",)]);self.db.commit()
    def tearDown(self):self.db.close()

    def test_absence_then_create_current_with_registered_and_free_components(self):
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology").fetchone()[0],0)
        mid,created=create_or_replace_alternative_morphology(self.db,1,component_count=3,free_permutation="SIN INFORMACIÓN",components=[{"position":1,"component_alternative_id":2},{"position":2,"component_label":" libre ","note":" nota "}]);self.assertTrue(created)
        row=self.db.execute("SELECT * FROM alternative_morphology WHERE alternative_morphology_id=?",(mid,)).fetchone();self.assertEqual(row["is_current"],1);components=self.db.execute("SELECT component_alternative_id,component_label,note FROM alternative_component ORDER BY position").fetchall();self.assertEqual([tuple(r) for r in components],[(2,None,None),(None,"libre","nota")])

    def test_replace_versions_entire_component_block(self):
        old,_=create_or_replace_alternative_morphology(self.db,1,component_count=2,free_permutation="NO",components=[{"position":1,"component_label":"OLD"}]);new,_=create_or_replace_alternative_morphology(self.db,1,component_count=1,components=[{"position":1,"component_label":"NEW"}]);rows=self.db.execute("SELECT * FROM alternative_morphology ORDER BY alternative_morphology_id").fetchall();self.assertEqual([r["is_current"] for r in rows],[0,1]);self.assertEqual(rows[1]["supersedes_alternative_morphology_id"],old);self.assertEqual(self.db.execute("SELECT component_label FROM alternative_component WHERE alternative_morphology_id=?",(old,)).fetchone()[0],"OLD");self.assertEqual(self.db.execute("SELECT component_label FROM alternative_component WHERE alternative_morphology_id=?",(new,)).fetchone()[0],"NEW")

    def test_identical_is_noop_and_one_current(self):
        first,_=create_or_replace_alternative_morphology(self.db,1,component_count=1);second,created=create_or_replace_alternative_morphology(self.db,1,component_count=1);self.assertEqual(first,second);self.assertFalse(created);self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE is_current=1").fetchone()[0],1)

    def test_retired_component_and_rollback(self):
        self.db.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=2");self.db.commit()
        with self.assertRaises(MorphologyValidationError):create_or_replace_alternative_morphology(self.db,1,component_count=2,free_permutation="NO",components=[{"component_alternative_id":2}])
        self.db.execute("UPDATE alternative SET retired_at=NULL WHERE alternative_id=2");self.db.execute("CREATE TRIGGER fail_component BEFORE INSERT ON alternative_component BEGIN SELECT RAISE(ABORT,'synthetic');END");self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):create_or_replace_alternative_morphology(self.db,1,component_count=2,free_permutation="NO",components=[{"component_alternative_id":2}])
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology").fetchone()[0],0)

    def test_morphology_edit_does_not_touch_nomenclature_or_relations(self):
        before_labels=self.db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id").fetchall();before_relations=self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0];before_events=self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0];create_or_replace_alternative_morphology(self.db,1,component_count=1);self.assertEqual(self.db.execute("SELECT alternative_id,working_label FROM alternative ORDER BY alternative_id").fetchall(),before_labels);self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_relation").fetchone()[0],before_relations);self.assertEqual(self.db.execute("SELECT count(*) FROM renumber_event").fetchone()[0],before_events)


class MorphologyReviewTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");database.crear_esquema(self.db);self.db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('S',2000,2000,'known')");self.db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        for gloss,year in (("OLD",2000),("NEW",2001)):
            oid=self.db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",(gloss,year)).lastrowid;self.db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)",(oid,))
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')");self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)");self.db.commit()
        self.morphology={"component_count":2,"free_permutation":"SIN INFORMACIÓN","note":"Proposal","components":[{"position":1,"component_alternative_id":1},{"position":2,"component_label":"DESCRIPTIVE"}]}
    def tearDown(self):self.db.close()
    def proposal(self,morphology=True):return create_alternative_submission(self.db,2,"NEW",phonological_relation_answer="NO",morphology=self.morphology if morphology else None)

    def test_new_without_morphology_and_existing_rejects_morphology_input(self):
        self.assertIsInstance(self.proposal(False),int);self.db.execute("UPDATE submission SET status='resolved',resolution='rejected' WHERE submission_id=1");self.db.commit()
        with self.assertRaises(MorphologyValidationError):create_alternative_submission(self.db,2,"EXISTING",proposed_existing_alternative_id=1,morphology=self.morphology)

    def test_proposal_persists_components_less_than_count(self):
        sid=self.proposal();row,components=submission_morphology(self.db,sid);self.assertEqual(row["component_count"],2);self.assertEqual(len(components),2)

    def test_review_new_explicit_approve_creates_canonical_with_provenance(self):
        sid=self.proposal();new=review_as_new(self.db,sid,approve_morphology=True,nomenclature_mode="automatic");row=self.db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1",(new,)).fetchone();self.assertEqual((row["component_count"],row["created_from_submission_id"]),(2,sid));self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_component WHERE alternative_morphology_id=?",(row["alternative_morphology_id"],)).fetchone()[0],2)

    def test_review_new_default_does_not_approve_but_history_remains(self):
        sid=self.proposal();new=review_as_new(self.db,sid,nomenclature_mode="automatic");self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology WHERE alternative_id=?",(new,)).fetchone()[0],0);self.assertIsNotNone(submission_morphology(self.db,sid))

    def test_resolve_existing_and_reject_never_apply_proposal(self):
        existing,_=create_or_replace_alternative_morphology(self.db,1,component_count=1,note="Stable")
        sid=self.proposal();review_as_existing(self.db,sid,1);self.assertEqual(self.db.execute("SELECT alternative_morphology_id FROM alternative_morphology WHERE alternative_id=1 AND is_current=1").fetchone()[0],existing);self.assertIsNotNone(submission_morphology(self.db,sid))
        sid=self.proposal();reject_alternative_submission(self.db,sid);self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_morphology").fetchone()[0],1);self.assertIsNotNone(submission_morphology(self.db,sid))

    def test_morphology_failure_rolls_back_whole_review(self):
        sid=create_alternative_submission(self.db,2,"NEW",phonological_relation_answer="YES",relations=[{"target_alternative_id":1,"phonological_parameter":"CM_1"}],morphology=self.morphology);self.db.execute("CREATE TRIGGER fail_canonical_component BEFORE INSERT ON alternative_component BEGIN SELECT RAISE(ABORT,'synthetic');END");self.db.commit();before={t:self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("alternative","assignment","alternative_relation","renumber_event","alternative_morphology")}
        with self.assertRaises(sqlite3.IntegrityError):review_as_new(self.db,sid,approve_relations=True,approve_morphology=True,nomenclature_mode="automatic")
        self.assertEqual({t:self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before},before);self.assertEqual(self.db.execute("SELECT status FROM submission WHERE submission_id=?",(sid,)).fetchone()[0],"pending")


if __name__=="__main__":unittest.main()
