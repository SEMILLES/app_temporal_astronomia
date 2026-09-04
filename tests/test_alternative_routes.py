import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp

ROOT=Path(__file__).resolve().parents[1]


class AlternativeRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"db.sqlite"; self.old=database.BASE_DATOS; database.BASE_DATOS=self.path
        db=self.connect(); database.crear_esquema(db); db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('Synthetic',2000,2000,'known')"); db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        for gloss,year in (("KNOWN",2000),("TO-ANALYZE",2001),("TARGET",2002)):
            oid=db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",(gloss,year)).lastrowid; db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)",(oid,))
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')"); db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)"); db.commit(); db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates")); app.testing=True; app.jinja_env.filters.update(human_concept_label=human_concept_label,alternative_display_label=alternative_display_label); app.register_blueprint(occurrences_bp); app.register_blueprint(submissions_bp); self.client=app.test_client()
    def tearDown(self): database.BASE_DATOS=self.old; self.tmp.cleanup()
    def connect(self): db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db

    def test_analysis_page_shows_context_canonical_and_pending_proposals(self):
        self.client.post("/ocurrencias/3/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"})
        page=self.client.get("/ocurrencias/2/clasificar").get_data(as_text=True)
        for text in ("TO-ANALYZE","Concepto","TEST-1","KNOWN","TEST — [PENDIENTE #","TARGET"):
            self.assertIn(text,page)

    def test_analysis_page_progressive_disclosure_and_singular_count(self):
        page=self.client.get("/ocurrencias/2/clasificar").get_data(as_text=True)
        self.assertIn("TEST-1 — 1 ocurrencia",page);self.assertNotIn("1 ocurrencias",page)
        self.assertIn('id="existing-alternative-field" hidden',page);self.assertIn("existing.hidden=!isExisting",page)
        self.assertIn('id="permutation-field" hidden',page)
        self.assertIn("¿Registrar componente(s) identificado(s)?",page)
        self.assertIn('name="record_components" value="no" checked',page)
        self.assertIn('<div id="components"></div>',page);self.assertIn('id="component-template"',page)
        self.assertNotIn('<div id="components"><div class="component">',page)

    def test_count_one_discards_stale_permutation_and_components(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"1","free_permutation":"SIN INFORMACIÓN","record_components":"yes","component_position":"1","component_label":"STALE","component_alternative_id":"","component_note":""})
        self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0]
        self.assertEqual(tuple(db.execute("SELECT component_count,free_permutation FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()),(1,"N/A"))
        self.assertEqual(db.execute("SELECT count(*) FROM alternative_submission_component WHERE submission_id=?",(sid,)).fetchone()[0],0);db.close()

    def test_na_normalizes_permutation_and_allows_optional_component(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A","free_permutation":"SÍ","record_components":"yes","component_position":"1","component_label":"EXPLÍCITO","component_alternative_id":"","component_note":""})
        self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0]
        self.assertEqual(tuple(db.execute("SELECT component_count,component_count_not_applicable,free_permutation FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()),(None,1,"N/A"))
        self.assertEqual(db.execute("SELECT component_label FROM alternative_submission_component WHERE submission_id=?",(sid,)).fetchone()[0],"EXPLÍCITO");db.close()

    def test_route_creates_existing_submission_not_assignment(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"EXISTING","proposed_existing_alternative_id":"1"})
        self.assertEqual(response.status_code,302); db=self.connect(); self.assertEqual(tuple(db.execute("SELECT s.submission_type,s.status,a.proposal_kind,a.proposed_existing_alternative_id FROM submission s JOIN alternative_submission a USING(submission_id)").fetchone()),("ALTERNATIVE","pending","EXISTING",1)); self.assertEqual(db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=2").fetchone()[0],0); db.close()

    def test_route_review_existing_materializes_assignment(self):
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"UNSURE","analysis_note":"Revisar"}); db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];db.close()
        response=self.client.post(f"/aportes/{sid}/decidir",data={"decision":"existing","alternative_id":"1","relation_policy":"preserve"}); self.assertEqual(response.status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0],1);db.close()

    def test_route_review_new_auto_and_legacy_detail_read_only(self):
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"});db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];db.close()
        review=self.client.get("/aportes/pendientes").get_data(as_text=True); self.assertIn("Propuesta: nueva alternativa",review); self.assertIn("PREVIEW DE CAMBIOS",review); self.assertIn("DECISIÓN DEL REVISOR",review)
        self.assertEqual(self.client.post(f"/aportes/{sid}/decidir",data={"decision":"new","approve_relations":"no","nomenclature_mode":"automatic"}).status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT count(*) FROM alternative").fetchone()[0],2);snapshot=tuple(db.execute("SELECT * FROM submission WHERE submission_id=?",(sid,)).fetchone());db.close()
        self.assertEqual(self.client.get(f"/aportes/{sid}").status_code,200);db=self.connect();self.assertEqual(tuple(db.execute("SELECT * FROM submission WHERE submission_id=?",(sid,)).fetchone()),snapshot);db.close()

    def test_route_captures_and_explicitly_approves_morphology(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","record_morphology":"yes","morphology_component_count":"2","free_permutation":"SIN INFORMACIÓN","morphology_note":"Synthetic morphology","record_components":"yes","component_position":["1","2"],"component_alternative_id":["1",""],"component_label":["","FREE"],"component_note":["Known",""]});self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];self.assertEqual(db.execute("SELECT component_count FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()[0],2);db.close()
        review=self.client.get("/aportes/pendientes").get_data(as_text=True);self.assertIn("Morfología propuesta por el analista",review);self.assertIn("Crear la alternativa y revisar morfología después",review);self.assertIn("Usar la morfología propuesta",review)
        response=self.client.post(f"/aportes/{sid}/decidir",data={"decision":"new","approve_relations":"no","approve_morphology":"yes","nomenclature_mode":"automatic"});self.assertEqual(response.status_code,302)
        db=self.connect();row=db.execute("SELECT m.created_from_submission_id,count(c.alternative_component_id) FROM alternative_morphology m LEFT JOIN alternative_component c USING(alternative_morphology_id) WHERE m.is_current=1 GROUP BY m.alternative_morphology_id").fetchone();self.assertEqual(tuple(row),(sid,2));db.close()


if __name__=="__main__": unittest.main()
