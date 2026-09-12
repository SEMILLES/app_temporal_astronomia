from alternative_video_service import get_current_video
from alternative_workflow import _relation_targets
import re
from submission_lexical_decision import get_decision
from submission_concept_resolution import save_resolution, current_resolution, resolution_history
from edit_concurrency import edit_token, StaleEdit
import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for, g

from database import conectar
from grammar_workflow import GrammarWorkflowError, resolve_grammar_submission
from grammatical_marks import GRAMMATICAL_MARK_VOCABULARIES
from occurrence_registration import RegistrationError, complete_registration, save_draft
from alternative_workflow import (
    AlternativeWorkflowError, reject_alternative_submission,
    review_as_existing, review_as_new,
)
from alternative_nomenclature import calculate_nomenclature_preview
from alternative_morphology import submission_morphology
from concept_labels import alternative_display_label
from source_period import format_source_period
from immediate_acceptance import (ImmediateAcceptanceError, run_normal_review,
    concept_registration_operation,preview_operation,confirm_operation)
from access_control import requires_reviewer

submissions_bp = Blueprint("submissions", __name__)
submissions_bp.add_app_template_filter(format_source_period, "source_period")


def _context(db, draft=None, error=None):
    return dict(
        fuentes=db.execute("SELECT source_id, source_name, source_type, start_year, end_year, end_year_status FROM source WHERE retired_at IS NULL ORDER BY source_name").fetchall(),
        conceptos=db.execute("SELECT concept_id, preferred_label FROM concept ORDER BY preferred_label").fetchall(),
        propuestas=db.execute("SELECT concept_proposal_id, proposed_label FROM concept_proposal WHERE status='pending' ORDER BY proposed_label").fetchall(),
        draft=draft, error=error,
    )


def _evidence(form):
    return {name: form.get(name) for name in (
        "source_id", "original_gloss", "occurrence_year", "source_detail_1",
        "source_detail_2", "source_detail_1_status", "source_detail_2_status", "usage_examples_present", "grammatical_info_present",
        "grammatical_note", "source_locator", "provenance_note", "hyperlink",
    )}


def _reference(form):
    kind = form.get("reference_kind")
    return dict(
        concept_id=form.get("reference_concept_id") if kind == "concept" else None,
        concept_proposal_id=form.get("reference_concept_proposal_id") if kind == "proposal" else None,
        proposed_label=form.get("proposed_label") if kind == "new" else None,
    )


def _grammar_review_values(form):
    fields = tuple(GRAMMATICAL_MARK_VOCABULARIES)
    if not any(f"reviewed_{field}" in form for field in fields):
        return None
    return {field: form.get(f"reviewed_{field}") for field in fields}


@submissions_bp.route("/aportes/nuevo")
def nuevo_aporte():
    db = conectar()
    try:
        context = _context(db)
    finally:
        db.close()
    return render_template("nueva_ocurrencia.html", **context)


@submissions_bp.route("/aportes", methods=["POST"])
@submissions_bp.route("/ocurrencias/guardar", methods=["POST"])
def guardar_aporte():
    role=getattr(g,"current_access_role",None)
    if (role in ("reviewer","master") and request.form.get("reference_kind")=="new"
            and request.form.get("concept_immediate_action") in ("new","existing")):
        db=conectar()
        try:result=preview_operation(db,_concept_immediate_operation(request.form))
        except (ValueError,sqlite3.IntegrityError) as error:
            context=_context(db,error=str(error));return render_template("nueva_ocurrencia.html",**context),400
        finally:db.close()
        return render_template("confirmar_aceptacion_inmediata.html",kind="concept",occurrence_id=None,payload=list(request.form.lists()),preflight=result,summary=None)
    db = conectar()
    try:
        occurrence_id = complete_registration(db, **_evidence(request.form), **_reference(request.form), collaborator_id=request.form.get("collaborator_id"), access_role=getattr(g, "current_access_role", None))
    except (RegistrationError, sqlite3.IntegrityError, ValueError) as error:
        context = _context(db, error=str(error))
        return render_template("nueva_ocurrencia.html", **context), 400
    finally:
        db.close()
    return redirect(url_for("occurrences.mostrar_gramatica", occurrence_id=occurrence_id, flow="registration"))


