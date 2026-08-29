from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar
from concept_labels import alternative_display_label, human_concept_label
from occurrence_grammar import (
    EmptyGrammarError,
    OccurrenceNotFoundError,
    create_or_replace_occurrence_grammar,
)
from routes.submissions import crear_o_reemplazar_assignment


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
               sub.status AS submission_status,
               c.preferred_label, al.working_label,
               EXISTS (
                   SELECT 1 FROM occurrence_grammar AS og
                   WHERE og.occurrence_id = o.occurrence_id
                     AND og.is_current = 1
               ) AS has_current_grammar
        FROM occurrence AS o
        LEFT JOIN submission AS sub
            ON sub.occurrence_id = o.occurrence_id
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
        workflow_labels = {
            None: "Sin aporte",
            "pending": "Pendiente",
            "accepted": "Aceptado",
            "rejected": "Rechazado",
        }
        occurrence["workflow_status"] = workflow_labels[row["submission_status"]]
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
        SELECT o.occurrence_id, o.original_gloss, o.hyperlink, s.source_name
        FROM occurrence AS o
        JOIN source AS s ON s.source_id = o.source_id
        WHERE o.occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if occurrence is None:
        return None, None, []
    current = conexion.execute("""
        SELECT occurrence_grammar_id, gender, plural, agentive,
               conjugated_form, negation, grammar_note, is_current,
               supersedes_occurrence_grammar_id, created_at, created_by,
               change_note
        FROM occurrence_grammar
        WHERE occurrence_id = ? AND is_current = 1
    """, (occurrence_id,)).fetchone()
    history = conexion.execute("""
        SELECT occurrence_grammar_id, gender, plural, agentive,
               conjugated_form, negation, grammar_note, is_current,
               supersedes_occurrence_grammar_id, created_at, created_by,
               change_note
        FROM occurrence_grammar
        WHERE occurrence_id = ?
        ORDER BY is_current DESC, occurrence_grammar_id DESC
    """, (occurrence_id,)).fetchall()
    return occurrence, current, history


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/gramatica")
def mostrar_gramatica(occurrence_id):
    conexion = conectar()
    try:
        occurrence, current, history = _load_grammar_page_data(
            conexion, occurrence_id
        )
    finally:
        conexion.close()
    if occurrence is None:
        return "La ocurrencia no existe.", 404
    result_messages = {
        "saved": "Análisis gramatical guardado.",
        "noop": "No hubo cambios en el análisis gramatical.",
    }
    form_values = dict(current) if current is not None else {}
    form_values["change_note"] = ""
    return render_template(
        "gramatica_ocurrencia.html",
        occurrence=occurrence,
        current=current,
        history=history,
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
        "grammar_note": request.form.get("grammar_note"),
        "change_note": request.form.get("change_note"),
    }
    conexion = conectar()
    try:
        _, created = create_or_replace_occurrence_grammar(
            conexion,
            occurrence_id,
            gender=form_values["gender"],
            plural=form_values["plural"],
            agentive=form_values["agentive"],
            conjugated_form=form_values["conjugated_form"],
            negation=form_values["negation"],
            grammar_note=form_values["grammar_note"],
            created_by=None,
            change_note=form_values["change_note"],
        )
    except OccurrenceNotFoundError:
        return "La ocurrencia no existe.", 404
    except EmptyGrammarError:
        occurrence, current, history = _load_grammar_page_data(
            conexion, occurrence_id
        )
        return render_template(
            "gramatica_ocurrencia.html",
            occurrence=occurrence,
            current=current,
            history=history,
            form_values=form_values,
            message=None,
            error="El análisis gramatical debe contener al menos un dato.",
        ), 400
    except Exception:
        return "No fue posible guardar el análisis gramatical.", 500
    finally:
        conexion.close()
    result = "saved" if created else "noop"
    return redirect(url_for(
        "occurrences.mostrar_gramatica",
        occurrence_id=occurrence_id,
        result=result,
    ))


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/clasificar")
def clasificar_ocurrencia(occurrence_id):
    conexion = conectar()
    occurrence = conexion.execute("""
        SELECT o.occurrence_id, o.original_gloss, o.hyperlink,
               s.source_name, a.assignment_id, a.alternative_id,
               al.working_label, c.preferred_label
        FROM occurrence AS o JOIN source AS s ON s.source_id = o.source_id
        LEFT JOIN assignment AS a
            ON a.occurrence_id = o.occurrence_id AND a.is_current = 1
        LEFT JOIN alternative AS al ON al.alternative_id = a.alternative_id
        LEFT JOIN concept AS c ON c.concept_id = al.concept_id
        WHERE o.occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if occurrence is None:
        conexion.close()
        return "La ocurrencia no existe.", 404
    concepts = conexion.execute(
        "SELECT concept_id, preferred_label FROM concept ORDER BY preferred_label"
    ).fetchall()
    alternatives = conexion.execute(
        "SELECT alternative_id, concept_id, working_label FROM alternative ORDER BY alternative_id"
    ).fetchall()
    alternatives_by_concept = [
        {"concept": concept, "alternatives": [
            alternative for alternative in alternatives
            if alternative["concept_id"] == concept["concept_id"]
        ]}
        for concept in concepts
    ]
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
        alternatives_by_concept=alternatives_by_concept, history=history
    )


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/clasificar", methods=["POST"])
def guardar_clasificacion(occurrence_id):
    alternative_id = request.form.get("alternative_id") or None
    concept_id = request.form.get("concept_id") or None
    if alternative_id is None and concept_id is None:
        return "Debe seleccionar una alternativa o un concepto nuevo.", 400
    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        occurrence_exists = conexion.execute(
            "SELECT 1 FROM occurrence WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        if occurrence_exists is None:
            conexion.rollback()
            return "La ocurrencia no existe.", 404
        if alternative_id is not None:
            if conexion.execute(
                "SELECT 1 FROM alternative WHERE alternative_id = ?", (alternative_id,)
            ).fetchone() is None:
                conexion.rollback()
                return "La alternativa no existe.", 400
        else:
            if conexion.execute(
                "SELECT 1 FROM concept WHERE concept_id = ?", (concept_id,)
            ).fetchone() is None:
                conexion.rollback()
                return "El concepto no existe.", 400
            cursor = conexion.execute(
                "INSERT INTO alternative (concept_id, working_label) VALUES (?, NULL)",
                (concept_id,)
            )
            alternative_id = cursor.lastrowid
        crear_o_reemplazar_assignment(conexion, occurrence_id, int(alternative_id))
        conexion.commit()
    except sqlite3.Error:
        conexion.rollback()
        return "No fue posible guardar la clasificación.", 500
    finally:
        conexion.close()
    return redirect(url_for("occurrences.clasificar_ocurrencia", occurrence_id=occurrence_id))
