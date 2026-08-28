from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar
from routes.alternatives import generated_working_label
from routes.sources import source_insert_values


submissions_bp = Blueprint("submissions", __name__)


@submissions_bp.route("/aportes/nuevo")
def nuevo_aporte():

    conexion = conectar()

    fuentes = conexion.execute("""
        SELECT source_id, source_name, start_year, end_year, end_year_status
        FROM source
        ORDER BY source_name
    """).fetchall()

    conceptos = conexion.execute("""
        SELECT concept_id, preferred_label
        FROM concept
        ORDER BY preferred_label
    """).fetchall()

    alternativas = conexion.execute("""
        SELECT alternative_id, concept_id, working_label, original_code
        FROM alternative
        ORDER BY alternative_id
    """).fetchall()

    alternative_details = []
    for alternative in alternativas:
        occurrences = conexion.execute("""
            SELECT s.source_name, o.occurrence_year, s.start_year,
                   s.end_year, s.end_year_status, o.original_gloss,
                   o.source_locator, o.hyperlink
            FROM assignment AS a
            JOIN occurrence AS o ON o.occurrence_id = a.occurrence_id
            JOIN submission AS sub ON sub.occurrence_id = o.occurrence_id
                AND sub.status = 'accepted'
            JOIN source AS s ON s.source_id = o.source_id
            WHERE a.alternative_id = ? AND a.is_current = 1
            ORDER BY o.occurrence_id
        """, (alternative["alternative_id"],)).fetchall()
        visual = conexion.execute("""
            SELECT m.origin_locator
            FROM alternative_media AS am
            JOIN media_asset AS m ON m.media_asset_id = am.media_asset_id
            WHERE am.alternative_id = ?
                AND (m.origin_locator LIKE 'http://%' OR m.origin_locator LIKE 'https://%')
            ORDER BY am.media_asset_id LIMIT 1
        """, (alternative["alternative_id"],)).fetchone()
        alternative_details.append({
            "alternative": alternative,
            "occurrences": occurrences,
            "visual_url": visual["origin_locator"] if visual else None
        })

    conexion.close()

    return render_template(
        "nueva_ocurrencia.html",
        fuentes=fuentes,
        conceptos=conceptos,
        alternativas=alternativas,
        alternative_details=alternative_details
    )


