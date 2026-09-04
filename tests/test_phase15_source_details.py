import importlib.util, sqlite3, tempfile, unittest
from pathlib import Path

from database import crear_esquema
from occurrence_registration import RegistrationError, complete_registration, save_draft
from source_details import SOURCE_TYPES, catalog_source_reference, normalize_occurrence_details
from normalization.phase15_source_details import normalize
from routes.sources import source_form_values

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("migration017",ROOT/"migrations"/"017_source_detail_semantics.py")
migration017=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration017)

class Migration017Tests(unittest.TestCase):
    def test_incremental_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"old.db";db=sqlite3.connect(path)
            db.executescript("CREATE TABLE source(source_id INTEGER);CREATE TABLE occurrence(occurrence_id INTEGER,source_detail_1 TEXT,source_detail_2 TEXT);CREATE TABLE occurrence_revision(occurrence_revision_id INTEGER,source_detail_1 TEXT,source_detail_2 TEXT);CREATE TABLE occurrence_draft(draft_id INTEGER,source_detail_1 TEXT,source_detail_2 TEXT);CREATE TABLE activity_event(activity_event_id INTEGER);CREATE TABLE collaborator(collaborator_id INTEGER PRIMARY KEY);")
            db.close();self.assertTrue(migration017.migrate(path,None));self.assertFalse(migration017.migrate(path,None))
            db=sqlite3.connect(path);self.assertTrue({"source_detail_1_status","source_detail_2_status"}<=migration017.columns(db,"occurrence"));self.assertEqual(db.execute("SELECT setting_value FROM application_setting").fetchone()[0],"1");db.close()

class DetailSemanticsTests(unittest.TestCase):
    def test_five_types_and_validation(self):
        self.assertEqual(len(SOURCE_TYPES),5)
        for source_type in SOURCE_TYPES:self.assertEqual(source_form_values({"source_name":"S","source_type":source_type})[1],source_type)
        with self.assertRaises(ValueError):source_form_values({"source_name":"S"})
        self.assertEqual(normalize_occurrence_details("MATERIAL_IMPRESO","NA",None,"VALUE","203"),("NA",None,"VALUE","203"))
        self.assertEqual(normalize_occurrence_details("VIDEO_POR_SENA","VALUE","COHETE 2","VALUE","bad"),("VALUE","COHETE 2","NA",None))
        for status in ("NA","UNKNOWN"):
            with self.assertRaises(ValueError):normalize_occurrence_details("OTRO",status,"texto","NA",None)
        with self.assertRaises(ValueError):normalize_occurrence_details("OTRO","VALUE","","NA",None)
        with self.assertRaises(ValueError):normalize_occurrence_details("MATERIAL_IMPRESO","NA",None,"VALUE","p. 203")
        with self.assertRaises(ValueError):normalize_occurrence_details("UN_VIDEO_VARIAS_SENAS","VALUE","Título","VALUE","99:99")

    def test_catalog_reference_rules(self):
        def item(kind,d1,s1,d2=None,s2="NA",gloss="COHETE"):
            return {"source":{"source_type":kind},"original_gloss":gloss,"source_detail_1":d1,"source_detail_1_status":s1,"source_detail_2":d2,"source_detail_2_status":s2}
        self.assertEqual(catalog_source_reference(item("MATERIAL_IMPRESO",None,"NA","203","VALUE")),"p. 203")
        self.assertEqual(catalog_source_reference(item("UN_VIDEO_VARIAS_SENAS","Título","VALUE","02:20","VALUE")),"Título · 02:20")
        self.assertIsNone(catalog_source_reference(item("VIDEO_POR_SENA","COHETE","VALUE")))
        self.assertEqual(catalog_source_reference(item("VIDEO_POR_SENA","COHETE 2","VALUE")),"COHETE 2")

    def test_ui_has_type_specific_labels_and_structured_statuses(self):
        javascript=(ROOT/"static"/"source-details.js").read_text(encoding="utf-8")
        template=(ROOT/"templates"/"_source_details_form.html").read_text(encoding="utf-8")
        for label in ("Submaterial / sección","Página","Título / identificador del video","Título del video","Tiempo","Detalle Fuente 1","Detalle Fuente 2"):
            self.assertIn(label,javascript)
        for value in ("VALUE","NA","UNKNOWN","Dato","N/A","Desconocido"):
            self.assertIn(value,template)
        self.assertIn("input.disabled",javascript);self.assertIn("input.required",javascript)

    def test_draft_incomplete_but_completion_gets_explicit_states(self):
        db=sqlite3.connect(":memory:");db.row_factory=sqlite3.Row;crear_esquema(db)
        db.execute("INSERT INTO source(source_name,source_type) VALUES('S','OTRO')");db.execute("INSERT INTO concept(preferred_label) VALUES('C')")
        draft=save_draft(db,source_id=1,original_gloss=None)
        row=db.execute("SELECT source_detail_1_status,source_detail_2_status FROM occurrence_draft WHERE draft_id=?",(draft,)).fetchone();self.assertEqual(tuple(row),("UNKNOWN","UNKNOWN"))
        oid=complete_registration(db,source_id=1,original_gloss="G",concept_id=1,source_detail_1_status="NA",source_detail_2_status="UNKNOWN")
        self.assertEqual(tuple(db.execute("SELECT source_detail_1_status,source_detail_2_status FROM occurrence WHERE occurrence_id=?",(oid,)).fetchone()),("NA","UNKNOWN"));db.close()

    def test_normalization_is_idempotent_explicit_and_preserves_legacy(self):
        db=sqlite3.connect(":memory:");db.row_factory=sqlite3.Row;crear_esquema(db)
        db.executemany("INSERT INTO source(source_id,source_name) VALUES(?,?)",[(i,f"S{i}") for i in range(1,45)])
        db.executemany("INSERT INTO occurrence(occurrence_id,source_id,legacy_source_detail_2,source_detail_2) VALUES(?,?,?,?)",[(i,19 if i==210 else 21 if i==206 else 1,"32:40:00" if i==210 else "N/A" if i==206 else None,"32:40:00" if i==210 else "N/A" if i==206 else None) for i in range(1,254)])
        db.commit()
        normalize(db,True);self.assertEqual(normalize(db,False),{"sources":[],"occurrences":[]})
        self.assertEqual(db.execute("SELECT source_type FROM source WHERE source_id=42").fetchone()[0],"UN_VIDEO_VARIAS_SENAS")
        self.assertEqual(tuple(db.execute("SELECT source_detail_2,source_detail_2_status,legacy_source_detail_2 FROM occurrence WHERE occurrence_id=206").fetchone()),(None,"NA","N/A"))
        self.assertEqual(tuple(db.execute("SELECT source_detail_2,source_detail_2_status,legacy_source_detail_2 FROM occurrence WHERE occurrence_id=210").fetchone()),("32:40","VALUE","32:40:00"));db.close()

if __name__=="__main__":unittest.main()
