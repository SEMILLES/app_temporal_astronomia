from submission_concept_resolution import save_resolution
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from grammar_workflow import GrammarWorkflowError, create_grammar_submission, resolve_grammar_submission
from occurrence_grammar import create_or_replace_occurrence_grammar
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("gender", "plural", "agentive", "conjugated_form", "negation")


class GrammarWorkflowRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)/"test.db"
        self.previous = database.BASE_DATOS; database.BASE_DATOS = self.path
        db = self.connect(); database.crear_esquema(db)
        db.execute("INSERT INTO source(source_name) VALUES('Synthetic source')")
        db.execute("INSERT INTO concept(preferred_label) VALUES('ASTRONOMIA')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss,hyperlink) VALUES(1,'EVIDENCE','https://example.test')")
        db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        db.commit(); db.close()
        app = Flask(__name__, template_folder=str(ROOT/"templates")); app.testing=True
        app.jinja_env.filters.update(human_concept_label=human_concept_label, alternative_display_label=alternative_display_label)
        app.register_blueprint(occurrences_bp); app.register_blueprint(submissions_bp)
        self.client=app.test_client()

    def tearDown(self): database.BASE_DATOS=self.previous; self.tmp.cleanup()
    def connect(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db

    def test_form_has_context_vocabularies_and_no_linguistic_default(self):
        page=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True)
        for text in ("EVIDENCE","Synthetic source","ASTRONOMIA","Sin analizar","SIN-MARCA","CON-NEG","Analizado con duda"):
            self.assertIn(text,page)
        self.assertNotIn('value="SIN-MARCA" selected',page)

    def test_partial_submission_with_uncertainty_and_no_canonical_write(self):
        response=self.client.post("/ocurrencias/1/gramatica",data={"gender":"FEM-A","gender_uncertain":"on","note":"Observed"})
        self.assertEqual(response.status_code,302)
        db=self.connect(); row=db.execute("SELECT s.submission_type,s.status,gs.gender,gs.gender_uncertain,gs.plural,gs.note FROM submission s JOIN grammar_submission gs USING(submission_id)").fetchone()
        self.assertEqual(tuple(row),("GRAMMAR","pending","FEM-A",1,None,"Observed"))
        self.assertEqual(db.execute("SELECT count(*) FROM occurrence_grammar").fetchone()[0],0); db.close()

    def test_empty_structured_proposal_and_null_uncertain_are_rejected(self):
        self.assertEqual(self.client.post("/ocurrencias/1/gramatica",data={"note":"Note only"}).status_code,400)
        self.assertEqual(self.client.post("/ocurrencias/1/gramatica",data={"gender":"","gender_uncertain":"on"}).status_code,400)
        self.assertEqual(
            self.client.post(
                "/ocurrencias/1/gramatica",
                data={"gender":"FEM-A","gender_uncertain":"on"},
            ).status_code,
            400,
        )

    def test_second_pending_is_prevented_and_displayed(self):
        self.client.post("/ocurrencias/1/gramatica",data={"plural":"REDUP."})
        page=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True)
        self.assertIn("Propuesta pendiente",page); self.assertNotIn("Enviar propuesta a revisión",page)
        self.assertEqual(self.client.post("/ocurrencias/1/gramatica",data={"gender":"FEM-A"}).status_code,400)
        db=self.connect(); self.assertEqual(db.execute("SELECT count(*) FROM submission").fetchone()[0],1); db.close()

    def test_accept_creates_current_provenance_and_resolves_uncertainty(self):
        db=self.connect()
        sid=create_grammar_submission(
            db,1,{"gender":"MASC-O","gender_uncertain":1,"note":"Full"}
        )
        resolve_grammar_submission(
            db,sid,"accepted",reviewed_by="reviewer",review_note="OK"
        )
        grammar=db.execute(
            "SELECT * FROM occurrence_grammar WHERE is_current=1"
        ).fetchone()
        submission=db.execute(
            "SELECT * FROM submission WHERE submission_id=?",(sid,)
        ).fetchone()
        proposal=db.execute(
            "SELECT gender,gender_uncertain,note FROM grammar_submission "
            "WHERE submission_id=?",(sid,)
        ).fetchone()
        self.assertEqual(
            (
                grammar["gender"],
                grammar["gender_uncertain"],
                grammar["created_from_submission_id"],
            ),
            ("MASC-O",0,sid),
        )
        self.assertEqual(tuple(proposal),("MASC-O",1,"Full"))
        self.assertEqual(
            (
                submission["status"],
                submission["resolution"],
                submission["reviewed_by"],
                submission["review_note"],
            ),
            ("resolved","accepted","reviewer","OK"),
        )
        self.assertIsNotNone(submission["resolved_at"])
        db.close()

    def test_accept_with_reviewer_correction_preserves_original_submission(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"gender":"MASC-O"})
        resolve_grammar_submission(
            db,
            sid,
            "accepted",
            reviewed_values={
                "gender":"FEM-A",
                "plural":"",
                "agentive":"",
                "conjugated_form":"",
                "negation":"",
            },
            reviewed_by="reviewer",
            review_note="Se corrige el género tras revisar la evidencia.",
        )
        proposal=db.execute(
            "SELECT gender,gender_uncertain FROM grammar_submission "
            "WHERE submission_id=?",(sid,)
        ).fetchone()
        grammar=db.execute(
            "SELECT gender,gender_uncertain,change_note,"
            "created_from_submission_id FROM occurrence_grammar "
            "WHERE is_current=1"
        ).fetchone()
        self.assertEqual(tuple(proposal),("MASC-O",0))
        self.assertEqual(
            tuple(grammar),
            (
                "FEM-A",
                0,
                "Se corrige el género tras revisar la evidencia.",
                sid,
            ),
        )
        db.close()

    def test_reviewer_change_requires_response_and_keeps_submission_pending(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"gender":"MASC-O"})
        with self.assertRaises(GrammarWorkflowError):
            resolve_grammar_submission(
                db,
                sid,
                "accepted",
                reviewed_values={
                    "gender":"FEM-A",
                    "plural":"",
                    "agentive":"",
                    "conjugated_form":"",
                    "negation":"",
                },
            )
        self.assertEqual(
            db.execute(
                "SELECT status FROM submission WHERE submission_id=?",(sid,)
            ).fetchone()[0],
            "pending",
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM occurrence_grammar").fetchone()[0],
            0,
        )
        db.close()

    def test_uncertain_proposal_requires_reviewer_response_before_acceptance(self):
        db=self.connect()
        sid=create_grammar_submission(
            db,
            1,
            {
                "gender":"MASC-O",
                "gender_uncertain":1,
                "note":"No estoy seguro por la calidad del video.",
            },
        )
        with self.assertRaises(GrammarWorkflowError):
            resolve_grammar_submission(db,sid,"accepted")
        self.assertEqual(
            db.execute(
                "SELECT status FROM submission WHERE submission_id=?",(sid,)
            ).fetchone()[0],
            "pending",
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM occurrence_grammar").fetchone()[0],
            0,
        )
        db.close()

    def test_reject_requires_reviewer_response(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"negation":"CON-NEG"})
        with self.assertRaises(GrammarWorkflowError):
            resolve_grammar_submission(db,sid,"rejected")
        self.assertEqual(
            db.execute(
                "SELECT status FROM submission WHERE submission_id=?",(sid,)
            ).fetchone()[0],
            "pending",
        )
        resolve_grammar_submission(
            db,
            sid,
            "rejected",
            review_note="La evidencia no permite sostener la propuesta.",
        )
        self.assertEqual(
            tuple(
                db.execute(
                    "SELECT status,resolution,review_note FROM submission "
                    "WHERE submission_id=?",(sid,)
                ).fetchone()
            ),
            (
                "resolved",
                "rejected",
                "La evidencia no permite sostener la propuesta.",
            ),
        )
        db.close()

    def test_accept_versions_complete_block_and_can_clear_field(self):
        db=self.connect(); old,_=create_or_replace_occurrence_grammar(db,1,gender="FEM-A",plural="REDUP.",gender_uncertain=1)
        sid=create_grammar_submission(db,1,{"gender":"","plural":"REDUP."}); resolve_grammar_submission(db,sid,"accepted")
        rows=db.execute("SELECT * FROM occurrence_grammar ORDER BY occurrence_grammar_id").fetchall()
        self.assertEqual([r["is_current"] for r in rows],[0,1]); self.assertIsNone(rows[1]["gender"]); self.assertEqual(rows[1]["plural"],"REDUP."); self.assertEqual(rows[1]["supersedes_occurrence_grammar_id"],old); db.close()

    def test_accept_identical_block_still_records_submission_provenance(self):
        db=self.connect(); old,_=create_or_replace_occurrence_grammar(db,1,gender="FEM-A")
        sid=create_grammar_submission(db,1,{"gender":"FEM-A"}); resolve_grammar_submission(db,sid,"accepted")
        rows=db.execute("SELECT occurrence_grammar_id,is_current,supersedes_occurrence_grammar_id,created_from_submission_id FROM occurrence_grammar ORDER BY occurrence_grammar_id").fetchall()
        self.assertEqual(len(rows),2); self.assertEqual(tuple(rows[0][1:]),(0,None,None)); self.assertEqual(tuple(rows[1][1:]),(1,old,sid)); db.close()

    def test_reject_resolves_without_touching_canonical(self):
        db=self.connect(); old,_=create_or_replace_occurrence_grammar(db,1,negation="SIN-NEG")
        sid=create_grammar_submission(db,1,{"negation":"CON-NEG"}); resolve_grammar_submission(db,sid,"rejected",review_note="No evidence")
        self.assertEqual(db.execute("SELECT occurrence_grammar_id,negation FROM occurrence_grammar WHERE is_current=1").fetchone()[0],old)
        self.assertEqual(tuple(db.execute("SELECT status,resolution FROM submission WHERE submission_id=?",(sid,)).fetchone()),("resolved","rejected")); db.close()

    def test_accept_rollback_keeps_submission_pending_and_current(self):
        db=self.connect(); old,_=create_or_replace_occurrence_grammar(db,1,gender="FEM-A")
        sid=create_grammar_submission(db,1,{"gender":"MASC-O"})
        db.execute("CREATE TRIGGER fail_grammar BEFORE INSERT ON occurrence_grammar BEGIN SELECT RAISE(ABORT,'synthetic'); END"); db.commit()
        with self.assertRaises(sqlite3.IntegrityError): resolve_grammar_submission(db,sid,"accepted")
        self.assertEqual(db.execute("SELECT status FROM submission WHERE submission_id=?",(sid,)).fetchone()[0],"pending")
        self.assertEqual(db.execute("SELECT occurrence_grammar_id FROM occurrence_grammar WHERE is_current=1").fetchone()[0],old); db.close()

    def test_assignment_and_grammar_are_independent(self):
        db=self.connect(); db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)"); db.commit()
        before=tuple(db.execute("SELECT * FROM assignment").fetchone()); sid=create_grammar_submission(db,1,{"agentive":"N/A"}); resolve_grammar_submission(db,sid,"accepted")
        self.assertEqual(tuple(db.execute("SELECT * FROM assignment").fetchone()),before); db.close()

    def test_form_prefills_complete_current_and_displays_legacy_value(self):
        db=self.connect(); create_or_replace_occurrence_grammar(db,1,gender="LEGACY-VALUE",plural="REDUP.",gender_uncertain=1); db.close()
        page=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True)
        self.assertIn("LEGACY-VALUE (legacy)",page); self.assertIn('value="REDUP." selected',page); self.assertRegex(page,r'name="gender_uncertain"\s+checked')

    def test_review_lists_alternative_and_reject_does_not_modify_canonical(self):
        db=self.connect(); cur=db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'ALTERNATIVE','pending')"); sid=cur.lastrowid
        db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id,is_legacy) VALUES(?,'UNSURE',1,1)",(sid,)); db.commit(); before=tuple(db.execute("SELECT * FROM submission WHERE submission_id=?",(sid,)).fetchone()); db.close()
        page=self.client.get("/aportes/pendientes").get_data(as_text=True); self.assertIn("Propuesta del analista",page); self.assertIn("DECISIÓN DEL REVISOR",page)
        db=self.connect(); canonical_before=tuple(db.execute("SELECT count(*) FROM alternative").fetchone())+tuple(db.execute("SELECT count(*) FROM assignment").fetchone()); db.close()
        db=self.connect(); save_resolution(db,sid,"CONFIRM_REFERENCE",access_role="reviewer"); db.close()
        self.assertEqual(self.client.post(f"/aportes/{sid}/decidir",data={"decision":"rejected"}).status_code,302)
        db=self.connect(); row=db.execute("SELECT status,resolution FROM submission WHERE submission_id=?",(sid,)).fetchone(); canonical_after=tuple(db.execute("SELECT count(*) FROM alternative").fetchone())+tuple(db.execute("SELECT count(*) FROM assignment").fetchone()); db.close()
        self.assertEqual(tuple(row),("resolved","rejected")); self.assertEqual(canonical_after,canonical_before)

    def test_review_ui_prefills_values_and_keeps_uncertainty_read_only(self):
        db=self.connect()
        create_grammar_submission(
            db,
            1,
            {
                "gender":"MASC-O",
                "gender_uncertain":1,
                "note":"Duda documentada.",
            },
        )
        db.close()
        page=self.client.get("/aportes/pendientes").get_data(as_text=True)
        self.assertIn("DECISIÓN DEL REVISOR",page)
        self.assertIn("Restablecer propuesta del analista",page)
        self.assertIn("Se ha modificado la propuesta original del analista.",page)
        self.assertIn('name="reviewed_gender"',page)
        self.assertIn('value="MASC-O" selected',page)
        self.assertIn('data-has-uncertainty="1"',page)
        self.assertIn("Respuesta del revisor",page)
        self.assertNotIn('name="reviewed_gender_uncertain"',page)

    def test_review_route_accepts_reviewer_correction(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"gender":"MASC-O"})
        db.close()
        response=self.client.post(
            f"/aportes/{sid}/decidir",
            data={
                "decision":"accepted",
                "reviewed_gender":"FEM-A",
                "reviewed_plural":"",
                "reviewed_agentive":"",
                "reviewed_conjugated_form":"",
                "reviewed_negation":"",
                "review_note":"Se corrige el género.",
            },
        )
        self.assertEqual(response.status_code,302)
        db=self.connect()
        self.assertEqual(
            tuple(
                db.execute(
                    "SELECT gender,gender_uncertain FROM grammar_submission "
                    "WHERE submission_id=?",(sid,)
                ).fetchone()
            ),
            ("MASC-O",0),
        )
        self.assertEqual(
            tuple(
                db.execute(
                    "SELECT gender,gender_uncertain,change_note "
                    "FROM occurrence_grammar WHERE is_current=1"
                ).fetchone()
            ),
            ("FEM-A",0,"Se corrige el género."),
        )
        db.close()

    def test_review_route_reject_requires_response(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"plural":"REDUP."})
        db.close()
        self.assertEqual(
            self.client.post(
                f"/aportes/{sid}/decidir",
                data={"decision":"rejected"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/aportes/{sid}/decidir",
                data={
                    "decision":"rejected",
                    "review_note":"No se confirma en la evidencia.",
                },
            ).status_code,
            302,
        )

    def test_review_route_accept_without_changes_and_without_doubt_needs_no_note(self):
        db=self.connect()
        sid=create_grammar_submission(db,1,{"gender":"FEM-A"})
        db.close()
        self.assertEqual(
            self.client.post(
                f"/aportes/{sid}/decidir",
                data={
                    "decision":"accepted",
                    "reviewed_gender":"FEM-A",
                    "reviewed_plural":"",
                    "reviewed_agentive":"",
                    "reviewed_conjugated_form":"",
                    "reviewed_negation":"",
                },
            ).status_code,
            302,
        )


    def test_grammar_forms_include_spanish_validation_messages(self):
        page=self.client.get("/ocurrencias/1/gramatica").get_data(as_text=True)
        self.assertIn(
            "Cuando se marcan uno o más campos con duda, la nota del analista debe explicar el motivo.",
            page,
        )

        db=self.connect()
        create_grammar_submission(
            db,
            1,
            {
                "gender":"MASC-O",
                "gender_uncertain":1,
                "note":"Duda documentada.",
            },
        )
        db.close()

        page=self.client.get("/aportes/pendientes").get_data(as_text=True)
        self.assertIn(
            "Para aceptar la propuesta, es necesario responder la nota del analista y resolver la duda.",
            page,
        )
        self.assertNotIn(
            'aria-label="Respuesta del revisor" required',
            page,
        )

    def test_resolved_detail_reports_exact_reviewer_changes(self):
        db=self.connect()
        sid=create_grammar_submission(
            db,
            1,
            {
                "gender":"MASC-HOMBRE",
                "gender_uncertain":1,
                "note":"Nota original.",
            },
        )
        resolve_grammar_submission(
            db,
            sid,
            "accepted",
            reviewed_values={
                "gender":"MASC-O",
                "plural":"",
                "agentive":"",
                "conjugated_form":"",
                "negation":"",
            },
            review_note="Respuesta del revisor.",
        )
        db.close()

        page=self.client.get(f"/aportes/{sid}").get_data(as_text=True)
        self.assertIn("Detalle del aporte",page)
        self.assertIn("REVISIÓN REALIZADA",page)
        self.assertIn("Aceptado con cambios",page)
        self.assertIn("MASC-HOMBRE",page)
        self.assertIn("MASC-O",page)
        self.assertIn("Nota original.",page)
        self.assertIn("Respuesta del revisor.",page)
        self.assertIn(
            "La propuesta original contenía al menos un campo marcado con duda.",
            page,
        )
        self.assertIn(
            "el resultado canónico se registró sin marcas de duda.",
            page,
        )
        self.assertNotIn(
            "El Revisor/Master debe resolverla",
            page,
        )

    def test_aportes_distinguishes_grammar_acceptance_with_and_without_changes(self):
        db=self.connect()

        first=create_grammar_submission(db,1,{"gender":"FEM-A"})
        resolve_grammar_submission(db,first,"accepted")

        second=create_grammar_submission(db,1,{"gender":"MASC-O"})
        resolve_grammar_submission(
            db,
            second,
            "accepted",
            reviewed_values={
                "gender":"FEM-A",
                "plural":"",
                "agentive":"",
                "conjugated_form":"",
                "negation":"",
            },
            review_note="Corrección documentada.",
        )
        db.close()

        page=self.client.get("/aportes").get_data(as_text=True)
        self.assertIn("Aceptado sin cambios",page)
        self.assertIn("Aceptado con cambios",page)



if __name__ == "__main__": unittest.main()
