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
            s.source_name,
            o.original_gloss,
            o.hyperlink,
            CASE
                WHEN c.preferred_label IS NULL
                    AND al.working_label IS NULL
                    THEN 'Sin clasificación'
                WHEN al.working_label IS NULL
                    THEN c.preferred_label
                WHEN c.preferred_label IS NULL
                    THEN al.working_label
                ELSE c.preferred_label || ' / ' || al.working_label
            END AS current_classification
        FROM occurrence AS o
        JOIN submission AS sub
            ON sub.occurrence_id = o.occurrence_id
            AND sub.status = 'accepted'
        JOIN source AS s
            ON o.source_id = s.source_id
        LEFT JOIN assignment AS a
            ON a.occurrence_id = o.occurrence_id
            AND a.is_current = 1
        LEFT JOIN alternative AS al
            ON al.alternative_id = a.alternative_id
        LEFT JOIN concept AS c
            ON c.concept_id = al.concept_id
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
            original_gloss,
            hyperlink,
            source_locator,
            provenance_note
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

    conexion.close()

    return render_template(
        "editar_ocurrencia.html",
        ocurrencia=ocurrencia,
        fuentes=fuentes
    )


@occurrences_bp.route(
    "/ocurrencias/<int:occurrence_id>/actualizar",
    methods=["POST"]
)
def actualizar_ocurrencia(occurrence_id):

    source_id = request.form.get("source_id", "")
    original_gloss = request.form.get("original_gloss", "").strip()
    hyperlink = request.form.get("hyperlink", "").strip()
    source_locator = request.form.get("source_locator", "").strip()
    provenance_note = request.form.get("provenance_note", "").strip()
    change_note = request.form.get("change_note", "").strip() or None

    if not source_id:

        return (
            "La fuente es obligatoria.",
            400
        )

    conexion = conectar()

    try:
        conexion.execute("BEGIN IMMEDIATE")

        actual = conexion.execute("""
            SELECT
                source_id,
                original_gloss,
                hyperlink,
                source_locator,
                provenance_note
            FROM occurrence
            WHERE occurrence_id = ?
        """, (occurrence_id,)).fetchone()

        if actual is None:

            conexion.rollback()

            return (
                "La ocurrencia no existe.",
                404
            )

        nuevo_estado = (
            int(source_id),
            original_gloss,
            hyperlink,
            source_locator,
            provenance_note
        )
        estado_actual = tuple(actual)

        if nuevo_estado != estado_actual:
            conexion.execute("""
                INSERT INTO occurrence_revision (
                    occurrence_id,
                    source_id,
                    original_gloss,
                    hyperlink,
                    source_locator,
                    provenance_note,
                    change_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                occurrence_id,
                actual["source_id"],
                actual["original_gloss"],
                actual["hyperlink"],
                actual["source_locator"],
                actual["provenance_note"],
                change_note
            ))

        cursor = conexion.execute("""
            UPDATE occurrence
            SET
                source_id = ?,
                original_gloss = ?,
                hyperlink = ?,
                source_locator = ?,
                provenance_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE occurrence_id = ?
        """, (
            source_id,
            original_gloss,
            hyperlink,
            source_locator,
            provenance_note,
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
