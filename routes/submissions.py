from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar
from routes.alternatives import structured_working_label


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
            os.proposed_alternative_id,
            os.proposed_alternative_label,
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
            os.proposed_alternative_id,
            os.proposed_alternative_label,
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

    conceptos = conexion.execute("""
        SELECT concept_id, preferred_label
        FROM concept
        ORDER BY preferred_label
    """).fetchall()

    alternativas = conexion.execute("""
        SELECT a.alternative_id, a.working_label,
               c.preferred_label, c.concept_id
        FROM alternative AS a
        JOIN concept AS c ON c.concept_id = a.concept_id
        ORDER BY c.preferred_label, a.alternative_id
    """).fetchall()

    alternativas_por_concepto = []
    for concepto in conceptos:
        alternativas_por_concepto.append({
            "concepto": concepto,
            "alternativas": [
                alternativa for alternativa in alternativas
                if alternativa["concept_id"] == concepto["concept_id"]
            ]
        })

    conexion.close()

    return render_template(
        "revision_aportes.html",
        aportes=aportes,
        conceptos=conceptos,
        alternativas=alternativas,
        alternativas_por_concepto=alternativas_por_concepto
    )


@submissions_bp.route(
    "/aportes/<int:submission_id>/decidir",
    methods=["POST"]
)
def decidir_aporte(submission_id):

    decision = request.form.get("decision", "")
    alternative_id = request.form.get("alternative_id") or None
    concept_id = request.form.get("concept_id") or None
    try:
        working_label = structured_working_label(request.form)
    except ValueError:
        return "Número y letra no son válidos.", 400

    conexion = conectar()

    try:

        conexion.execute("BEGIN IMMEDIATE")

        aporte = conexion.execute("""
            SELECT
                occurrence_id,
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

        if decision == "assign_existing":
            if alternative_id is None:
                return "Debe seleccionar una alternativa.", 400

            alternative = conexion.execute("""
                SELECT alternative_id
                FROM alternative
                WHERE alternative_id = ?
            """, (alternative_id,)).fetchone()
            if alternative is None:
                return "La alternativa no existe.", 400

            crear_o_reemplazar_assignment(
                conexion,
                aporte["occurrence_id"],
                int(alternative_id)
            )

        elif decision == "create_new":
            if concept_id is None:
                return "Debe seleccionar un concepto.", 400

            if conexion.execute(
                "SELECT 1 FROM concept WHERE concept_id = ?", (concept_id,)
            ).fetchone() is None:
                return "El concepto no existe.", 400

            cursor = conexion.execute("""
                INSERT INTO alternative (concept_id, working_label)
                VALUES (?, ?)
            """, (concept_id, working_label))
            crear_o_reemplazar_assignment(
                conexion,
                aporte["occurrence_id"],
                cursor.lastrowid
            )

        elif decision not in ("accept_unclassified", "reject"):
            return "La decisión no es válida.", 400

        new_status = "rejected" if decision == "reject" else "accepted"

        actualizacion = conexion.execute("""
            UPDATE submission
            SET
                status = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE
                submission_id = ?
                AND status = 'pending'
        """, (new_status, submission_id))

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


def crear_o_reemplazar_assignment(conexion, occurrence_id, alternative_id):

    current = conexion.execute("""
        SELECT assignment_id, alternative_id
        FROM assignment
        WHERE occurrence_id = ? AND is_current = 1
    """, (occurrence_id,)).fetchone()

    if current is not None and current["alternative_id"] == alternative_id:
        return

    previous_id = None
    if current is not None:
        previous_id = current["assignment_id"]
        conexion.execute("""
            UPDATE assignment
            SET is_current = 0
            WHERE assignment_id = ?
        """, (previous_id,))

    conexion.execute("""
        INSERT INTO assignment (
            occurrence_id, alternative_id, is_current,
            supersedes_assignment_id
        )
        VALUES (?, ?, 1, ?)
    """, (occurrence_id, alternative_id, previous_id))


@submissions_bp.route(
    "/aportes/<int:submission_id>/aprobar",
    methods=["GET", "POST"]
)
def aprobar_aporte_compatibilidad(submission_id):

    return redirect(url_for("submissions.revisar_aportes"))


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
