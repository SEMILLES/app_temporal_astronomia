import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for, g

from database import conectar
from grammar_workflow import GrammarWorkflowError, resolve_grammar_submission
from occurrence_registration import RegistrationError, complete_registration, save_draft
from alternative_workflow import (
    AlternativeWorkflowError, reject_alternative_submission,
    review_as_existing, review_as_new,
)
from alternative_nomenclature import calculate_nomenclature_preview
from alternative_morphology import submission_morphology

submissions_bp = Blueprint("submissions", __name__)


def _context(db, draft=None, error=None):
    return dict(
        fuentes=db.execute("SELECT source_id, source_name, start_year, end_year, end_year_status FROM source ORDER BY source_name").fetchall(),
        conceptos=db.execute("SELECT concept_id, preferred_label FROM concept ORDER BY preferred_label").fetchall(),
        propuestas=db.execute("SELECT concept_proposal_id, proposed_label FROM concept_proposal WHERE status='pending' ORDER BY proposed_label").fetchall(),
        draft=draft, error=error,
    )


def _evidence(form):
    return {name: form.get(name) for name in (
        "source_id", "original_gloss", "occurrence_year", "source_locator",
        "provenance_note", "hyperlink",
    )}


def _reference(form):
    kind = form.get("reference_kind")
    return dict(
        concept_id=form.get("reference_concept_id") if kind == "concept" else None,
        concept_proposal_id=form.get("reference_concept_proposal_id") if kind == "proposal" else None,
        proposed_label=form.get("proposed_label") if kind == "new" else None,
    )


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
    db = conectar()
    try:
        occurrence_id = complete_registration(db, **_evidence(request.form), **_reference(request.form), collaborator_id=request.form.get("collaborator_id"), access_role=getattr(g, "current_access_role", None))
    except (RegistrationError, sqlite3.IntegrityError, ValueError) as error:
        context = _context(db, error=str(error))
        return render_template("nueva_ocurrencia.html", **context), 400
    finally:
        db.close()
    return redirect(url_for("occurrences.editar_ocurrencia", occurrence_id=occurrence_id))


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
    return redirect(url_for("occurrences.editar_ocurrencia", occurrence_id=occurrence_id))


