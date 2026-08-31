from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar
from concept_labels import alternative_display_label, human_concept_label
from assignments import create_or_replace_assignment
from grammar_workflow import GrammarWorkflowError, create_grammar_submission
from grammatical_marks import GRAMMATICAL_MARK_VOCABULARIES
from alternative_workflow import AlternativeWorkflowError, create_alternative_submission
from phonological_parameters import PHONOLOGICAL_PARAMETERS


occurrences_bp = Blueprint("occurrences", __name__)


def validate_occurrence_year(conexion, source_id, value):
    if value in (None, ""):
        return None
    if not value.isdigit() or len(value) != 4:
        raise ValueError
    year = int(value)
    if year < 1:
        raise ValueError
    return year


@occurrences_bp.route("/ocurrencias")
def ocurrencias():
    conexion = conectar()
    rows = conexion.execute("""
        SELECT o.occurrence_id, s.source_name, o.original_gloss, o.hyperlink,
               c.preferred_label, al.working_label,
               EXISTS (
                   SELECT 1 FROM occurrence_grammar AS og
                   WHERE og.occurrence_id = o.occurrence_id
                     AND og.is_current = 1
               ) AS has_current_grammar,
               EXISTS (
                   SELECT 1 FROM submission gs
                   WHERE gs.occurrence_id = o.occurrence_id
                     AND gs.submission_type = 'GRAMMAR' AND gs.status = 'pending'
               ) AS has_pending_grammar,
               EXISTS (
                   SELECT 1 FROM submission als
                   WHERE als.occurrence_id = o.occurrence_id
                     AND als.submission_type = 'ALTERNATIVE' AND als.status = 'pending'
               ) AS has_pending_alternative
        FROM occurrence AS o
        JOIN source AS s ON o.source_id = s.source_id
        LEFT JOIN assignment AS a
            ON a.occurrence_id = o.occurrence_id AND a.is_current = 1
        LEFT JOIN alternative AS al ON al.alternative_id = a.alternative_id
        LEFT JOIN concept AS c ON c.concept_id = al.concept_id
        ORDER BY o.occurrence_id
    """).fetchall()
    ocurrencias = []
    for row in rows:
        occurrence = dict(row)
        if row["preferred_label"] is None and row["working_label"] is None:
            current_classification = "Sin clasificación"
        elif row["working_label"] is None:
            current_classification = human_concept_label(row["preferred_label"])
        else:
            current_classification = alternative_display_label(
                row["preferred_label"], row["working_label"]
            )
        occurrence["current_classification"] = current_classification
        occurrence["grammar_status"] = (
            "Pendiente de revisión" if row["has_pending_grammar"]
            else "Analizadas" if row["has_current_grammar"] else "Sin analizar"
        )
        occurrence["alternative_status"] = (
            "Clasificada" if row["working_label"] is not None or row["preferred_label"] is not None
            else "Pendiente" if row["has_pending_alternative"] else "Sin analizar"
        )
        ocurrencias.append(occurrence)
    conexion.close()
    return render_template("ocurrencias.html", ocurrencias=ocurrencias)