def _concept_immediate_operation(form):
    if form.get("reference_kind")!="new":raise ImmediateAcceptanceError("La aceptación conceptual inmediata exige proponer un concept nuevo.")
    decision={"action":form.get("concept_immediate_action"),"concept_id":form.get("concept_immediate_existing_id") or None,"label":form.get("proposed_label")}
    return concept_registration_operation(_evidence(form),form.get("proposed_label"),decision,actor_context={"collaborator_id":form.get("collaborator_id"),"access_role":getattr(g,"current_access_role",None)})


@submissions_bp.post("/aportes/concepto/aceptacion-inmediata/preview")
@requires_reviewer
def preview_concept_immediate():
    db=conectar()
    try:result=preview_operation(db,_concept_immediate_operation(request.form))
    except (ValueError,sqlite3.IntegrityError) as error:return str(error),400
    finally:db.close()
    return render_template("confirmar_aceptacion_inmediata.html",kind="concept",occurrence_id=None,payload=list(request.form.lists()),preflight=result)


@submissions_bp.post("/aportes/concepto/aceptacion-inmediata/confirmar")
@requires_reviewer
def confirm_concept_immediate():
    if request.form.get("confirm_immediate")!="yes":return "Se requiere confirmación explícita de la aceptación inmediata.",400
    db=conectar()
    try:result=confirm_operation(db,_concept_immediate_operation(request.form))
    except (ValueError,sqlite3.IntegrityError) as error:return str(error),400
    finally:db.close()
    return redirect(url_for("occurrences.mostrar_gramatica",occurrence_id=result["result"]["occurrence_id"],flow="registration"))


@submissions_bp.route("/borradores")
def borradores():
    db = conectar()
    try:
        rows = db.execute("SELECT d.*, s.source_name FROM occurrence_draft d LEFT JOIN source s ON s.source_id=d.source_id ORDER BY d.updated_at DESC").fetchall()
    finally:
        db.close()
    return render_template("borradores.html", drafts=rows)


@submissions_bp.route("/borradores/guardar", methods=["POST"])
@submissions_bp.route("/borradores/<int:draft_id>/guardar", methods=["POST"])
def guardar_borrador(draft_id=None):
    values = _evidence(request.form)
    values.pop("hyperlink")
    refs = _reference(request.form)
    values.update(reference_concept_id=refs["concept_id"], reference_concept_proposal_id=refs["concept_proposal_id"])
    db = conectar()
    try:
        save_draft(db, draft_id=draft_id, collaborator_id=request.form.get("collaborator_id"), access_role=getattr(g, "current_access_role", None), **values)
    except (RegistrationError, sqlite3.IntegrityError, ValueError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("submissions.borradores"))


@submissions_bp.route("/borradores/<int:draft_id>/editar")
def editar_borrador(draft_id):
    db = conectar()
    try:
        draft = db.execute("SELECT * FROM occurrence_draft WHERE draft_id=?", (draft_id,)).fetchone()
        if draft is None:
            return "El borrador no existe.", 404
        context = _context(db, draft=draft)
    finally:
        db.close()
    return render_template("nueva_ocurrencia.html", **context)


@submissions_bp.route("/borradores/<int:draft_id>/eliminar", methods=["POST"])
def eliminar_borrador(draft_id):
    db = conectar()
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute("DELETE FROM occurrence_draft WHERE draft_id=?", (draft_id,))
        if cursor.rowcount and getattr(g, "current_access_role", None):
            from activity import record_activity
            record_activity(db,"occurrence_draft_deleted",entity_type="occurrence_draft",entity_id=draft_id,collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("submissions.borradores")) if cursor.rowcount else ("El borrador no existe.", 404)


@submissions_bp.route("/borradores/<int:draft_id>/completar", methods=["POST"])
def completar_borrador(draft_id):
    db = conectar()
    try:
        occurrence_id = complete_registration(db, draft_id=draft_id, **_evidence(request.form), **_reference(request.form), collaborator_id=request.form.get("collaborator_id"), access_role=getattr(g, "current_access_role", None))
    except (RegistrationError, sqlite3.IntegrityError, ValueError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("occurrences.mostrar_gramatica", occurrence_id=occurrence_id, flow="registration"))


