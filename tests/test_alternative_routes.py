from submission_concept_resolution import save_resolution
import sqlite3
import tempfile
import unittest
import re
import html
from pathlib import Path

from flask import Flask, g

import database
from concept_labels import alternative_display_label, human_concept_label
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from routes.alternatives import alternatives_bp
from routes.concepts import concepts_bp
from alternative_preconditions import relevant_state
from edit_concurrency import fingerprint, sign

ROOT=Path(__file__).resolve().parents[1]


class AlternativeRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"db.sqlite"; self.old=database.BASE_DATOS; database.BASE_DATOS=self.path
        db=self.connect(); database.crear_esquema(db); db.execute("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES('Synthetic',2000,2000,'known')"); db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')")
        for gloss,year in (("KNOWN",2000),("TO-ANALYZE",2001),("TARGET",2002)):
            oid=db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,?,?)",(gloss,year)).lastrowid; db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)",(oid,))
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1')"); db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)"); db.commit(); db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates")); app.testing=True; app.jinja_env.filters.update(human_concept_label=human_concept_label,alternative_display_label=alternative_display_label); app.register_blueprint(occurrences_bp); app.register_blueprint(submissions_bp); app.register_blueprint(alternatives_bp); app.register_blueprint(concepts_bp); self.client=app.test_client()
        @app.before_request
        def reviewer_context():
            g.current_access_role='reviewer'
    def tearDown(self): database.BASE_DATOS=self.old; self.tmp.cleanup()
    def connect(self): db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db

    def resolve_pending_concepts(self):
        db=self.connect()
        for row in db.execute("SELECT s.submission_id FROM submission s LEFT JOIN submission_concept_resolution r ON r.submission_id=s.submission_id AND r.is_current=1 WHERE s.submission_type='ALTERNATIVE' AND s.status='pending' AND r.submission_id IS NULL").fetchall():
            save_resolution(db,row[0],'CONFIRM_REFERENCE',access_role='reviewer')
        db.close()

    def review_post(self, path, **kwargs):
        sid=int(path.split('/')[-2])
        db=self.connect()
        save_resolution(db,sid,'CONFIRM_REFERENCE',access_role='reviewer')
        db.close()
        from tests.form_client import hidden
        kwargs["data"]["lexical_preview_token"] = hidden(self.client.get(path.removesuffix("/decidir")).get_data(as_text=True), "lexical_preview_token")
        return self.client.post(path,**kwargs)

    def test_analysis_page_shows_context_canonical_and_pending_proposals(self):
        self.client.post("/ocurrencias/3/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"})
        page=self.client.get("/ocurrencias/2/clasificar").get_data(as_text=True)
        for text in ("TO-ANALYZE","Concepto","TEST-1","KNOWN","OCC-000003 · TARGET","TARGET"):
            self.assertIn(text,page)

    def test_alternatives_page_orders_working_labels_structurally(self):
        db=self.connect()
        db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=1")
        db.executemany(
            "INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",
            [("10a",), ("2a",), ("unexpected",), ("1c",), ("3a",), ("1b",)],
        )
        db.commit(); db.close()
        page=self.client.get("/conceptos/1/alternativas").get_data(as_text=True)
        positions=[page.index("TEST-" + label) for label in ("1a", "1b", "1c", "2a", "3a", "10a", "unexpected")]
        self.assertEqual(positions, sorted(positions))

    def test_alternatives_page_orders_occurrences_by_temporal_reference(self):
        db = self.connect()
        db.executemany(
            "INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES(?,?,?,?)",
            [
                ("Range", 2010, 2020, "known"),
                ("Single", 2015, 2015, "known"),
                ("Older source", 1900, 1900, "known"),
                ("Undated", None, None, None),
            ],
        )
        occurrences = [
            (2, "RANGE", None),
            (3, "SINGLE", None),
            (4, "OCCURRENCE YEAR", 2026),
            (5, "UNDATED", None),
            (1, "SAME YEAR", 2000),
        ]
        for source_id, gloss, year in occurrences:
            occurrence_id = db.execute(
                "INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(?,?,?)",
                (source_id, gloss, year),
            ).lastrowid
            db.execute(
                "INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,1)",
                (occurrence_id,),
            )
        db.commit()
        db.close()

        page = self.client.get("/conceptos/1/alternativas").text
        positions = [page.index(f"OCC-{occurrence_id:06d}") for occurrence_id in (1, 8, 4, 5, 6, 7)]
        self.assertEqual(positions, sorted(positions))

    def test_alternatives_page_keeps_each_alternative_occurrence_order_independent(self):
        db = self.connect()
        db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=1")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2a')")
        occurrence_id = db.execute(
            "INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'EARLIER',1999)",
        ).lastrowid
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,1)", (occurrence_id,))
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(2,2)")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(3,2)")
        db.commit()
        db.close()

        page = self.client.get("/conceptos/1/alternativas").text
        first_alternative = page.split("TEST-1a", 1)[1].split("TEST-2a", 1)[0]
        second_alternative = page.split("TEST-2a", 1)[1]
        self.assertLess(first_alternative.index("OCC-000004"), first_alternative.index("OCC-000001"))
        self.assertLess(second_alternative.index("OCC-000002"), second_alternative.index("OCC-000003"))

    def test_isolated_alternative_keeps_move_option(self):
        page = self.client.get("/alternativas/1/gestionar").get_data(as_text=True)
        self.assertIn('name="action" value="preview_move"', page)

    def test_related_alternative_shows_group_move_and_no_individual_form(self):
        db = self.connect()
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2a')")
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        db.commit(); db.close()
        page = self.client.get("/alternativas/1/gestionar").get_data(as_text=True)
        self.assertIn("Mover grupo a otro concepto", page)
        move_section = page.split("<fieldset><legend>Mover grupo a otro concepto</legend>", 1)[1].split("</fieldset>", 1)[0]
        self.assertNotIn('name="action" value="preview_move"', move_section)
        self.assertNotIn("Relaciones retiradas", move_section)
        self.assertIn('value="preview_component_move"', move_section)
        self.assertIn('ID 1', move_section)
        self.assertIn('ID 2', move_section)

    def group_fixture(self):
        db=self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('DESTINATION')")
        db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)", [('2a',),('3a',),('4a',)])
        db.executemany("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,'CM_1')", [(1,2),(2,3)])
        db.commit();db.close()

    def test_group_preview_and_confirmation_end_to_end(self):
        self.group_fixture()
        response=self.client.post('/alternativas/2/gestionar',data={'action':'preview_component_move','destination_concept_id':'2'})
        self.assertEqual(response.status_code,200)
        page=response.get_data(as_text=True)
        for text in ('TRASLADO DE GRUPO','Origen: TEST','Destino: DESTINATION','ID 1','ID 2','ID 3','Relaciones preservadas: 2','Confirmar traslado del grupo','VISTA PREVIA DE CAMBIOS — ORIGEN','VISTA PREVIA DE CAMBIOS — DESTINO'):
            self.assertIn(text,page)
        token=html.unescape(re.search(r'name="preview_token" value="([^"]+)"',page)[1])
        data={'action':'confirm_component_move','destination_concept_id':'2','reason':'Traslado','confirm':'yes','preview_token':token}
        changed=dict(data,destination_concept_id='1')
        self.assertEqual(self.client.post('/alternativas/2/gestionar',data=changed).status_code,409)
        self.assertEqual(self.client.post('/alternativas/2/gestionar',data=dict(data,reason='')).status_code,400)
        self.assertEqual(self.client.post('/alternativas/2/gestionar',data=data).status_code,302)
        db=self.connect()
        self.assertEqual(dict(db.execute('SELECT alternative_id,concept_id FROM alternative')),{1:2,2:2,3:2,4:1})
        self.assertEqual(db.execute('SELECT count(*) FROM alternative_relation WHERE is_current=1').fetchone()[0],2)
        db.close()
        self.assertEqual(self.client.post('/alternativas/2/gestionar',data=data).status_code,409)

    def test_group_analyst_cannot_preview_or_confirm(self):
        self.group_fixture()
        @self.client.application.before_request
        def analyst_context():
            g.current_access_role='analyst'
        for action in ('preview_component_move','confirm_component_move'):
            self.assertEqual(self.client.post('/alternativas/2/gestionar',data={'action':action,'destination_concept_id':'2'}).status_code,404)

    def test_group_legacy_integrity_error_renders_without_move_form(self):
        self.group_fixture()
        db=self.connect();db.execute('UPDATE alternative SET concept_id=2 WHERE alternative_id=3');db.commit();db.close()
        response=self.client.get('/alternativas/2/gestionar')
        self.assertEqual(response.status_code,200)
        page=response.get_data(as_text=True)
        self.assertIn('cross-concept',page)
        self.assertNotIn('value="preview_component_move"',page)

    def test_structural_operations_render_utf8_texts_and_no_mojibake(self):
        page = self.client.get("/alternativas/1/gestionar").get_data(as_text=True)
        for text in ("previsualización", "confirmación", "fusión", "división", "—", "→"):
            self.assertIn(text, page)
        for bad in ("Ã", "â€”", "â†’"):
            self.assertNotIn(bad, page)

    def test_move_block_preserves_isolated_alternative_button_text(self):
        page = self.client.get("/alternativas/1/gestionar").get_data(as_text=True)
        self.assertIn("Previsualizar movimiento", page)

        db = self.connect()
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2a')")
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        db.commit(); db.close()
        page_related = self.client.get("/alternativas/1/gestionar").get_data(as_text=True)
        self.assertIn("Mover grupo a otro concepto", page_related)
        self.assertNotIn("Previsualizar movimiento", page_related.split("<fieldset><legend>Mover grupo a otro concepto</legend>", 1)[1].split("</fieldset>", 1)[0])

    def test_forced_move_post_is_rejected_without_changes(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('DESTINATION')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'2a')")
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        db.commit()
        with self.client.application.app_context():
            token = sign({
                "purpose": "structural-alternative",
                "source_id": 1,
                "spec": {"kind": "move", "destination_concept_id": 2},
                "fingerprint": fingerprint(relevant_state(db, 1, 2)),
            })
        before = "\n".join(db.iterdump())
        db.close()
        response = self.client.post("/alternativas/1/gestionar", data={
            "action": "confirm_move",
            "destination_concept_id": "2",
            "preview_token": token,
            "confirm": "yes",
            "reason": "Reclasificación",
        })
        self.assertEqual(response.status_code, 400)
        db = self.connect()
        self.assertEqual(before, "\n".join(db.iterdump()))
        self.assertEqual(db.execute("SELECT concept_id FROM alternative WHERE alternative_id=1").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT is_current FROM alternative_relation WHERE alternative_low_id=1 AND alternative_high_id=2").fetchone()[0], 1)
        db.close()

    def test_analysis_page_progressive_disclosure_and_singular_count(self):
        page=self.client.get("/ocurrencias/2/clasificar").get_data(as_text=True)
        self.assertIn("TEST-1 · ID 1 — 1 ocurrencia",page);self.assertNotIn("1 ocurrencias",page)
        self.assertIn('id="existing-alternative-field" hidden',page);self.assertIn("existing.hidden=!isExisting",page)
        self.assertIn('id="permutation-field" hidden',page)
        self.assertIn("¿Se identificaron componentes?",page)
        self.assertIn('name="record_components" value="no" checked',page)
        self.assertIn('<div id="components"></div>',page);self.assertIn('id="component-template"',page)
        self.assertNotIn('<div id="components"><div class="component">',page)
        self.assertNotIn("list.replaceChildren()",page)

    def test_classification_comparator_id_only_in_summary(self):
        response = self.client.get('/ocurrencias/2/clasificar')
        self.assertEqual(200, response.status_code)
        comparator = response.text.split('<h2>Alternativas vigentes</h2>')[1].split('</section>')[0]
        self.assertIn('<summary>TEST-1 · ID 1 — 1 ocurrencia</summary>', comparator)
        self.assertNotIn('ID 1', comparator.split('</summary>')[1])

    def test_classification_comparator_temporal_priority(self):
        cases = (
            (2014, 2012, 2017, 'known', ' · 2014'),
            (None, 2001, 2001, 'known', ' · 2001'),
            (None, 2012, 2017, 'known', ' · 2012–2017'),
            (None, 2012, None, 'ongoing', ' · 2012–en curso'),
            (None, 2012, None, 'unknown', ' · 2012–final desconocido'),
            (None, None, None, None, ''),
            (None, None, None, 'known', ''),
            (None, None, 2017, 'known', ' · 2017'),
        )
        for year, start, end, status, suffix in cases:
            with self.subTest(year=year, start=start, end=end, status=status):
                db = self.connect()
                db.execute('UPDATE occurrence SET occurrence_year=? WHERE occurrence_id=1', (year,))
                db.execute('UPDATE source SET start_year=?,end_year=?,end_year_status=?', (start, end, status))
                db.commit(); db.close()
                response = self.client.get('/ocurrencias/2/clasificar')
                self.assertEqual(200, response.status_code)
                self.assertIn('<p>OCC-000001 · KNOWN · Synthetic' + suffix + '</p>', response.text)

    def test_classification_comparator_preserves_current_order(self):
        db = self.connect()
        db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=1")
        db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(1,?)',
                       [('10a',), ('2a',), ('1c',), ('3a',), ('1b',)])
        expected = db.execute('SELECT alternative_id,working_label FROM alternative ORDER BY working_label').fetchall()
        db.commit(); db.close()
        page = self.client.get('/ocurrencias/2/clasificar').text
        comparator = page.split('<h2>Alternativas vigentes</h2>')[1].split('</section>')[0]
        positions = [comparator.index('<summary>TEST-' + row['working_label'] + ' · ID ' + str(row['alternative_id'])) for row in expected]
        self.assertEqual(positions, sorted(positions))

    def test_count_one_discards_stale_permutation_and_components(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"1","free_permutation":"SIN INFORMACIÓN","record_components":"yes","component_position":"1","component_type":"unapproved","component_alternative_id":"","component_note":"STALE"})
        self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0]
        self.assertEqual(tuple(db.execute("SELECT component_count,free_permutation FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()),(1,"N/A"))
        self.assertEqual(db.execute("SELECT count(*) FROM alternative_submission_component WHERE submission_id=?",(sid,)).fetchone()[0],0);db.close()

    def test_na_normalizes_permutation_and_allows_optional_component(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A","free_permutation":"SÍ","record_components":"yes","component_position":"1","component_type":"unapproved","component_alternative_id":"","component_note":"EXPLÍCITO"})
        self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0]
        self.assertEqual(tuple(db.execute("SELECT component_count,component_count_not_applicable,free_permutation FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()),(None,1,"N/A"))
        self.assertEqual(tuple(db.execute("SELECT component_alternative_id,note FROM alternative_submission_component WHERE submission_id=?",(sid,)).fetchone()),(None,"EXPLÍCITO"));db.close()

    def test_component_type_validation_and_unapproved_creates_no_entities(self):
        base={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"2","free_permutation":"NO","record_components":"yes","component_position":"1"}
        self.assertEqual(self.client.post("/ocurrencias/2/clasificar",data=dict(base,component_type="existing",component_alternative_id="999",component_note="")).status_code,400)
        self.assertEqual(self.client.post("/ocurrencias/2/clasificar",data=dict(base,component_type="unapproved",component_alternative_id="",component_note="")).status_code,400)
        response=self.client.post("/ocurrencias/2/clasificar",data=dict(base,component_type="unapproved",component_alternative_id="",component_note="Forma dudosa por revisar"));self.assertEqual(response.status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT count(*) FROM alternative").fetchone()[0],1);self.assertEqual(db.execute("SELECT count(*) FROM concept_proposal").fetchone()[0],0)
        self.assertEqual(tuple(db.execute("SELECT component_alternative_id,note FROM alternative_submission_component").fetchone()),(None,"Forma dudosa por revisar"));db.close()

    def test_new_reviewer_explicit_morphology_and_nomenclature_copy(self):
        db=self.connect();db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=1");db.commit();db.close()
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"})
        self.resolve_pending_concepts()
        page=self.client.get("/aportes/pendientes").get_data(as_text=True)
        self.assertIn("Aceptar la propuesta del analista: crear nueva alternativa",page)
        self.assertIn('class="new-decision-controls" hidden',page);self.assertIn('class="existing-decision-controls" hidden',page)
        self.assertIn('name="morphology_resolution" value="pending" checked',page)
        self.assertNotIn('name="approve_morphology"',page)
        self.assertIn("Las etiquetas de las alternativas existentes no cambian. La nueva alternativa se creará como TEST-2a.",page)
        self.assertIn("TEST-1a · ID 1 — 1 ocurrencia",page);self.assertNotIn("1 ocurrencias",page)
        for text in ("Estado","= Sin cambio","+ Nueva","0 alternativas existentes cambian. Se creará 1 alternativa nueva."):
            self.assertIn(text,page)
        self.assertIn("Los cambios de nomenclatura se aplicarán al confirmar la decisión del revisor.",page)
        self.assertIn('class="nomenclature-label" name="label_1" value="1a" readonly',page)
        self.assertIn("input.readOnly=!editable",page)

    def test_existing_reviewer_decisions_and_changed_nomenclature_warning(self):
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"EXISTING","proposed_existing_alternative_id":"1"})
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];db.close()
        self.resolve_pending_concepts()
        page=self.client.get("/aportes/pendientes").get_data(as_text=True)
        for text in ("Aceptar la alternativa propuesta por el analista","Asignar a otra alternativa existente","Crear una nueva alternativa","Rechazar el resto del análisis"):
            self.assertIn(text,page)
        self.assertIn("Esta operación modificará la nomenclatura de 1 alternativa existente.",page)
        self.assertIn("↻ Cambia",page);self.assertIn("1 alternativa existente cambia. Se creará 1 alternativa nueva.",page)
        self.assertEqual(self.review_post(f"/aportes/{sid}/decidir",data={"decision":"existing_proposed"}).status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0],1);db.close()

    def test_route_creates_existing_submission_not_assignment(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"EXISTING","proposed_existing_alternative_id":"1"})
        self.assertEqual(response.status_code,302); db=self.connect(); self.assertEqual(tuple(db.execute("SELECT s.submission_type,s.status,a.proposal_kind,a.proposed_existing_alternative_id FROM submission s JOIN alternative_submission a USING(submission_id)").fetchone()),("ALTERNATIVE","pending","EXISTING",1)); self.assertEqual(db.execute("SELECT count(*) FROM assignment WHERE occurrence_id=2").fetchone()[0],0); db.close()

    def test_route_review_existing_materializes_assignment(self):
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"UNSURE","analysis_note":"Revisar"}); db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];db.close()
        response=self.review_post(f"/aportes/{sid}/decidir",data={"decision":"existing","alternative_id":"1","relation_policy":"preserve","review_note":"Resolver la incertidumbre"}); self.assertEqual(response.status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=2 AND is_current=1").fetchone()[0],1);db.close()

    def test_route_review_new_auto_and_legacy_detail_read_only(self):
        self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"});db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];db.close()
        self.resolve_pending_concepts()
        review=self.client.get("/aportes/pendientes").get_data(as_text=True); self.assertIn("Propuesta: nueva alternativa",review); self.assertIn("VISTA PREVIA DE CAMBIOS",review); self.assertIn("DECISIÓN DEL REVISOR",review)
        self.assertEqual(self.review_post(f"/aportes/{sid}/decidir",data={"decision":"new","approve_relations":"no","approve_morphology":"yes","nomenclature_mode":"automatic"}).status_code,302)
        db=self.connect();self.assertEqual(db.execute("SELECT count(*) FROM alternative").fetchone()[0],2);snapshot=tuple(db.execute("SELECT * FROM submission WHERE submission_id=?",(sid,)).fetchone());db.close()
        self.assertEqual(self.client.get(f"/aportes/{sid}").status_code,200);db=self.connect();self.assertEqual(tuple(db.execute("SELECT * FROM submission WHERE submission_id=?",(sid,)).fetchone()),snapshot);db.close()

    def test_route_captures_and_explicitly_approves_morphology(self):
        response=self.client.post("/ocurrencias/2/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","record_morphology":"yes","morphology_component_count":"2","free_permutation":"SIN INFORMACIÓN","morphology_note":"Synthetic morphology","record_components":"yes","component_position":["1","2"],"component_type":["existing","unapproved"],"component_alternative_id":["1",""],"component_note":["Known","FREE"]});self.assertEqual(response.status_code,302)
        db=self.connect();sid=db.execute("SELECT submission_id FROM submission").fetchone()[0];self.assertEqual(db.execute("SELECT component_count FROM alternative_submission_morphology WHERE submission_id=?",(sid,)).fetchone()[0],2);db.close()
        self.resolve_pending_concepts()
        review=self.client.get("/aportes/pendientes").get_data(as_text=True);self.assertIn("Morfología propuesta por el analista",review);self.assertIn("Rechazar morfología propuesta",review);self.assertIn("Aceptar morfología propuesta",review)
        response=self.review_post(f"/aportes/{sid}/decidir",data={"decision":"new","approve_relations":"no","approve_morphology":"yes","nomenclature_mode":"automatic"});self.assertEqual(response.status_code,302)
        db=self.connect();row=db.execute("SELECT m.created_from_submission_id,count(c.alternative_component_id) FROM alternative_morphology m LEFT JOIN alternative_component c USING(alternative_morphology_id) WHERE m.is_current=1 GROUP BY m.alternative_morphology_id").fetchone();self.assertEqual(tuple(row),(sid,2));db.close()

    def test_existing_requires_and_forwards_explicit_group_decision(self):
        self.client.post('/ocurrencias/2/clasificar',data={'proposal_kind':'NEW','phonological_relation_answer':'NO','morphology_component_count':'N/A'})
        db=self.connect();sid=db.execute('SELECT submission_id FROM submission').fetchone()[0];db.close()
        form={'decision':'existing','alternative_id':'1','review_note':'Conservar morfolog?a del destino'}
        self.assertEqual(400,self.review_post(f'/aportes/{sid}/decidir',data=form).status_code)
        db=self.connect()
        self.assertEqual('pending',db.execute('SELECT status FROM submission WHERE submission_id=?',(sid,)).fetchone()[0])
        self.assertEqual(0,db.execute('SELECT count(*) FROM assignment WHERE occurrence_id=2').fetchone()[0]);db.close()
        form['morphology_resolution']='REJECTED'
        self.assertEqual(302,self.review_post(f'/aportes/{sid}/decidir',data=form).status_code)
        db=self.connect()
        self.assertEqual('REJECTED',db.execute('SELECT morphology_resolution FROM submission_lexical_decision WHERE submission_id=?',(sid,)).fetchone()[0]);db.close()

    def test_new_group_decisions_are_explicit_through_normal_route(self):
        self.client.post('/ocurrencias/2/clasificar',data={'proposal_kind':'NEW','phonological_relation_answer':'NO','morphology_component_count':'N/A'})
        db=self.connect();sid=db.execute('SELECT submission_id FROM submission').fetchone()[0];db.close()
        path=f'/aportes/{sid}/decidir'
        self.assertEqual(400,self.review_post(path,data={'decision':'new'}).status_code)
        db=self.connect()
        self.assertEqual(1,db.execute('SELECT count(*) FROM alternative').fetchone()[0])
        self.assertEqual('pending',db.execute('SELECT status FROM submission').fetchone()[0]);db.close()
        self.assertEqual(302,self.review_post(path,data={'decision':'new','morphology_resolution':'ACCEPTED'}).status_code)
        db=self.connect()
        self.assertEqual(('CREATE_NEW','ACCEPTED'),tuple(db.execute('SELECT decision_action,morphology_resolution FROM submission_lexical_decision').fetchone()));db.close()


if __name__=="__main__": unittest.main()