@occurrences_bp.route("/ocurrencias/nueva")
def nueva_ocurrencia():
    return redirect(url_for("submissions.nuevo_aporte"))


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/editar")
def editar_ocurrencia(occurrence_id):
    conexion = conectar()
    ocurrencia = conexion.execute("""
        SELECT occurrence_id, source_id, original_gloss, hyperlink,
               source_locator, provenance_note, occurrence_year
        FROM occurrence WHERE occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if ocurrencia is None:
        conexion.close()
        return "La ocurrencia no existe.", 404
    fuentes = conexion.execute("""
        SELECT source_id, source_name, start_year, end_year, end_year_status
        FROM source ORDER BY source_name
    """).fetchall()
    conexion.close()
    return render_template(
        "editar_ocurrencia.html", ocurrencia=ocurrencia, fuentes=fuentes
    )


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/actualizar", methods=["POST"])
def actualizar_ocurrencia(occurrence_id):
    source_id = request.form.get("source_id", "")
    original_gloss = request.form.get("original_gloss", "").strip()
    hyperlink = request.form.get("hyperlink", "").strip()
    source_locator = request.form.get("source_locator", "").strip()
    provenance_note = request.form.get("provenance_note", "").strip()
    occurrence_year_value = request.form.get("occurrence_year", "").strip()
    change_note = request.form.get("change_note", "").strip() or None
    if not source_id:
        return "La fuente es obligatoria.", 400

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        actual = conexion.execute("""
            SELECT legacy_occurrence_id, source_id, original_gloss, hyperlink,
                   legacy_source_detail_1, legacy_source_detail_2,
                   source_locator, provenance_note, occurrence_year
            FROM occurrence WHERE occurrence_id = ?
        """, (occurrence_id,)).fetchone()
        if actual is None:
            conexion.rollback()
            return "La ocurrencia no existe.", 404
        occurrence_year = validate_occurrence_year(
            conexion, source_id, occurrence_year_value
        )
        new_state = (
            int(source_id), original_gloss, hyperlink, source_locator,
            provenance_note, occurrence_year
        )
        previous_editable_state = (
            actual["source_id"], actual["original_gloss"], actual["hyperlink"],
            actual["source_locator"], actual["provenance_note"],
            actual["occurrence_year"]
        )
        if new_state != previous_editable_state:
            conexion.execute("""
                INSERT INTO occurrence_revision (
                    occurrence_id, legacy_occurrence_id, source_id,
                    original_gloss, hyperlink, legacy_source_detail_1,
                    legacy_source_detail_2, source_locator, provenance_note,
                    occurrence_year, change_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                occurrence_id, actual["legacy_occurrence_id"],
                actual["source_id"], actual["original_gloss"],
                actual["hyperlink"], actual["legacy_source_detail_1"],
                actual["legacy_source_detail_2"], actual["source_locator"],
                actual["provenance_note"], actual["occurrence_year"],
                change_note
            ))
        cursor = conexion.execute("""
            UPDATE occurrence SET
                source_id = ?, original_gloss = ?, hyperlink = ?,
                source_locator = ?, provenance_note = ?, occurrence_year = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE occurrence_id = ?
        """, (*new_state, occurrence_id))
        if cursor.rowcount != 1:
            conexion.rollback()
            return "La ocurrencia no existe.", 404
        conexion.commit()
    except (sqlite3.IntegrityError, ValueError):
        conexion.rollback()
        return "La fuente o el año de la occurrence no son válidos.", 400
    finally:
        conexion.close()
    return redirect(url_for("occurrences.ocurrencias"))


