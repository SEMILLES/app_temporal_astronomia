from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3
import re

from database import conectar


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

    alternatives = conexion.execute("""
        SELECT alternative_id, working_label, created_at, retired_at
        FROM alternative
        WHERE concept_id = ?
        ORDER BY alternative_id
    """, (concept_id,)).fetchall()

    conexion.close()

    return render_template(
        "alternativas.html",
        concepto=concepto,
        alternativas=alternatives
    )


@alternatives_bp.route(
    "/conceptos/<int:concept_id>/alternativas/nueva",
    methods=["POST"]
)
def nueva_alternativa(concept_id):

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

        conexion.execute("""
            INSERT INTO alternative (concept_id, working_label)
            VALUES (?, ?)
        """, (concept_id, working_label))
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "No fue posible crear la alternativa.", 400
    finally:
        conexion.close()

    return redirect(url_for("alternatives.alternativas", concept_id=concept_id))


@alternatives_bp.route("/alternativas/<int:alternative_id>/editar")
def editar_alternativa(alternative_id):

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