def _rows(db, pending=False):
    where = "WHERE s.status='pending'" if pending else ""
    return db.execute(f"""SELECT s.*, o.original_gloss, o.hyperlink, src.source_name,
        gs.gender, gs.gender_uncertain, gs.plural, gs.plural_uncertain,
        gs.agentive, gs.agentive_uncertain, gs.conjugated_form,
        gs.conjugated_form_uncertain, gs.negation, gs.negation_uncertain, gs.note,
        als.proposal_kind, als.analysis_note,als.reference_concept_id,
        als.reference_concept_proposal_id,als.proposed_existing_alternative_id,
        als.phonological_relation_answer,als.resolved_alternative_id,als.is_legacy,
        COALESCE(context.preferred_label,cp.proposed_label) AS context_label,
        cp.status AS concept_proposal_status,cp.resolved_concept_id
        FROM submission s JOIN occurrence o USING(occurrence_id)
        JOIN source src ON src.source_id=o.source_id
        LEFT JOIN grammar_submission gs USING(submission_id)
        LEFT JOIN alternative_submission als USING(submission_id)
        LEFT JOIN concept context ON context.concept_id=als.reference_concept_id
        LEFT JOIN concept_proposal cp ON cp.concept_proposal_id=als.reference_concept_proposal_id
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
    return render_template("revision_aportes.html", aportes=rows, current_by_occurrence=current, alternative_context=alternative_context)


def _alternative_review_context(db, rows):
    result={}
    concepts=db.execute("SELECT concept_id,preferred_label FROM concept ORDER BY preferred_label").fetchall()
    for row in rows:
        if row["submission_type"] != "ALTERNATIVE": continue
        concept_id=row["reference_concept_id"] or row["resolved_concept_id"]
        alternatives=db.execute("SELECT alternative_id,working_label FROM alternative WHERE concept_id=? AND retired_at IS NULL ORDER BY working_label",(concept_id,)).fetchall() if concept_id else []
        relations=db.execute("""
            SELECT r.*,a.working_label AS target_working_label,
                   ts.status AS target_submission_status,
                   ta.resolved_alternative_id AS target_resolved_alternative_id
            FROM alternative_submission_relation r
            LEFT JOIN alternative a ON a.alternative_id=r.target_alternative_id
            LEFT JOIN submission ts ON ts.submission_id=r.target_submission_id
            LEFT JOIN alternative_submission ta ON ta.submission_id=r.target_submission_id
            WHERE r.submission_id=? ORDER BY r.alternative_submission_relation_id
        """,(row["submission_id"],)).fetchall()
        assignment=db.execute("""SELECT a.alternative_id,al.working_label,c.preferred_label FROM assignment a JOIN alternative al USING(alternative_id) JOIN concept c USING(concept_id) WHERE a.occurrence_id=? AND a.is_current=1""",(row["occurrence_id"],)).fetchone()
        pending=db.execute("""SELECT s.submission_id,o.original_gloss FROM submission s JOIN alternative_submission a USING(submission_id) JOIN occurrence o USING(occurrence_id) WHERE s.status='pending' AND s.submission_type='ALTERNATIVE' AND a.proposal_kind='NEW' AND s.submission_id!=? AND (a.reference_concept_id=? OR a.reference_concept_proposal_id=?)""",(row["submission_id"],row["reference_concept_id"],row["reference_concept_proposal_id"])).fetchall()
        preview=None
        if concept_id:
            edges=[]
            for relation in relations:
                target=relation["target_alternative_id"] or relation["target_resolved_alternative_id"]
                if target: edges.append(("new",target))
            preview=calculate_nomenclature_preview(
                db,concept_id,extra_edges=edges,
                virtual_occurrences={"new":row["occurrence_id"]},
            )
        morphology=submission_morphology(db,row["submission_id"])
        result[row["submission_id"]]=dict(alternatives=alternatives,relations=relations,assignment=assignment,pending=pending,concepts=concepts,nomenclature_preview=preview,proposed_morphology=morphology)
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
    return render_template("revision_aportes.html",aportes=rows,current_by_occurrence=current,alternative_context=context,detail=True)


@submissions_bp.route("/aportes/<int:submission_id>/decidir", methods=["POST"])
def decidir_aporte(submission_id):
    decision = {"accept": "accepted", "accept_proposed": "accepted", "reject": "rejected"}.get(request.form.get("decision"), request.form.get("decision"))
    db = conectar()
    try:
        row = db.execute("SELECT submission_type FROM submission WHERE submission_id=?", (submission_id,)).fetchone()
        if row is None:
            return "El aporte no existe.", 404
        if row[0] == "GRAMMAR":
            resolve_grammar_submission(db, submission_id, decision, reviewed_by=request.form.get("reviewed_by"), review_note=request.form.get("review_note"), collaborator_id=request.form.get("collaborator_id"), access_role=getattr(g, "current_access_role", None))
        elif decision == "rejected":
            reject_alternative_submission(db,submission_id,reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None))
        else:
            concept_resolution=None
            action=request.form.get("concept_resolution_action")
            if action: concept_resolution={"action":action,"concept_id":request.form.get("resolved_concept_id") or None,"label":request.form.get("new_concept_label") or None}
            if decision == "existing":
                review_as_existing(db,submission_id,request.form.get("alternative_id"),concept_resolution=concept_resolution,relation_policy=request.form.get("relation_policy","preserve"),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None))
            elif decision == "new":
                labels={key[6:]:value for key,value in request.form.items() if key.startswith("label_")}
                review_as_new(db,submission_id,concept_resolution=concept_resolution,approve_relations=request.form.get("approve_relations")=="yes",nomenclature_mode=request.form.get("nomenclature_mode","automatic"),labels=labels,reason=request.form.get("nomenclature_reason"),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"),approve_morphology=request.form.get("approve_morphology")=="yes",collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g, "current_access_role", None))
            else: raise AlternativeWorkflowError("Decisión de review no válida.")
    except (AlternativeWorkflowError,GrammarWorkflowError, sqlite3.IntegrityError, ValueError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("submissions.revisar_aportes"))
