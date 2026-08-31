import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for

from database import conectar
from grammar_workflow import GrammarWorkflowError, resolve_grammar_submission
from occurrence_registration import RegistrationError, complete_registration, save_draft

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
        occurrence_id = complete_registration(db, **_evidence(request.form), **_reference(request.form))
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
        save_draft(db, draft_id=draft_id, **values)
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
        cursor = db.execute("DELETE FROM occurrence_draft WHERE draft_id=?", (draft_id,))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("submissions.borradores")) if cursor.rowcount else ("El borrador no existe.", 404)


@submissions_bp.route("/borradores/<int:draft_id>/completar", methods=["POST"])
def completar_borrador(draft_id):
    db = conectar()
    try:
        occurrence_id = complete_registration(db, draft_id=draft_id, **_evidence(request.form), **_reference(request.form))
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
        als.proposal_kind, als.analysis_note
        FROM submission s JOIN occurrence o USING(occurrence_id)
        JOIN source src ON src.source_id=o.source_id
        LEFT JOIN grammar_submission gs USING(submission_id)
        LEFT JOIN alternative_submission als USING(submission_id)
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
    finally:
        db.close()
    return render_template("revision_aportes.html", aportes=rows, current_by_occurrence=current)


@submissions_bp.route("/aportes/<int:submission_id>/decidir", methods=["POST"])
def decidir_aporte(submission_id):
    decision = {"accept": "accepted", "accept_proposed": "accepted", "reject": "rejected"}.get(request.form.get("decision"), request.form.get("decision"))
    db = conectar()
    try:
        row = db.execute("SELECT submission_type FROM submission WHERE submission_id=?", (submission_id,)).fetchone()
        if row is None:
            return "El aporte no existe.", 404
        if row[0] != "GRAMMAR":
            return "Flujo de análisis de alternativa en actualización.", 409
        resolve_grammar_submission(db, submission_id, decision, reviewed_by=request.form.get("reviewed_by"), review_note=request.form.get("review_note"))
    except (GrammarWorkflowError, sqlite3.IntegrityError, ValueError) as error:
        return str(error), 400
    finally:
        db.close()
    return redirect(url_for("submissions.revisar_aportes"))
