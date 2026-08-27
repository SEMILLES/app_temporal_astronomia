from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar


submissions_bp = Blueprint("submissions", __name__)


@submissions_bp.route("/aportes/nuevo")
def nuevo_aporte():

    conexion = conectar()

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

    alternativas = conexion.execute("""
        SELECT alternative_id, concept_id, working_label
        FROM alternative
        ORDER BY alternative_id
    """).fetchall()

    conexion.close()

    return render_template(
        "nueva_ocurrencia.html",
        fuentes=fuentes,
        conceptos=conceptos,
        alternativas=alternativas
    )


@submissions_bp.route("/aportes", methods=["POST"])
def guardar_aporte():

    source_id = request.form.get("source_id", "")
    proposed_concept_id = request.form.get("proposed_concept_id") or None
    proposed_alternative_id = request.form.get("proposed_alternative_id") or None
    proposed_alternative_label = request.form.get(
        "proposed_alternative_label", ""
    ).strip() or None
    proposal_type = request.form.get("proposal_type", "not_sure")
    original_gloss = request.form.get("original_gloss", "").strip()
    hyperlink = request.form.get("hyperlink", "").strip()

    if not source_id:

        return (
            "La fuente es obligatoria.",
            400
        )

    conexion = conectar()

    try:

        cursor = conexion.execute("""
            INSERT INTO occurrence (source_id, original_gloss, hyperlink)
            VALUES (?, ?, ?)
        """, (
            source_id,
            original_gloss,
            hyperlink
        ))

        conexion.execute("""
            INSERT INTO submission (
                occurrence_id, proposed_concept_id, proposed_alternative_id,
                proposed_alternative_label, proposal_type
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            cursor.lastrowid,
            proposed_concept_id,
            proposed_alternative_id,
            proposed_alternative_label,
            proposal_type
        ))

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.rollback()

        return (
            "La fuente o el concepto no son válidos.",
            400
        )

    finally:

        conexion.close()

    return redirect(
        url_for("submissions.aportes")
    )


@submissions_bp.route("/ocurrencias/guardar", methods=["POST"])
def guardar_aporte_compatible():

    return guardar_aporte()


@submissions_bp.route("/aportes", methods=["GET"])
def aportes():

    conexion = conectar()

    aportes = conexion.execute("""
        SELECT
            os.submission_id,
            c.preferred_label,
            s.source_name,
            o.original_gloss,
            o.hyperlink,
            os.status,
            os.occurrence_id,
            os.submitted_at,
            os.reviewed_at
        FROM submission AS os
        JOIN occurrence AS o ON o.occurrence_id = os.occurrence_id
        LEFT JOIN concept AS c ON os.proposed_concept_id = c.concept_id
        JOIN source AS s ON o.source_id = s.source_id
        ORDER BY os.submission_id DESC
    """).fetchall()

    conexion.close()

    return render_template("aportes.html", aportes=aportes)


@submissions_bp.route("/aportes/pendientes")
def revisar_aportes():

    conexion = conectar()

    aportes = conexion.execute("""
        SELECT
            os.submission_id,
            c.preferred_label,
            s.source_name,
            o.original_gloss,
            o.hyperlink,
            os.submitted_at
        FROM submission AS os
        JOIN occurrence AS o ON o.occurrence_id = os.occurrence_id
        LEFT JOIN concept AS c ON os.proposed_concept_id = c.concept_id
        JOIN source AS s ON o.source_id = s.source_id
        WHERE os.status = 'pending'
        ORDER BY os.submission_id
    """).fetchall()

    conexion.close()

    return render_template(
        "revision_aportes.html",
        aportes=aportes
    )


@submissions_bp.route(
    "/aportes/<int:submission_id>/aprobar",
    methods=["POST"]
)
def aprobar_aporte(submission_id):

    conexion = conectar()

    try:

        conexion.execute("BEGIN IMMEDIATE")

        aporte = conexion.execute("""
            SELECT
                status
            FROM submission
            WHERE submission_id = ?
        """, (submission_id,)).fetchone()

        if aporte is None:

            conexion.rollback()

            return (
                "El aporte no existe.",
                404
            )

        if aporte["status"] != "pending":

            conexion.rollback()

            return (
                "El aporte ya fue revisado.",
                409
            )

        actualizacion = conexion.execute("""
            UPDATE submission
            SET
                status = 'accepted',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE
                submission_id = ?
                AND status = 'pending'
        """, (submission_id,))

        if actualizacion.rowcount != 1:

            conexion.rollback()

            return (
                "El aporte ya fue revisado.",
                409
            )

        conexion.commit()

    except sqlite3.Error:

        conexion.rollback()

        return (
            "No fue posible aprobar el aporte.",
            500
        )

    finally:

        conexion.close()

    return redirect(
        url_for("submissions.revisar_aportes")
    )


@submissions_bp.route(
    "/aportes/<int:submission_id>/rechazar",
    methods=["POST"]
)
def rechazar_aporte(submission_id):

    conexion = conectar()

    try:

        conexion.execute("BEGIN IMMEDIATE")

        aporte = conexion.execute("""
            SELECT status
            FROM submission
            WHERE submission_id = ?
        """, (submission_id,)).fetchone()

        if aporte is None:

            conexion.rollback()

            return (
                "El aporte no existe.",
                404
            )

        if aporte["status"] != "pending":

            conexion.rollback()

            return (
                "El aporte ya fue revisado.",
                409
            )

        actualizacion = conexion.execute("""
            UPDATE submission
            SET
                status = 'rejected',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE
                submission_id = ?
                AND status = 'pending'
        """, (submission_id,))

        if actualizacion.rowcount != 1:

            conexion.rollback()

            return (
                "El aporte ya fue revisado.",
                409
            )

        conexion.commit()

    except sqlite3.Error:

        conexion.rollback()

        return (
            "No fue posible rechazar el aporte.",
            500
        )

    finally:

        conexion.close()

    return redirect(
        url_for("submissions.revisar_aportes")
    )
