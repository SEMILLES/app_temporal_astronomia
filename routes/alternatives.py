from flask import Blueprint, render_template, request, redirect, url_for, g, abort

import sqlite3
import re

from database import conectar
from activity import record_activity


alternatives_bp = Blueprint("alternatives", __name__)


def structured_working_label(form, fallback=None):

    number = form.get("working_label_number", "").strip()
    letter = form.get("working_label_letter", "").strip().lower()

    if number or letter:
        if not number.isdigit() or not 1 <= int(number) <= 10:
            raise ValueError
        if not re.fullmatch(r"[a-z]", letter):
            raise ValueError
        return f"{int(number)}{letter}"

    return fallback


def generated_working_label(conexion, concept_id, related_alternative_id=None):
    rows = conexion.execute("""
        SELECT working_label
        FROM alternative
        WHERE concept_id = ?
    """, (concept_id,)).fetchall()
    labels = [row["working_label"] for row in rows if row["working_label"]]
    parsed = [
        (int(match.group(1)), match.group(2))
        for label in labels
        for match in [re.fullmatch(r"(10|[1-9])([a-z])", label)]
        if match
    ]
    if not parsed and labels:
        return None

    if related_alternative_id is not None:
        base = conexion.execute("""
            SELECT working_label
            FROM alternative
            WHERE alternative_id = ? AND concept_id = ?
        """, (related_alternative_id, concept_id)).fetchone()
        if base is None:
            raise ValueError
        match = re.fullmatch(r"(10|[1-9])([a-z])", base["working_label"] or "")
        if match is None:
            return None
        number = int(match.group(1))
        used_letters = {letter for item_number, letter in parsed if item_number == number}
        for code in range(ord("a"), ord("z") + 1):
            letter = chr(code)
            if letter not in used_letters:
                return f"{number}{letter}"
        return None

    max_number = max((number for number, _ in parsed), default=0)
    if max_number >= 10:
        return None
    return f"{max_number + 1}a"


@alternatives_bp.route("/conceptos/<int:concept_id>/alternativas")
def alternativas(concept_id):

    conexion = conectar()

    concepto = conexion.execute("""
        SELECT concept_id, preferred_label
        FROM concept
        WHERE concept_id = ?
    """, (concept_id,)).fetchone()

    if concepto is None:
        conexion.close()
        return "El concepto no existe.", 404

    alternative_rows = conexion.execute("""
        SELECT a.alternative_id, a.working_label, a.original_code,
               a.created_at, a.retired_at,
               EXISTS(SELECT 1 FROM alternative_morphology m
                      WHERE m.alternative_id=a.alternative_id AND m.is_current=1)
                   AS has_current_morphology
        FROM alternative a
        WHERE a.concept_id = ?
        ORDER BY a.alternative_id
    """, (concept_id,)).fetchall()

    alternatives = {
        row["alternative_id"]: {
            "alternative": row,
            "occurrences": [],
            "relations": []
        }
        for row in alternative_rows
    }

    occurrence_rows = conexion.execute("""
        SELECT
            a.alternative_id,
            o.occurrence_id,
            o.occurrence_year,
            o.original_gloss,
            o.source_locator,
            o.hyperlink,
            s.source_name,
            s.start_year,
            s.end_year,
            s.end_year_status
        FROM assignment AS a
        JOIN occurrence AS o ON o.occurrence_id = a.occurrence_id
        JOIN source AS s ON s.source_id = o.source_id
        JOIN alternative AS alt
            ON alt.alternative_id = a.alternative_id
        WHERE alt.concept_id = ? AND a.is_current = 1
        ORDER BY a.alternative_id, o.occurrence_id
    """, (concept_id,)).fetchall()

    for occurrence in occurrence_rows:
        alternatives[occurrence["alternative_id"]]["occurrences"].append(
            occurrence
        )

    relation_rows = conexion.execute("""
        SELECT
            r.alternative_low_id AS alternative_a_id,
            r.alternative_high_id AS alternative_b_id,
            r.phonological_parameter,
            alternative_a.working_label AS alternative_a_working_label,
            alternative_b.working_label AS alternative_b_working_label,
            concept_a.preferred_label AS alternative_a_concept_label,
            concept_b.preferred_label AS alternative_b_concept_label
        FROM alternative_relation AS r
        JOIN alternative AS alternative_a
            ON alternative_a.alternative_id = r.alternative_low_id
        JOIN alternative AS alternative_b
            ON alternative_b.alternative_id = r.alternative_high_id
        JOIN concept AS concept_a
            ON concept_a.concept_id = alternative_a.concept_id
        JOIN concept AS concept_b
            ON concept_b.concept_id = alternative_b.concept_id
        WHERE r.is_current = 1
          AND (alternative_a.concept_id = ? OR alternative_b.concept_id = ?)
    """, (concept_id, concept_id)).fetchall()

    for relation in relation_rows:
        for alternative_id, related_id, related_label, related_concept in (
            (
                relation["alternative_a_id"],
                relation["alternative_b_id"],
                relation["alternative_b_working_label"],
                relation["alternative_b_concept_label"]
            ),
            (
                relation["alternative_b_id"],
                relation["alternative_a_id"],
                relation["alternative_a_working_label"],
                relation["alternative_a_concept_label"]
            )
        ):
            if alternative_id in alternatives:
                alternatives[alternative_id]["relations"].append({
                    "alternative_id": related_id,
                    "working_label": related_label,
                    "concept_label": related_concept,
                    "phonological_parameter": (
                        relation["phonological_parameter"]
                    )
                })

    alternative_groups = list(alternatives.values())
    for group in alternative_groups:
        group["relations"].sort(
            key=lambda relation: relation["alternative_id"]
        )

    conexion.close()

    return render_template(
        "alternativas.html",
        concepto=concepto,
        alternative_groups=alternative_groups
    )