def _load_grammar_page_data(conexion, occurrence_id):
    occurrence = conexion.execute("""
        SELECT o.occurrence_id, o.original_gloss, o.hyperlink, s.source_name,
               COALESCE(c.preferred_label, cp.proposed_label) AS reference_label,
               ac.preferred_label AS assignment_concept,
               al.working_label AS assignment_label
        FROM occurrence AS o
        JOIN source AS s ON s.source_id = o.source_id
        LEFT JOIN occurrence_concept_reference r
          ON r.occurrence_id=o.occurrence_id AND r.is_current=1
        LEFT JOIN concept c ON c.concept_id=r.concept_id
        LEFT JOIN concept_proposal cp ON cp.concept_proposal_id=r.concept_proposal_id
        LEFT JOIN assignment a ON a.occurrence_id=o.occurrence_id AND a.is_current=1
        LEFT JOIN alternative al ON al.alternative_id=a.alternative_id
        LEFT JOIN concept ac ON ac.concept_id=al.concept_id
        WHERE o.occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if occurrence is None:
        return None, None, [], None
    current = conexion.execute("""
        SELECT occurrence_grammar_id, gender, plural, agentive,
               conjugated_form, negation, grammar_note, is_current,
               supersedes_occurrence_grammar_id, created_at, created_by,
               change_note, gender_uncertain, plural_uncertain,
               agentive_uncertain, conjugated_form_uncertain,
               negation_uncertain, created_from_submission_id
        FROM occurrence_grammar
        WHERE occurrence_id = ? AND is_current = 1
    """, (occurrence_id,)).fetchone()
    history = conexion.execute("""
        SELECT occurrence_grammar_id, gender, plural, agentive,
               conjugated_form, negation, grammar_note, is_current,
               supersedes_occurrence_grammar_id, created_at, created_by,
               change_note, gender_uncertain, plural_uncertain,
               agentive_uncertain, conjugated_form_uncertain,
               negation_uncertain, created_from_submission_id
        FROM occurrence_grammar
        WHERE occurrence_id = ?
        ORDER BY is_current DESC, occurrence_grammar_id DESC
    """, (occurrence_id,)).fetchall()
    pending = conexion.execute("""
        SELECT s.submission_id, s.submitted_at, gs.*
        FROM submission s JOIN grammar_submission gs USING(submission_id)
        WHERE s.occurrence_id=? AND s.submission_type='GRAMMAR' AND s.status='pending'
    """, (occurrence_id,)).fetchone()
    return occurrence, current, history, pending


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/gramatica")
def mostrar_gramatica(occurrence_id):
    conexion = conectar()
    try:
        occurrence, current, history, pending = _load_grammar_page_data(
            conexion, occurrence_id
        )
    finally:
        conexion.close()
    if occurrence is None:
        return "La ocurrencia no existe.", 404
    result_messages = {
        "submitted": "Propuesta gramatical enviada a revisión.",
    }
    form_values = dict(current) if current is not None else {}
    form_values["note"] = current["grammar_note"] if current is not None else ""
    return render_template(
        "gramatica_ocurrencia.html",
        occurrence=occurrence,
        current=current,
        history=history,
        pending=pending,
        vocabularies=GRAMMATICAL_MARK_VOCABULARIES,
        form_values=form_values,
        message=result_messages.get(request.args.get("result")),
        error=None,
    )


@occurrences_bp.route(
    "/ocurrencias/<int:occurrence_id>/gramatica", methods=["POST"]
)
def guardar_gramatica(occurrence_id):
    form_values = {
        "gender": request.form.get("gender"),
        "plural": request.form.get("plural"),
        "agentive": request.form.get("agentive"),
        "conjugated_form": request.form.get("conjugated_form"),
        "negation": request.form.get("negation"),
        "note": request.form.get("note"),
    }
    for field in ("gender", "plural", "agentive", "conjugated_form", "negation"):
        form_values[field + "_uncertain"] = request.form.get(field + "_uncertain")
    conexion = conectar()
    try:
        create_grammar_submission(conexion, occurrence_id, form_values)
    except GrammarWorkflowError as error:
        occurrence, current, history, pending = _load_grammar_page_data(
            conexion, occurrence_id
        )
        if occurrence is None:
            return "La ocurrencia no existe.", 404
        return render_template(
            "gramatica_ocurrencia.html",
            occurrence=occurrence,
            current=current,
            history=history,
            pending=pending,
            vocabularies=GRAMMATICAL_MARK_VOCABULARIES,
            form_values=form_values,
            message=None,
            error=str(error),
        ), 400
    except sqlite3.IntegrityError:
        occurrence, current, history, pending = _load_grammar_page_data(
            conexion, occurrence_id
        )
        return render_template(
            "gramatica_ocurrencia.html", occurrence=occurrence, current=current,
            history=history, pending=pending,
            vocabularies=GRAMMATICAL_MARK_VOCABULARIES,
            form_values=form_values, message=None,
            error="Ya existe una propuesta gramatical pendiente.",
        ), 400
    except Exception:
        return "No fue posible guardar el análisis gramatical.", 500
    finally:
        conexion.close()
    return redirect(url_for(
        "occurrences.mostrar_gramatica",
        occurrence_id=occurrence_id,
        result="submitted",
    ))


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/clasificar")
def clasificar_ocurrencia(occurrence_id):
    conexion = conectar()
    occurrence = conexion.execute("""
        SELECT o.occurrence_id, o.original_gloss, o.hyperlink,
               o.occurrence_year,o.source_locator,o.provenance_note,
               s.source_name, a.assignment_id, a.alternative_id,
               al.working_label, c.preferred_label,
               r.concept_id AS reference_concept_id,
               r.concept_proposal_id AS reference_concept_proposal_id,
               COALESCE(rc.preferred_label,cp.proposed_label) AS reference_label,
               cp.status AS concept_proposal_status,
               cp.resolved_concept_id
        FROM occurrence AS o JOIN source AS s ON s.source_id = o.source_id
        LEFT JOIN assignment AS a
            ON a.occurrence_id = o.occurrence_id AND a.is_current = 1
        LEFT JOIN alternative AS al ON al.alternative_id = a.alternative_id
        LEFT JOIN concept AS c ON c.concept_id = al.concept_id
        LEFT JOIN occurrence_concept_reference r
          ON r.occurrence_id=o.occurrence_id AND r.is_current=1
        LEFT JOIN concept rc ON rc.concept_id=r.concept_id
        LEFT JOIN concept_proposal cp
          ON cp.concept_proposal_id=r.concept_proposal_id
        WHERE o.occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if occurrence is None:
        conexion.close()
        return "La ocurrencia no existe.", 404
    context_concept_id = occurrence["reference_concept_id"] or occurrence["resolved_concept_id"]
    alternatives = []
    pending_context = []
    if context_concept_id is not None:
        alternatives = [dict(row) for row in conexion.execute("""
            SELECT alternative_id,working_label FROM alternative
            WHERE concept_id=? AND retired_at IS NULL ORDER BY working_label
        """, (context_concept_id,)).fetchall()]
        for alternative in alternatives:
            alternative["occurrences"] = conexion.execute("""
                SELECT o.original_gloss,s.source_name,o.occurrence_year
                FROM assignment a JOIN occurrence o USING(occurrence_id)
                JOIN source s USING(source_id)
                WHERE a.alternative_id=? AND a.is_current=1
            """, (alternative["alternative_id"],)).fetchall()
        pending_context = conexion.execute("""
            SELECT s.submission_id,o.original_gloss,src.source_name
            FROM submission s JOIN alternative_submission als USING(submission_id)
            JOIN occurrence o USING(occurrence_id) JOIN source src USING(source_id)
            WHERE s.status='pending' AND s.submission_type='ALTERNATIVE'
              AND als.proposal_kind='NEW' AND als.reference_concept_id=?
              AND s.occurrence_id != ?
        """, (context_concept_id, occurrence_id)).fetchall()
    existing_pending = conexion.execute("""
        SELECT s.submission_id,als.proposal_kind,als.analysis_note
        FROM submission s JOIN alternative_submission als USING(submission_id)
        WHERE s.occurrence_id=? AND s.submission_type='ALTERNATIVE' AND s.status='pending'
    """, (occurrence_id,)).fetchone()
    history = conexion.execute("""
        SELECT a.assignment_id, a.alternative_id, a.is_current,
               a.created_at, a.supersedes_assignment_id,
               c.preferred_label, al.working_label
        FROM assignment AS a
        JOIN alternative AS al ON al.alternative_id = a.alternative_id
        JOIN concept AS c ON c.concept_id = al.concept_id
        WHERE a.occurrence_id = ? ORDER BY a.assignment_id DESC
    """, (occurrence_id,)).fetchall()
    conexion.close()
    return render_template(
        "clasificar_ocurrencia.html", occurrence=occurrence,
        alternatives=alternatives, pending_context=pending_context,
        existing_pending=existing_pending,
        phonological_parameters=PHONOLOGICAL_PARAMETERS, history=history
    )


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/clasificar", methods=["POST"])
def guardar_clasificacion(occurrence_id):
    proposal_kind=request.form.get("proposal_kind")
    alternative_id=request.form.get("proposed_existing_alternative_id") or None
    target_types=request.form.getlist("relation_target_type")
    target_ids=request.form.getlist("relation_target_id")
    parameters=request.form.getlist("relation_parameter")
    uncertain=set(request.form.getlist("relation_uncertain"))
    relations=[]
    for index,(kind,target,parameter) in enumerate(zip(target_types,target_ids,parameters)):
        if not target and not parameter: continue
        relation={"phonological_parameter":parameter,"uncertain":str(index) in uncertain}
        relation["target_submission_id" if kind=="submission" else "target_alternative_id"]=target or None
        relations.append(relation)
    conexion = conectar()
    try:
        create_alternative_submission(
            conexion,occurrence_id,proposal_kind,
            proposed_existing_alternative_id=alternative_id,
            phonological_relation_answer=request.form.get("phonological_relation_answer"),
            relations=relations,analysis_note=request.form.get("analysis_note"),
        )
    except (AlternativeWorkflowError,ValueError,sqlite3.IntegrityError) as error:
        return str(error),400
    finally:
        conexion.close()
    return redirect(url_for("occurrences.clasificar_ocurrencia", occurrence_id=occurrence_id))