def _rows(db, pending=False):
    where = "WHERE s.status='pending'" if pending else ""
    return db.execute(f"""SELECT s.*, o.original_gloss, o.hyperlink, src.source_name,
        gs.gender, gs.gender_uncertain, gs.plural, gs.plural_uncertain,
        gs.agentive, gs.agentive_uncertain, gs.conjugated_form,
        gs.conjugated_form_uncertain, gs.negation, gs.negation_uncertain, gs.note,
        local.concept_id AS local_concept_id,local_concept.preferred_label AS local_concept_label,
        als.proposal_kind, als.analysis_note,als.reference_concept_id,
        als.reference_concept_proposal_id,als.proposed_existing_alternative_id,
        als.phonological_relation_answer,als.resolved_alternative_id,als.is_legacy,
        COALESCE(context.preferred_label,cp.proposed_label) AS context_label,
        cp.status AS concept_proposal_status,cp.resolved_concept_id,
        COALESCE(context.preferred_label,cp_resolved.preferred_label,
                 occurrence_context.preferred_label,occurrence_cp_resolved.preferred_label,
                 cp.proposed_label,occurrence_cp.proposed_label) AS display_concept,
        resolved_concept.preferred_label AS resolved_concept_label,
        resolved_alt.working_label AS resolved_working_label,
        proposed_concept.preferred_label AS proposed_concept_label,
        proposed_alt.working_label AS proposed_working_label,
        current_concept.preferred_label AS current_concept_label,
        current_alt.working_label AS current_working_label,
        reviewed_grammar.occurrence_grammar_id AS reviewed_grammar_id,
        reviewed_grammar.gender AS reviewed_gender,
        reviewed_grammar.gender_uncertain AS reviewed_gender_uncertain,
        reviewed_grammar.plural AS reviewed_plural,
        reviewed_grammar.plural_uncertain AS reviewed_plural_uncertain,
        reviewed_grammar.agentive AS reviewed_agentive,
        reviewed_grammar.agentive_uncertain AS reviewed_agentive_uncertain,
        reviewed_grammar.conjugated_form AS reviewed_conjugated_form,
        reviewed_grammar.conjugated_form_uncertain AS reviewed_conjugated_form_uncertain,
        reviewed_grammar.negation AS reviewed_negation,
        reviewed_grammar.negation_uncertain AS reviewed_negation_uncertain,
        reviewed_grammar.grammar_note AS reviewed_grammar_note,
        reviewed_grammar.change_note AS reviewed_change_note,
        reviewed_grammar.created_by AS reviewed_created_by
        FROM submission s JOIN occurrence o USING(occurrence_id)
        JOIN source src ON src.source_id=o.source_id
        LEFT JOIN grammar_submission gs USING(submission_id)
        LEFT JOIN alternative_submission als USING(submission_id)
        LEFT JOIN submission_concept_resolution local ON local.submission_id=s.submission_id AND local.is_current=1
        LEFT JOIN concept local_concept ON local_concept.concept_id=local.concept_id
        LEFT JOIN concept context ON context.concept_id=als.reference_concept_id
        LEFT JOIN concept_proposal cp ON cp.concept_proposal_id=als.reference_concept_proposal_id
        LEFT JOIN concept cp_resolved ON cp_resolved.concept_id=cp.resolved_concept_id
        LEFT JOIN alternative resolved_alt ON resolved_alt.alternative_id=als.resolved_alternative_id
        LEFT JOIN concept resolved_concept ON resolved_concept.concept_id=resolved_alt.concept_id
        LEFT JOIN alternative proposed_alt ON proposed_alt.alternative_id=als.proposed_existing_alternative_id
        LEFT JOIN concept proposed_concept ON proposed_concept.concept_id=proposed_alt.concept_id
        LEFT JOIN occurrence_concept_reference occurrence_ref ON occurrence_ref.occurrence_id=o.occurrence_id AND occurrence_ref.is_current=1
        LEFT JOIN concept occurrence_context ON occurrence_context.concept_id=occurrence_ref.concept_id
        LEFT JOIN concept_proposal occurrence_cp ON occurrence_cp.concept_proposal_id=occurrence_ref.concept_proposal_id
        LEFT JOIN concept occurrence_cp_resolved ON occurrence_cp_resolved.concept_id=occurrence_cp.resolved_concept_id
        LEFT JOIN assignment current_assignment ON current_assignment.occurrence_id=o.occurrence_id AND current_assignment.is_current=1
        LEFT JOIN alternative current_alt ON current_alt.alternative_id=current_assignment.alternative_id
        LEFT JOIN concept current_concept ON current_concept.concept_id=current_alt.concept_id
        LEFT JOIN occurrence_grammar reviewed_grammar
          ON reviewed_grammar.created_from_submission_id=s.submission_id
        {where} ORDER BY s.submission_id DESC""").fetchall()


