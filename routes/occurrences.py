from flask import Blueprint, render_template, request, redirect, url_for, g

import sqlite3

from database import conectar
from concept_labels import alternative_display_label, human_concept_label
from assignments import create_or_replace_assignment
from grammar_workflow import GrammarWorkflowError, create_grammar_submission
from grammatical_marks import GRAMMATICAL_MARK_VOCABULARIES
from alternative_workflow import AlternativeWorkflowError, create_alternative_submission
from phonological_parameters import PHONOLOGICAL_PARAMETERS
from source_period import validate_occurrence_year
from access_control import requires_reviewer
from immediate_acceptance import (ImmediateAcceptanceError,ImmediateBlockingError,
    alternative_operation,confirm_operation,grammar_operation,preview_operation)


occurrences_bp = Blueprint("occurrences", __name__)


def _grammar_values(form):
    values={field:form.get(field) for field in ("gender","plural","agentive","conjugated_form","negation","note")}
    for field in ("gender","plural","agentive","conjugated_form","negation"):values[field+"_uncertain"]=form.get(field+"_uncertain")
    return values


def _alternative_payload(form):
    proposal_kind=form.get("proposal_kind");relations=[]
    types=form.getlist("relation_target_type");targets=form.getlist("relation_target_id");parameters=form.getlist("relation_parameter");uncertain=set(form.getlist("relation_uncertain"))
    if types:
        for index,(kind,target,parameter) in enumerate(zip(types,targets,parameters)):
            if not target and not parameter:continue
            item={"phonological_parameter":parameter,"uncertain":str(index) in uncertain};item["target_submission_id" if kind=="submission" else "target_alternative_id"]=target or None;relations.append(item)
    else:
        kinds=[value for key,value in form.items() if key.startswith("relation_target_type_")];alternative_ids=form.getlist("relation_alternative_id");submission_ids=form.getlist("relation_submission_id")
        for index,kind in enumerate(kinds):
            target=(submission_ids[index] if kind=="submission" and index<len(submission_ids) else alternative_ids[index] if index<len(alternative_ids) else "");parameter=parameters[index] if index<len(parameters) else ""
            if not target and not parameter:continue
            item={"phonological_parameter":parameter,"uncertain":str(index) in uncertain};item["target_submission_id" if kind=="submission" else "target_alternative_id"]=target or None;relations.append(item)
    morphology=None
    if proposal_kind=="NEW":
        choice=form.get("morphology_component_count");components=[]
        if form.get("record_components") == "yes":
            for position,alternative,label,note in zip(form.getlist("component_position"),form.getlist("component_alternative_id"),form.getlist("component_label"),form.getlist("component_note")):
                if not alternative and not label.strip() and not note.strip():continue
                components.append({"position":position,"component_alternative_id":alternative or None,"component_label":label,"note":note})
        morphology={"component_count":None if choice in (None,"","N/A") else choice,"component_count_not_applicable":choice=="N/A","free_permutation":form.get("free_permutation"),"note":form.get("morphology_note"),"components":components}
    return {"proposal_kind":proposal_kind,"proposed_existing_alternative_id":form.get("proposed_existing_alternative_id") or None,"phonological_relation_answer":form.get("phonological_relation_answer"),"relations":relations,"analysis_note":form.get("analysis_note"),"morphology":morphology}


def _alternative_decision(form):
    action=form.get("concept_resolution_action");concept_resolution={"action":action,"concept_id":form.get("resolved_concept_id") or None,"label":form.get("new_concept_label") or None} if action else None
    return {"decision":form.get("canonical_decision"),"alternative_id":form.get("canonical_alternative_id") or form.get("proposed_existing_alternative_id"),"relation_policy":form.get("relation_policy","preserve"),"concept_resolution":concept_resolution,"approve_relations":form.get("approve_relations")=="yes","approve_morphology":form.get("approve_morphology")=="yes","nomenclature_mode":form.get("nomenclature_mode","automatic"),"labels":{key[6:]:value for key,value in form.items() if key.startswith("label_")},"nomenclature_reason":form.get("nomenclature_reason")}


def _actor(form):return {"collaborator_id":form.get("collaborator_id"),"access_role":getattr(g,"current_access_role",None)}


