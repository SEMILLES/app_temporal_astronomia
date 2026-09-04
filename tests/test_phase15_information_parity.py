import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from catalog_projection import build_catalog_projection
from normalization.phase15_information_parity import normalize
from occurrence_registration import complete_registration, save_draft

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("migration016", ROOT / "migrations/016_information_parity.py")
MIGRATION = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MIGRATION)


class Phase15SchemaAndRegistrationTests(unittest.TestCase):
    def test_migration_is_minimal_idempotent_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"db.sqlite"; db=sqlite3.connect(path); database.crear_esquema(db)
            for table in ("occurrence","occurrence_revision","occurrence_draft"):
                for name,_ in MIGRATION.OCCURRENCE_COLUMNS: pass
            # Simulate the pre-016 schema with a compact fixture.
            db.execute("INSERT INTO source(source_name) VALUES('S')")
            db.execute("INSERT INTO concept(preferred_label) VALUES('C')")
            db.execute("INSERT INTO occurrence(source_id,legacy_source_detail_1,legacy_source_detail_2) VALUES(1,'material','p. 2')")
            db.commit(); db.close()
            self.assertFalse(MIGRATION.migrate(path,None))
            db=sqlite3.connect(path); self.assertEqual(db.execute("SELECT legacy_source_detail_1,legacy_source_detail_2 FROM occurrence").fetchone(),("material","p. 2")); db.close()

    def test_new_occurrence_and_draft_capture_documentary_fields(self):
        db=sqlite3.connect(":memory:"); db.row_factory=sqlite3.Row; database.crear_esquema(db)
        db.execute("INSERT INTO source(source_name) VALUES('S')"); db.execute("INSERT INTO concept(preferred_label) VALUES('C')"); db.commit()
        draft=save_draft(db,source_id=1,original_gloss="G",source_detail_1="video",source_detail_2="01:20",usage_examples_present="1",grammatical_info_present="1",grammatical_note="nota",reference_concept_id=1)
        oid=complete_registration(db,draft_id=draft,concept_id=1)
        row=db.execute("SELECT source_detail_1,source_detail_2,usage_examples_present,grammatical_info_present,grammatical_note,hyperlink,source_locator FROM occurrence WHERE occurrence_id=?",(oid,)).fetchone()
        self.assertEqual(tuple(row),("video","01:20",1,1,"nota",None,None)); db.close()

    def test_catalog_hides_negative_flags_and_projects_positive_metadata(self):
        db=sqlite3.connect(":memory:"); db.row_factory=sqlite3.Row; database.crear_esquema(db)
        db.execute("INSERT INTO source(source_name,legacy_source_code) VALUES('S','LS')")
        db.execute("INSERT INTO concept(preferred_label,knowledge_area_1) VALUES('C','ASTRONOMÍA')")
        db.execute("INSERT INTO alternative(concept_id,working_label,original_code) VALUES(1,'1a','OLD')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss,source_detail_1,usage_examples_present) VALUES(1,'G','video',1)")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)"); db.commit()
        concept=build_catalog_projection(db)["concepts"][0]; occurrence=concept["alternatives"][0]["occurrences"][0]
        self.assertEqual(concept["knowledge_areas"],["ASTRONOMÍA"]); self.assertEqual(occurrence["source_detail_1"],"video"); self.assertTrue(occurrence["usage_examples_present"]); self.assertFalse(occurrence["grammatical_info_present"]); db.close()


class Phase15AstronomyNormalizationTests(unittest.TestCase):
    def test_documented_normalization_versions_morphology_and_preserves_relations(self):
        protected=ROOT/"import_inputs/astronomia/lesico_astronomia_working.db"
        if not protected.exists(): self.skipTest("Private astronomy fixture unavailable")
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"copy.db"; shutil.copy2(protected,path); MIGRATION.migrate(path,None)
            db=sqlite3.connect(path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON")
            before=[tuple(r) for r in db.execute("SELECT * FROM alternative_relation ORDER BY alternative_relation_id")]
            report=normalize(db,apply=True)
            self.assertEqual(len(report["plan"]["morphology"]),15)
            self.assertEqual(db.execute("SELECT count(*) FROM alternative_morphology WHERE is_current=0").fetchone()[0],15)
            self.assertEqual(db.execute("SELECT count(*) FROM alternative_relation WHERE is_current=1").fetchone()[0],19)
            self.assertEqual(before,[tuple(r) for r in db.execute("SELECT * FROM alternative_relation ORDER BY alternative_relation_id")])
            self.assertEqual(db.execute("SELECT count(*) FROM concept WHERE knowledge_area_1='ASTRONOMÍA' AND knowledge_area_2 IS NULL AND semantic_field_1 IS NULL AND semantic_field_2 IS NULL").fetchone()[0],150)
            tiempo=db.execute("""SELECT m.component_count FROM alternative_morphology m JOIN alternative a USING(alternative_id) JOIN concept c USING(concept_id) WHERE c.preferred_label='TIEMPO' AND a.working_label='1a' AND m.is_current=1""").fetchone()[0]
            self.assertEqual(tiempo,1); db.close()


if __name__ == "__main__": unittest.main()