@submissions_bp.route("/aportes", methods=["POST"])
def guardar_aporte():

    source_id = request.form.get("source_id", "")
    concept_choice = request.form.get("concept_choice", "")
    proposed_concept_id = (
        concept_choice if concept_choice.isdigit() else None
    )
    proposed_concept_label = request.form.get(
        "proposed_concept_label", ""
    ).strip() or None
    proposed_concept_note = request.form.get(
        "proposed_concept_note", ""
    ).strip() or None
    proposed_alternative_id = request.form.get("proposed_alternative_id") or None
    proposed_alternative_label = request.form.get(
        "proposed_alternative_label", ""
    ).strip() or None
    proposal_type = request.form.get("proposal_type", "not_sure")
    original_gloss = request.form.get("original_gloss", "").strip()
    hyperlink = request.form.get("hyperlink", "").strip()
    occurrence_year = request.form.get("occurrence_year", "").strip() or None
    proposed_concept_status = request.form.get("proposed_concept_status") or None
    concept_uncertainty_note = request.form.get("concept_uncertainty_note", "").strip() or None
    proposed_relation_answer = request.form.get("proposed_relation_answer") or None
    proposed_related_alternative_id = request.form.get("proposed_related_alternative_id") or None
    proposed_phonological_parameter = request.form.get("proposed_phonological_parameter", "").strip() or None
    alternative_uncertainty_note = request.form.get("alternative_uncertainty_note", "").strip() or None

    if not source_id or not concept_choice:

        return (
            "La fuente es obligatoria.",
            400
        )

    conexion = conectar()

    try:

        conexion.execute("BEGIN IMMEDIATE")

        if source_id == "__new__":
            source_values = source_insert_values(request.form)
            conexion.execute("""
                INSERT INTO source (
                    source_name, source_type, source_reference,
                    start_year, end_year, end_year_status, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (*source_values, None))
            source_id = str(conexion.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0])
        elif not source_id.isdigit():
            raise ValueError

        from routes.occurrences import validate_occurrence_year
        occurrence_year = validate_occurrence_year(
            conexion, source_id, occurrence_year
        )
        if concept_choice == "not_sure":
            proposed_concept_id = None
            proposed_concept_status = "not_sure"
            if not concept_uncertainty_note:
                raise ValueError
        elif concept_choice == "new":
            proposed_concept_id = None
            proposed_concept_status = "new"
            if not proposed_concept_label or not proposed_concept_note:
                raise ValueError
        else:
            proposed_concept_status = "selected"
        if proposal_type == "existing_alternative":
            if proposed_alternative_id is None:
                raise ValueError
            alternative = conexion.execute(
                "SELECT concept_id FROM alternative WHERE alternative_id = ?",
                (proposed_alternative_id,)
            ).fetchone()
            if alternative is None or int(alternative["concept_id"]) != int(proposed_concept_id):
                raise ValueError
        elif proposal_type == "new_alternative" and proposed_relation_answer == "yes":
            if proposed_related_alternative_id is None or not proposed_phonological_parameter:
                raise ValueError
            related = conexion.execute(
                "SELECT concept_id FROM alternative WHERE alternative_id = ?",
                (proposed_related_alternative_id,)
            ).fetchone()
            if related is None or int(related["concept_id"]) != int(proposed_concept_id):
                raise ValueError

        cursor = conexion.execute("""
            INSERT INTO occurrence (source_id, original_gloss, hyperlink, occurrence_year)
            VALUES (?, ?, ?, ?)
        """, (
            source_id,
            original_gloss,
            hyperlink,
            occurrence_year
        ))

        conexion.execute("""
            INSERT INTO submission (
                occurrence_id, proposed_concept_id, proposed_alternative_id,
                proposed_concept_label, proposed_concept_note,
                proposed_alternative_label, proposed_concept_status,
                concept_uncertainty_note, proposed_relation_answer,
                proposed_related_alternative_id, proposed_phonological_parameter,
                alternative_uncertainty_note, proposal_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cursor.lastrowid,
            proposed_concept_id,
            proposed_alternative_id,
            proposed_concept_label,
            proposed_concept_note,
            proposed_alternative_label,
            proposed_concept_status,
            concept_uncertainty_note,
            proposed_relation_answer,
            proposed_related_alternative_id,
            proposed_phonological_parameter,
            alternative_uncertainty_note,
            proposal_type
        ))

        conexion.commit()

    except (sqlite3.IntegrityError, ValueError):

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
            proposed_alternative.working_label AS proposed_alternative_working_label,
            os.proposed_alternative_label,
            os.proposed_concept_status,
            os.proposed_concept_label,
            os.proposed_concept_note,
            os.concept_uncertainty_note,
            os.proposed_relation_answer,
            os.proposed_related_alternative_id,
            os.proposed_phonological_parameter,
            os.alternative_uncertainty_note,
            os.proposal_type,
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
        LEFT JOIN alternative AS proposed_alternative
            ON proposed_alternative.alternative_id = os.proposed_alternative_id
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
            proposed_alternative.working_label AS proposed_alternative_working_label,
            os.proposed_alternative_label,
            os.proposed_concept_status,
            os.proposed_concept_label,
            os.proposed_concept_note,
            os.concept_uncertainty_note,
            os.proposed_relation_answer,
            os.proposed_related_alternative_id,
            related_alternative.working_label AS proposed_related_alternative_working_label,
            os.proposed_phonological_parameter,
            os.alternative_uncertainty_note,
            os.proposal_type,
            s.source_name,
            o.original_gloss,
            o.hyperlink,
            os.submitted_at
        FROM submission AS os
        JOIN occurrence AS o ON o.occurrence_id = os.occurrence_id
        LEFT JOIN concept AS c ON os.proposed_concept_id = c.concept_id
        LEFT JOIN alternative AS proposed_alternative
            ON proposed_alternative.alternative_id = os.proposed_alternative_id
        LEFT JOIN alternative AS related_alternative
            ON related_alternative.alternative_id = os.proposed_related_alternative_id
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
    relation_answer = request.form.get("relation_answer") or None
    related_alternative_id = request.form.get("related_alternative_id") or None
    phonological_parameter = request.form.get("phonological_parameter", "").strip() or None

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

            if concept_id == "proposed":
                proposed = conexion.execute("""
                    SELECT proposed_concept_label, proposed_concept_note
                    FROM submission WHERE submission_id = ?
                """, (submission_id,)).fetchone()
                if (proposed is None or not proposed["proposed_concept_label"]
                        or not proposed["proposed_concept_note"]):
                    return "No hay un concepto nuevo propuesto completo.", 400
                concept_cursor = conexion.execute("""
                    INSERT INTO concept (preferred_label)
                    VALUES (?)
                """, (proposed["proposed_concept_label"],))
                concept_id = str(concept_cursor.lastrowid)

            if conexion.execute(
                "SELECT 1 FROM concept WHERE concept_id = ?", (concept_id,)
            ).fetchone() is None:
                return "El concepto no existe.", 400

            if relation_answer == "yes":
                if related_alternative_id is None or not phonological_parameter:
                    return "La relación fonológica requiere alternativa y parámetro.", 400
                related = conexion.execute("""
                    SELECT concept_id FROM alternative WHERE alternative_id = ?
                """, (related_alternative_id,)).fetchone()
                if related is None or int(related["concept_id"]) != int(concept_id):
                    return "La alternativa relacionada debe pertenecer al concepto elegido.", 400
            elif relation_answer not in (None, "no", "not_sure"):
                return "La respuesta de relación no es válida.", 400

            working_label = generated_working_label(
                conexion,
                int(concept_id),
                int(related_alternative_id) if relation_answer == "yes" else None
            )
            cursor = conexion.execute("""
                INSERT INTO alternative (concept_id, working_label)
                VALUES (?, ?)
            """, (concept_id, working_label))
            if relation_answer == "yes":
                a_id, b_id = sorted((int(related_alternative_id), cursor.lastrowid))
                conexion.execute("""
                    INSERT INTO alternative_relation (
                        alternative_a_id, alternative_b_id, phonological_parameter
                    ) VALUES (?, ?, ?)
                """, (a_id, b_id, phonological_parameter))
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

    except (sqlite3.Error, ValueError):

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