def _confirmation(template_kind,occurrence_id,operation):
    db=conectar()
    try:
        occurrence=db.execute("SELECT occurrence_id,original_gloss FROM occurrence WHERE occurrence_id=?",(occurrence_id,)).fetchone()
        current=db.execute("SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1",(occurrence_id,)).fetchone() if template_kind=="grammar" else None
        result=preview_operation(db,operation)
    except (ValueError,sqlite3.IntegrityError) as error:return str(error),400
    finally:db.close()
    summary={"occurrence":dict(occurrence) if occurrence else None,"current":dict(current) if current else None,"proposed":_grammar_values(request.form) if template_kind=="grammar" else _alternative_payload(request.form),"decision":_alternative_decision(request.form) if template_kind=="alternative" else None}
    return render_template("confirmar_aceptacion_inmediata.html",kind=template_kind,occurrence_id=occurrence_id,payload=list(request.form.lists()),preflight=result,summary=summary)


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
        SELECT occurrence_id, source_id, original_gloss, source_detail_1,
               source_detail_2, occurrence_year, usage_examples_present,
               grammatical_info_present, grammatical_note, provenance_note
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
    source_detail_1 = request.form.get("source_detail_1", "").strip() or None
    source_detail_2 = request.form.get("source_detail_2", "").strip() or None
    usage_examples_present = 1 if request.form.get("usage_examples_present") == "1" else 0
    grammatical_info_present = 1 if request.form.get("grammatical_info_present") == "1" else 0
    grammatical_note = request.form.get("grammatical_note", "").strip() or None
    if not grammatical_info_present:
        grammatical_note = None
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
                   source_locator, provenance_note, occurrence_year,
                   source_detail_1,source_detail_2,usage_examples_present,
                   grammatical_info_present,grammatical_note
            FROM occurrence WHERE occurrence_id = ?
        """, (occurrence_id,)).fetchone()
        if actual is None:
            conexion.rollback()
            return "La ocurrencia no existe.", 404
        occurrence_year = validate_occurrence_year(
            conexion, source_id, occurrence_year_value
        )
        new_state = (
            int(source_id), original_gloss, source_detail_1, source_detail_2,
            occurrence_year, usage_examples_present, grammatical_info_present,
            grammatical_note, provenance_note
        )
        previous_editable_state = (
            actual["source_id"], actual["original_gloss"],
            actual["source_detail_1"], actual["source_detail_2"],
            actual["occurrence_year"], actual["usage_examples_present"],
            actual["grammatical_info_present"], actual["grammatical_note"],
            actual["provenance_note"]
        )
        if new_state != previous_editable_state:
            conexion.execute("""
                INSERT INTO occurrence_revision (
                    occurrence_id, legacy_occurrence_id, source_id,
                    original_gloss, hyperlink, legacy_source_detail_1,
                    legacy_source_detail_2, source_locator, provenance_note,
                    occurrence_year, source_detail_1,source_detail_2,
                    usage_examples_present,grammatical_info_present,
                    grammatical_note,change_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                occurrence_id, actual["legacy_occurrence_id"],
                actual["source_id"], actual["original_gloss"],
                actual["hyperlink"], actual["legacy_source_detail_1"],
                actual["legacy_source_detail_2"], actual["source_locator"],
                actual["provenance_note"], actual["occurrence_year"],
                actual["source_detail_1"],actual["source_detail_2"],
                actual["usage_examples_present"],actual["grammatical_info_present"],
                actual["grammatical_note"],change_note
            ))
        cursor = conexion.execute("""
            UPDATE occurrence SET
                source_id=?,original_gloss=?,source_detail_1=?,source_detail_2=?,
                occurrence_year=?,usage_examples_present=?,
                grammatical_info_present=?,grammatical_note=?,provenance_note=?,
                updated_at = CURRENT_TIMESTAMP
            WHERE occurrence_id = ?
        """, (*new_state, occurrence_id))
        if cursor.rowcount != 1:
            conexion.rollback()
            return "La ocurrencia no existe.", 404
        conexion.commit()
    except (sqlite3.IntegrityError, ValueError):
        conexion.rollback()
        return "La fuente o el año de la ocurrencia no son válidos.", 400
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
        "accepted": "Análisis gramatical aceptado inmediatamente.",
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
    form_values = _grammar_values(request.form)
    conexion = conectar()
    try:
        create_grammar_submission(
            conexion, occurrence_id, form_values,
            collaborator_id=request.form.get("collaborator_id"),
            access_role=getattr(g, "current_access_role", None),
        )
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


