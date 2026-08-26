from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar


occurrences_bp = Blueprint("occurrences", __name__)


@occurrences_bp.route("/ocurrencias")
def ocurrencias():

    conexion = conectar()

    ocurrencias = conexion.execute("""
        SELECT
            o.occurrence_id,
            c.preferred_label,
            s.source_name,
            o.original_gloss,
            o.hyperlink
        FROM occurrence AS o
        JOIN concept AS c
            ON o.concept_id = c.concept_id
        JOIN source AS s
            ON o.source_id = s.source_id
        ORDER BY o.occurrence_id
    """).fetchall()

    conexion.close()

    return render_template(
        "ocurrencias.html",
        ocurrencias=ocurrencias
    )


@occurrences_bp.route("/ocurrencias/nueva")
def nueva_ocurrencia():

    return redirect(
        url_for("submissions.nuevo_aporte")
    )


@occurrences_bp.route("/ocurrencias/<int:occurrence_id>/editar")
def editar_ocurrencia(occurrence_id):

    conexion = conectar()

    ocurrencia = conexion.execute("""
        SELECT
            occurrence_id,
            source_id,
            concept_id,
            original_gloss,
            hyperlink
        FROM occurrence
        WHERE occurrence_id = ?
    """, (occurrence_id,)).fetchone()

    if ocurrencia is None:

        conexion.close()

        return (
            "La ocurrencia no existe.",
            404
        )

    fuentes = conexion.execute("""
        SELECT source_id, source_name
        FROM source
        ORDER BY source_name
    """).fetchall()

    conceptos = conexion.execute("""
        SELECT concept_id, preferred_label
        FROM concept
        ORDER BY preferred_label
    """).fetchall()

    conexion.close()

    return render_template(
        "editar_ocurrencia.html",
        ocurrencia=ocurrencia,
        fuentes=fuentes,
        conceptos=conceptos
    )


@occurrences_bp.route(
    "/ocurrencias/<int:occurrence_id>/actualizar",
    methods=["POST"]
)
def actualizar_ocurrencia(occurrence_id):

    source_id = request.form.get("source_id", "")
    concept_id = request.form.get("concept_id", "")
    original_gloss = request.form.get("original_gloss", "").strip()
    hyperlink = request.form.get("hyperlink", "").strip()

    if not source_id or not concept_id:

        return (
            "Fuente y concepto son obligatorios.",
            400
        )

    conexion = conectar()

    try:

        cursor = conexion.execute("""
            UPDATE occurrence
            SET
                source_id = ?,
                concept_id = ?,
                original_gloss = ?,
                hyperlink = ?
            WHERE occurrence_id = ?
        """, (
            source_id,
            concept_id,
            original_gloss,
            hyperlink,
            occurrence_id
        ))

        if cursor.rowcount == 0:

            conexion.close()

            return (
                "La ocurrencia no existe.",
                404
            )

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.close()

        return (
            "La fuente o el concepto no son válidos.",
            400
        )

    conexion.close()

    return redirect(
        url_for("occurrences.ocurrencias")
    )