@submissions_bp.route("/aportes", methods=["GET"])
def aportes():
    db = conectar()
    try:
        rows = _rows(db)
    finally:
        db.close()
    return render_template("aportes.html", aportes=rows)


@submissions_bp.route("/aportes/pendientes")
def revisar_aportes():
    db = conectar()
    try:
        rows = _rows(db, True)
        current = {row["occurrence_id"]: db.execute("SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1", (row["occurrence_id"],)).fetchone() for row in rows}
        alternative_context = _alternative_review_context(db, rows)
    finally:
        db.close()
    return render_template("revision_aportes.html", aportes=rows, current_by_occurrence=current, alternative_context=alternative_context, grammar_vocabularies=GRAMMATICAL_MARK_VOCABULARIES)


def _alternative_review_context(db, rows):
    result={}
    concepts=db.execute("SELECT concept_id,preferred_label FROM concept ORDER BY preferred_label").fetchall()
    for row in rows:
        if row["submission_type"] != "ALTERNATIVE": continue
        concept_id=row["local_concept_id"]
        alternatives=[dict(item) for item in db.execute("SELECT a.alternative_id,a.working_label,c.preferred_label FROM alternative a JOIN concept c USING(concept_id) WHERE a.concept_id=? AND a.retired_at IS NULL ORDER BY a.working_label",(concept_id,)).fetchall()] if concept_id else []
        for alternative in alternatives:
            alternative["current_video"]=get_current_video(db, alternative["alternative_id"])
            alternative["display_label"]=alternative_display_label(
                alternative["preferred_label"],alternative["working_label"]
            )
            alternative["occurrences"]=[dict(item) for item in db.execute("""
                SELECT o.occurrence_id,o.original_gloss,s.source_name,o.source_detail_1,o.source_detail_2
                FROM assignment ass JOIN occurrence o USING(occurrence_id)
                JOIN source s USING(source_id)
                WHERE ass.alternative_id=? AND ass.is_current=1 ORDER BY o.occurrence_id
            """,(alternative["alternative_id"],))]
        relations=db.execute("""
            SELECT r.*,a.working_label AS target_working_label,c.preferred_label AS target_concept_label,
                   ts.status AS target_submission_status, ts.resolution AS target_submission_resolution,
                   o.occurrence_id AS target_occurrence_id,o.original_gloss AS target_gloss,
                   ra.working_label AS resolved_working_label,rc.preferred_label AS resolved_concept_label,
                   ta.resolved_alternative_id AS target_resolved_alternative_id
            FROM alternative_submission_relation r
            LEFT JOIN alternative a ON a.alternative_id=r.target_alternative_id
            LEFT JOIN concept c ON c.concept_id=a.concept_id
            LEFT JOIN submission ts ON ts.submission_id=r.target_submission_id
            LEFT JOIN alternative_submission ta ON ta.submission_id=r.target_submission_id
            LEFT JOIN occurrence o ON o.occurrence_id=ts.occurrence_id
            LEFT JOIN alternative ra ON ra.alternative_id=ta.resolved_alternative_id
            LEFT JOIN concept rc ON rc.concept_id=ra.concept_id
            WHERE r.submission_id=? ORDER BY r.alternative_submission_relation_id
        """,(row["submission_id"],)).fetchall()
        assignment=db.execute("""SELECT a.alternative_id,al.working_label,c.preferred_label FROM assignment a JOIN alternative al USING(alternative_id) JOIN concept c USING(concept_id) WHERE a.occurrence_id=? AND a.is_current=1""",(row["occurrence_id"],)).fetchone()
        pending=db.execute("""SELECT s.submission_id,o.original_gloss FROM submission s JOIN alternative_submission a USING(submission_id) JOIN occurrence o USING(occurrence_id) WHERE s.status='pending' AND s.submission_type='ALTERNATIVE' AND a.proposal_kind='NEW' AND s.submission_id!=? AND (a.reference_concept_id=? OR a.reference_concept_proposal_id=?)""",(row["submission_id"],row["reference_concept_id"],row["reference_concept_proposal_id"])).fetchall()
        relations_error=None
        try:
            resolved_targets=_relation_targets(db,row['submission_id'])
        except AlternativeWorkflowError as error:
            resolved_targets=[]
            relations_error=str(error)
        previews={}
        if concept_id and row['status']=='pending':
            previews['REJECTED' if relations else 'NOT_PROPOSED']=calculate_nomenclature_preview(
                db,concept_id,virtual_occurrences={'new':row['occurrence_id']})
            edges=[]
            if not relations_error:
                for target, parameter in resolved_targets:
                    valid=db.execute('SELECT 1 FROM alternative WHERE alternative_id=? AND concept_id=? AND retired_at IS NULL',(target,concept_id)).fetchone()
                    if not valid:
                        relations_error='La relación propuesta ya no tiene un destino vigente del mismo concepto.'
                        break
                    edges.append(('new',target))
                else:
                    if relations:
                        previews['ACCEPTED']=calculate_nomenclature_preview(
                            db,concept_id,extra_edges=edges,virtual_occurrences={'new':row['occurrence_id']})
        lexical_decision=get_decision(db,row['submission_id'])
        if lexical_decision:
            lexical_decision = dict(lexical_decision)
            label = lexical_decision['alternative_label_snapshot']
            if (lexical_decision['decision_action'] == 'CREATE_NEW' and label
                    and re.fullmatch(r'[0-9]+[a-z]+', label)
                    and lexical_decision['concept_label_snapshot']):
                label = alternative_display_label(lexical_decision['concept_label_snapshot'], label)
            lexical_decision['historical_display_label'] = label
        result_current=None
        morphology_result=None
        if lexical_decision:
            result_current=db.execute('SELECT a.*,c.preferred_label FROM alternative a JOIN concept c USING(concept_id) WHERE alternative_id=?',(lexical_decision['resolved_alternative_id'],)).fetchone()
            morphology_result=db.execute('SELECT * FROM alternative_morphology WHERE alternative_morphology_id=?',(lexical_decision['morphology_result_id'],)).fetchone()
        morphology=submission_morphology(db,row["submission_id"])
        if morphology:
            components = []
            for component in morphology[1]:
                item = dict(component)
                label = db.execute("SELECT c.preferred_label,a.working_label FROM alternative a JOIN concept c USING(concept_id) WHERE a.alternative_id=?", (item["component_alternative_id"],)).fetchone()
                item["display_label"] = alternative_display_label(label["preferred_label"], label["working_label"]) if label else None
                components.append(item)
            morphology = morphology[0], components
        result[row["submission_id"]]=dict(relations_error=relations_error,proposed_alternative_matches=any(a["alternative_id"] == row["proposed_existing_alternative_id"] for a in alternatives),concept_resolution=current_resolution(db,row["submission_id"]),concept_history=resolution_history(db,row["submission_id"]),concept_edit_token=edit_token(db,"submission_concept",row["submission_id"]),alternatives=alternatives,relations=relations,assignment=assignment,pending=pending,concepts=concepts,nomenclature_previews=previews,lexical_decision=lexical_decision,result_current=result_current,morphology_result=morphology_result,proposed_morphology=morphology)
    return result