@occurrences_bp.post("/ocurrencias/<int:occurrence_id>/gramatica/aceptacion-inmediata/preview")
@requires_reviewer
def preview_grammar_immediate(occurrence_id):
    operation=grammar_operation(occurrence_id,_grammar_values(request.form),actor_context=_actor(request.form),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"))
    return _confirmation("grammar",occurrence_id,operation)


@occurrences_bp.post("/ocurrencias/<int:occurrence_id>/gramatica/aceptacion-inmediata/confirmar")
@requires_reviewer
def confirm_grammar_immediate(occurrence_id):
    if request.form.get("confirm_immediate")!="yes":return "Debe confirmar explícitamente la aceptación inmediata.",400
    db=conectar();operation=grammar_operation(occurrence_id,_grammar_values(request.form),actor_context=_actor(request.form),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"))
    try:confirm_operation(db,operation)
    except ImmediateBlockingError as error:return str(error),409
    except (ValueError,sqlite3.IntegrityError) as error:return str(error),400
    finally:db.close()
    return redirect(url_for("occurrences.mostrar_gramatica",occurrence_id=occurrence_id,result="accepted"))


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
    component_alternatives=conexion.execute("""
        SELECT a.alternative_id,a.working_label,c.preferred_label,
               GROUP_CONCAT(o.original_gloss, ' / ') AS evidence_glosses
        FROM alternative a JOIN concept c USING(concept_id)
        LEFT JOIN assignment ass ON ass.alternative_id=a.alternative_id AND ass.is_current=1
        LEFT JOIN occurrence o ON o.occurrence_id=ass.occurrence_id
        WHERE a.retired_at IS NULL
        GROUP BY a.alternative_id,a.working_label,c.preferred_label
        ORDER BY c.preferred_label,a.working_label
    """).fetchall()
    concepts=conexion.execute("SELECT concept_id,preferred_label FROM concept ORDER BY preferred_label").fetchall()
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
        phonological_parameters=PHONOLOGICAL_PARAMETERS, history=history,
        component_alternatives=component_alternatives,
        concepts=concepts,
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
    if not target_types:
        kinds=[value for key,value in request.form.items() if key.startswith("relation_target_type_")]
        alternative_ids=request.form.getlist("relation_alternative_id")
        submission_ids=request.form.getlist("relation_submission_id")
        for index,kind in enumerate(kinds):
            target=(submission_ids[index] if kind=="submission" and index<len(submission_ids)
                    else alternative_ids[index] if index<len(alternative_ids) else "")
            parameter=parameters[index] if index<len(parameters) else ""
            if not target and not parameter: continue
            relation={"phonological_parameter":parameter,"uncertain":str(index) in uncertain}
            relation["target_submission_id" if kind=="submission" else "target_alternative_id"]=target or None
            relations.append(relation)
    morphology=None
    if proposal_kind=="NEW":
        count_choice=request.form.get("morphology_component_count")
        not_applicable=count_choice=="N/A"
        component_count=None if count_choice in (None,"","N/A") else count_choice
        component_positions=request.form.getlist("component_position")
        component_alternatives=request.form.getlist("component_alternative_id")
        component_labels=request.form.getlist("component_label")
        component_notes=request.form.getlist("component_note")
        components=[]
        if request.form.get("record_components") == "yes":
            for position,alternative,label,note in zip(component_positions,component_alternatives,component_labels,component_notes):
                if not alternative and not label.strip() and not note.strip(): continue
                components.append({"position":position,"component_alternative_id":alternative or None,"component_label":label,"note":note})
        morphology={"component_count":component_count,"component_count_not_applicable":not_applicable,"free_permutation":request.form.get("free_permutation"),"note":request.form.get("morphology_note"),"components":components}
    conexion = conectar()
    try:
        create_alternative_submission(
            conexion,occurrence_id,proposal_kind,
            proposed_existing_alternative_id=alternative_id,
            phonological_relation_answer=request.form.get("phonological_relation_answer"),
            relations=relations,analysis_note=request.form.get("analysis_note"),
            morphology=morphology,
            collaborator_id=request.form.get("collaborator_id"),
            access_role=getattr(g, "current_access_role", None),
        )
    except (AlternativeWorkflowError,ValueError,sqlite3.IntegrityError) as error:
        return str(error),400
    finally:
        conexion.close()
    return redirect(url_for("occurrences.clasificar_ocurrencia", occurrence_id=occurrence_id))


@occurrences_bp.post("/ocurrencias/<int:occurrence_id>/clasificar/aceptacion-inmediata/preview")
@requires_reviewer
def preview_alternative_immediate(occurrence_id):
    operation=alternative_operation(occurrence_id,_alternative_payload(request.form),_alternative_decision(request.form),actor_context=_actor(request.form),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"))
    return _confirmation("alternative",occurrence_id,operation)


@occurrences_bp.post("/ocurrencias/<int:occurrence_id>/clasificar/aceptacion-inmediata/confirmar")
@requires_reviewer
def confirm_alternative_immediate(occurrence_id):
    if request.form.get("confirm_immediate")!="yes":return "Debe confirmar explícitamente la aceptación inmediata.",400
    db=conectar();operation=alternative_operation(occurrence_id,_alternative_payload(request.form),_alternative_decision(request.form),actor_context=_actor(request.form),reviewed_by=request.form.get("reviewed_by"),review_note=request.form.get("review_note"))
    try:confirm_operation(db,operation)
    except ImmediateBlockingError as error:return str(error),409
    except (ValueError,sqlite3.IntegrityError) as error:return str(error),400
    finally:db.close()
    return redirect(url_for("occurrences.clasificar_ocurrencia",occurrence_id=occurrence_id))