@alternatives_bp.route(
    "/conceptos/<int:concept_id>/alternativas/nueva",
    methods=["POST"]
)
def nueva_alternativa(concept_id):
    abort(404)
    try:
        working_label = structured_working_label(request.form)
    except ValueError:
        return "Número y letra no son válidos.", 400
    conexion = conectar()

    try:
        if conexion.execute(
            "SELECT 1 FROM concept WHERE concept_id = ?", (concept_id,)
        ).fetchone() is None:
            return "El concepto no existe.", 404

        conexion.execute("BEGIN IMMEDIATE")
        alternative_id = conexion.execute("""
            INSERT INTO alternative (concept_id, working_label)
            VALUES (?, ?)
        """, (concept_id, working_label)).lastrowid
        role = getattr(g, "current_access_role", None)
        if role:
            record_activity(conexion,"alternative_created",entity_type="alternative",
                            entity_id=alternative_id,
                            collaborator_id=request.form.get("collaborator_id"),
                            access_role=role)
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "No fue posible crear la alternativa.", 400
    finally:
        conexion.close()

    return redirect(url_for("alternatives.alternativas", concept_id=concept_id))


@alternatives_bp.route("/alternativas/<int:alternative_id>/editar")
def editar_alternativa(alternative_id):
    abort(404)
    conexion = conectar()
    alternative = conexion.execute("""
        SELECT alternative_id, concept_id, working_label
        FROM alternative
        WHERE alternative_id = ?
    """, (alternative_id,)).fetchone()
    conexion.close()

    if alternative is None:
        return "La alternativa no existe.", 404

    label = alternative["working_label"] or ""
    match = re.fullmatch(r"(10|[1-9])([a-z])", label)

    return render_template(
        "editar_alternativa.html",
        alternativa=alternative,
        working_label_number=match.group(1) if match else None,
        working_label_letter=match.group(2) if match else None,
        working_label_is_unstructured=bool(label) and match is None
    )


@alternatives_bp.route(
    "/alternativas/<int:alternative_id>/actualizar",
    methods=["POST"]
)
def actualizar_alternativa(alternative_id):
    abort(404)
    fallback = request.form.get("working_label_fallback", "").strip() or None
    try:
        working_label = structured_working_label(request.form, fallback)
    except ValueError:
        return "Número y letra no son válidos.", 400
    conexion = conectar()

    try:
        alternative = conexion.execute("""
            SELECT concept_id
            FROM alternative
            WHERE alternative_id = ?
        """, (alternative_id,)).fetchone()

        if alternative is None:
            return "La alternativa no existe.", 404

        conexion.execute("""
            UPDATE alternative
            SET working_label = ?
            WHERE alternative_id = ?
        """, (working_label, alternative_id))
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "No fue posible actualizar la alternativa.", 400
    finally:
        conexion.close()

    return redirect(
        url_for(
            "alternatives.alternativas",
            concept_id=alternative["concept_id"]
        )
    )