@submissions_bp.route("/aportes/<int:submission_id>")
def detalle_aporte(submission_id):
    db=conectar()
    try:
        rows=[row for row in _rows(db) if row["submission_id"]==submission_id]
        if not rows: return "El aporte no existe.",404
        current={rows[0]["occurrence_id"]:db.execute("SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1",(rows[0]["occurrence_id"],)).fetchone()}
        context=_alternative_review_context(db,rows)
    finally: db.close()
    return render_template("revision_aportes.html",aportes=rows,current_by_occurrence=current,alternative_context=context,grammar_vocabularies=GRAMMATICAL_MARK_VOCABULARIES,detail=True)


@submissions_bp.post("/aportes/<int:submission_id>/concepto")
@requires_reviewer
def resolver_concepto_aporte(submission_id):
    db = conectar()
    try:
        save_resolution(db, submission_id, request.form.get("concept_action"),
            concept_id=request.form.get("concept_id") or None,
            label=request.form.get("concept_label"), note=request.form.get("concept_note"),
            collaborator_id=request.form.get("collaborator_id"), access_role=g.current_access_role,
            expected_edit_token=request.form.get("concept_edit_token", ""))
    except StaleEdit as error:
        return str(error), 409
    except (ValueError, sqlite3.IntegrityError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("submissions.detalle_aporte", submission_id=submission_id))


