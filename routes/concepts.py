from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

import sqlite3

from database import conectar
from concept_labels import InvalidConceptLabel, normalize_concept_label


concepts_bp = Blueprint(
    "concepts",
    __name__
)


# =========================================================
# CONSULTAR CONCEPTOS
# =========================================================

@concepts_bp.route("/conceptos")
def conceptos():

    conexion = conectar()

    conceptos = conexion.execute("""
        SELECT
            concept_id,
            preferred_label

        FROM concept

        ORDER BY preferred_label
    """).fetchall()

    conexion.close()

    return render_template(
        "conceptos.html",
        conceptos=conceptos
    )


# =========================================================
# CREAR CONCEPTO
# =========================================================

@concepts_bp.route(
    "/conceptos/nuevo",
    methods=["POST"]
)
def nuevo_concepto():

    try:
        preferred_label = normalize_concept_label(
            request.form.get("preferred_label", "")
        )
    except InvalidConceptLabel as error:
        return str(error), 400

    conexion = conectar()

    try:

        conexion.execute("""
            INSERT INTO concept
            (preferred_label)

            VALUES (?)
        """, (preferred_label,))

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.close()

        return (
            "Ya existe un concepto con esa etiqueta.",
            400
        )

    conexion.close()

    return redirect(
        url_for("concepts.conceptos")
    )


# =========================================================
# FORMULARIO EDITAR
# =========================================================

@concepts_bp.route(
    "/conceptos/<int:concept_id>/editar"
)
def editar_concepto(concept_id):

    conexion = conectar()

    concepto = conexion.execute("""
        SELECT
            concept_id,
            preferred_label

        FROM concept

        WHERE concept_id = ?
    """, (concept_id,)).fetchone()

    conexion.close()

    if concepto is None:

        return (
            "El concepto no existe.",
            404
        )

    return render_template(
        "editar_concepto.html",
        concepto=concepto
    )


# =========================================================
# ACTUALIZAR CONCEPTO
# =========================================================

@concepts_bp.route(
    "/conceptos/<int:concept_id>/actualizar",
    methods=["POST"]
)
def actualizar_concepto(concept_id):

    try:
        preferred_label = normalize_concept_label(
            request.form.get("preferred_label", "")
        )
    except InvalidConceptLabel as error:
        return str(error), 400

    conexion = conectar()

    try:

        cursor = conexion.execute("""
            UPDATE concept

            SET preferred_label = ?

            WHERE concept_id = ?
        """, (
            preferred_label,
            concept_id
        ))

        if cursor.rowcount == 0:

            conexion.close()

            return (
                "El concepto no existe.",
                404
            )

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.close()

        return (
            "Ya existe otro concepto con esa etiqueta.",
            400
        )

    conexion.close()

    return redirect(
        url_for("concepts.conceptos")
    )