@submissions_bp.route("/aportes/<int:submission_id>/decidir", methods=["POST"])
def decidir_aporte(submission_id):
    decision = {"accept": "accepted", "accept_proposed": "accepted", "reject": "rejected"}.get(request.form.get("decision"), request.form.get("decision"))
    db = conectar(); created_message = None
    try:
        row = db.execute("SELECT submission_type FROM submission WHERE submission_id=?", (submission_id,)).fetchone()
        if row is None:
            return "El aporte no existe.", 404
        if row[0] == "GRAMMAR":
            reviewed_values = _grammar_review_values(request.form)
            operation=lambda connection: resolve_grammar_submission(
                connection, submission_id, decision,
                reviewed_values=reviewed_values if decision == "accepted" else None,
                reviewed_by=request.form.get("reviewed_by"),
                review_note=request.form.get("review_note"),
                collaborator_id=request.form.get("collaborator_id"),
                access_role=getattr(g, "current_access_role", None),
            )
            if decision=="accepted":run_normal_review(db,operation,request.form.get("review_note"))
            else:operation(db)
        elif decision == "pending":
            return redirect(url_for('submissions.detalle_aporte',submission_id=submission_id))
        elif decision == "rejected":
            reject_alternative_submission(db,submission_id,reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None))
        else:
            concept_resolution=None
            action=request.form.get("concept_resolution_action")
            if action: concept_resolution={"action":action,"concept_id":request.form.get("resolved_concept_id") or None,"label":request.form.get("new_concept_label") or None}
            if decision in ("existing", "existing_proposed"):
                target_id=request.form.get("alternative_id")
                if decision == "existing_proposed":
                    target_id=db.execute("SELECT proposed_existing_alternative_id FROM alternative_submission WHERE submission_id=?",(submission_id,)).fetchone()[0]
                run_normal_review(db,lambda connection: review_as_existing(connection,submission_id,target_id,concept_resolution=concept_resolution,relation_policy=request.form.get("relation_policy","preserve"),relations_resolution=request.form.get("relations_resolution"),morphology_resolution=request.form.get("morphology_resolution"),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None)),request.form.get("review_note"))
            elif decision == "new":
                labels={key[6:]:value for key,value in request.form.items() if key.startswith("label_")}
                before_labels=dict(db.execute("SELECT alternative_id,working_label FROM alternative"))
                new_id=run_normal_review(db,lambda connection: review_as_new(connection,submission_id,concept_resolution=concept_resolution,approve_relations=(request.form.get("approve_relations")=="yes" if "approve_relations" in request.form else None),relations_resolution=request.form.get("relations_resolution"),morphology_resolution=request.form.get("morphology_resolution"),nomenclature_mode=request.form.get("nomenclature_mode","automatic"),labels=labels,reason=request.form.get("nomenclature_reason") or request.form.get("review_note"),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),approve_morphology=(request.form.get("approve_morphology")=="yes" if "approve_morphology" in request.form else None),collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None)),request.form.get("review_note"))
                created=db.execute("SELECT c.preferred_label,a.working_label FROM alternative a JOIN concept c USING(concept_id) WHERE a.alternative_id=?",(new_id,)).fetchone()
                changes=sum(before_labels[item[0]] != item[1] for item in db.execute("SELECT alternative_id,working_label FROM alternative") if item[0] in before_labels)
                renumber_message = (f"Se actualizaron {changes} etiquetas de alternativas existentes." if changes != 1 else "Se actualizó 1 etiqueta de una alternativa existente.") if changes else "No fue necesario renumerar alternativas existentes."
                created_message=f"Nueva alternativa creada como {alternative_display_label(created['preferred_label'],created['working_label'])}. {renumber_message}"
            else: raise AlternativeWorkflowError("Decisión de review no válida.")
    except (AlternativeWorkflowError,GrammarWorkflowError,ImmediateAcceptanceError, sqlite3.IntegrityError, ValueError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("submissions.revisar_aportes",message=created_message) if created_message else url_for("submissions.revisar_aportes"))
