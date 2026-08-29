from flask import Blueprint, render_template, request, redirect, url_for

import sqlite3

from database import conectar
from concept_labels import InvalidConceptLabel, normalize_concept_label
from routes.alternatives import generated_working_label
from routes.sources import source_insert_values


submissions_bp = Blueprint("submissions", __name__)


def table_has_column(conexion, table_name, column_name):
    columns = conexion.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in columns)


def related_submission_options(conexion, concept_id=None):
    if concept_id is None:
        return []
    if not table_has_column(conexion, "submission", "proposed_related_submission_id"):
        return []
    query = """
        SELECT
            s.submission_id,
            s.proposed_concept_id,
            s.proposal_type,
            s.status,
            s.submitted_at,
            o.original_gloss,
            src.source_name
        FROM submission AS s
        JOIN occurrence AS o ON o.occurrence_id = s.occurrence_id
        JOIN source AS src ON src.source_id = o.source_id
        WHERE s.status = 'pending'
          AND s.proposal_type = 'new_alternative'
    """
    params = []
    if concept_id is not None:
        query += " AND s.proposed_concept_id = ?"
        params.append(concept_id)
    query += " ORDER BY s.submission_id DESC"
    return conexion.execute(query, tuple(params)).fetchall()


def submission_accepts_proposed(conexion, submission):
    if submission is None:
        return False

    proposal_type = submission["proposal_type"]
    proposed_alternative_id = submission["proposed_alternative_id"]
    proposed_related_alternative_id = submission["proposed_related_alternative_id"]
    proposed_related_submission_id = submission["proposed_related_submission_id"] if table_has_column(conexion, "submission", "proposed_related_submission_id") else None
    relation_answer = submission["proposed_relation_answer"]
    phonological_parameter = submission["proposed_phonological_parameter"]
    proposed_concept_id = submission["proposed_concept_id"]

    if proposal_type == "existing_alternative":
        return bool(proposed_alternative_id) and proposed_concept_id is not None

    if proposal_type == "new_alternative":
        if proposed_concept_id is None:
            return False
        if relation_answer == "no":
            return True
        if relation_answer == "yes":
            if proposed_related_alternative_id is not None:
                if not phonological_parameter:
                    return False
                related = conexion.execute(
                    "SELECT concept_id FROM alternative WHERE alternative_id = ?",
                    (proposed_related_alternative_id,)
                ).fetchone()
                if related is None:
                    return False
                return int(related["concept_id"]) == int(proposed_concept_id)
            if proposed_related_submission_id is not None:
                related = conexion.execute(
                    """
                    SELECT s.status, s.proposed_concept_id, a.alternative_id
                    FROM submission AS s
                    LEFT JOIN assignment AS a ON a.occurrence_id = s.occurrence_id AND a.is_current = 1
                    WHERE s.submission_id = ?
                    """,
                    (proposed_related_submission_id,)
                ).fetchone()
                if related is None:
                    return False
                if related["status"] != "accepted":
                    return False
                if related["alternative_id"] is None:
                    return False
                return True
            return False

    return False


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
        SELECT a.alternative_id, a.concept_id, a.working_label,
               a.original_code, c.preferred_label
        FROM alternative AS a
        JOIN concept AS c ON c.concept_id = a.concept_id
        ORDER BY a.alternative_id
    """).fetchall()

    alternative_details = []
    for alternative in alternativas:
        occurrences = conexion.execute("""
            SELECT s.source_name, o.occurrence_year, s.start_year,
                   s.end_year, s.end_year_status, o.original_gloss,
                   o.source_locator, o.hyperlink
            FROM assignment AS a
            JOIN occurrence AS o ON o.occurrence_id = a.occurrence_id
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

    related_pending_submissions = conexion.execute("""
        SELECT
            s.submission_id,
            s.proposed_concept_id,
            s.proposed_concept_label,
            s.status,
            s.submitted_at,
            o.original_gloss,
            src.source_name,
            s.proposal_type
        FROM submission AS s
        JOIN occurrence AS o ON o.occurrence_id = s.occurrence_id
        JOIN source AS src ON src.source_id = o.source_id
        WHERE s.status = 'pending'
          AND s.proposal_type = 'new_alternative'
        ORDER BY s.submission_id DESC
    """).fetchall()

    conexion.close()

    return render_template(
        "nueva_ocurrencia.html",
        fuentes=fuentes,
        conceptos=conceptos,
        alternativas=alternativas,
        alternative_details=alternative_details,
        related_pending_submissions=related_pending_submissions
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
    relation_target_type = request.form.get("relation_target_type") or None
    proposed_related_alternative_id = request.form.get("proposed_related_alternative_id") or None
    proposed_related_submission_id = request.form.get("proposed_related_submission_id") or None
    proposed_phonological_parameter = request.form.get("proposed_phonological_parameter", "").strip() or None
    alternative_uncertainty_note = request.form.get("alternative_uncertainty_note", "").strip() or None

    if proposed_related_alternative_id and proposed_related_submission_id:
        return "La relación no puede apuntar a una alternativa y a un aporte pendiente simultáneamente.", 400

    if relation_target_type == "submission":
        proposed_related_alternative_id = None
    elif relation_target_type == "alternative":
        proposed_related_submission_id = None
    elif proposed_relation_answer != "yes":
        proposed_related_alternative_id = None
        proposed_related_submission_id = None

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
            if not proposed_concept_label:
                raise ValueError
            normalize_concept_label(proposed_concept_label)
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
            if relation_target_type == "submission":
                if proposed_related_submission_id is None or not proposed_phonological_parameter:
                    raise ValueError
                related_submission = conexion.execute(
                    "SELECT proposed_concept_id, status FROM submission WHERE submission_id = ?",
                    (proposed_related_submission_id,)
                ).fetchone()
                if related_submission is None:
                    raise ValueError
                if related_submission["proposed_concept_id"] is not None and int(related_submission["proposed_concept_id"]) != int(proposed_concept_id):
                    raise ValueError
            else:
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

        columns = [
            "occurrence_id", "proposed_concept_id", "proposed_alternative_id",
            "proposed_concept_label", "proposed_concept_note",
            "proposed_alternative_label", "proposed_concept_status",
            "concept_uncertainty_note", "proposed_relation_answer",
            "proposed_related_alternative_id", "proposed_phonological_parameter",
            "alternative_uncertainty_note", "proposal_type"
        ]
        values = [
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
        ]
        if table_has_column(conexion, "submission", "proposed_related_submission_id"):
            columns.append("proposed_related_submission_id")
            values.append(proposed_related_submission_id)

        placeholders = ", ".join(["?"] * len(values))
        insert_sql = f"INSERT INTO submission ({', '.join(columns)}) VALUES ({placeholders})"
        conexion.execute(insert_sql, tuple(values))

        conexion.commit()

    except InvalidConceptLabel as error:

        conexion.rollback()

        return str(error), 400

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

    select_columns = [
        "os.submission_id",
        "os.proposed_concept_id",
        "c.preferred_label",
        "os.proposed_alternative_id",
        "proposed_alternative.working_label AS proposed_alternative_working_label",
        "os.proposed_alternative_label",
        "os.proposed_concept_status",
        "os.proposed_concept_label",
        "os.proposed_concept_note",
        "os.concept_uncertainty_note",
        "os.proposed_relation_answer",
        "os.proposed_related_alternative_id",
        "related_alternative.working_label AS proposed_related_alternative_working_label",
        "os.proposed_phonological_parameter",
        "os.alternative_uncertainty_note",
        "os.proposal_type",
        "s.source_name",
        "o.original_gloss",
        "o.hyperlink",
        "os.submitted_at"
    ]
    if table_has_column(conexion, "submission", "proposed_related_submission_id"):
        select_columns.extend([
            "os.proposed_related_submission_id",
            "related_submission.status AS related_submission_status",
            "related_submission.proposal_type AS related_submission_proposal_type",
            "related_submission.occurrence_id AS related_submission_occurrence_id",
            "related_occurrence.original_gloss AS related_submission_original_gloss",
            "related_source.source_name AS related_submission_source_name",
            "related_submission.submitted_at AS related_submission_submitted_at",
            "related_assignment.alternative_id AS related_submission_current_alternative_id",
            "related_current_alternative.working_label AS related_submission_current_working_label",
            "related_current_concept.preferred_label AS related_submission_current_concept_label"
        ])

    query = f"""
        SELECT
            {', '.join(select_columns)}
        FROM submission AS os
        JOIN occurrence AS o ON o.occurrence_id = os.occurrence_id
        LEFT JOIN concept AS c ON os.proposed_concept_id = c.concept_id
        LEFT JOIN alternative AS proposed_alternative
            ON proposed_alternative.alternative_id = os.proposed_alternative_id
        LEFT JOIN alternative AS related_alternative
            ON related_alternative.alternative_id = os.proposed_related_alternative_id
        LEFT JOIN submission AS related_submission
            ON related_submission.submission_id = os.proposed_related_submission_id
        LEFT JOIN occurrence AS related_occurrence
            ON related_occurrence.occurrence_id = related_submission.occurrence_id
        LEFT JOIN source AS related_source
            ON related_source.source_id = related_occurrence.source_id
        LEFT JOIN assignment AS related_assignment
            ON related_assignment.occurrence_id = related_submission.occurrence_id
            AND related_assignment.is_current = 1
        LEFT JOIN alternative AS related_current_alternative
            ON related_current_alternative.alternative_id = related_assignment.alternative_id
        LEFT JOIN concept AS related_current_concept
            ON related_current_concept.concept_id = related_current_alternative.concept_id
        JOIN source AS s ON o.source_id = s.source_id
        WHERE os.status = 'pending'
        ORDER BY os.submission_id
    """
    aportes = [dict(aporte) for aporte in conexion.execute(query).fetchall()]

    for aporte in aportes:
        aporte["accept_proposed_allowed"] = submission_accepts_proposed(conexion, aporte)
        aporte["proposed_concept_preview"] = None
        aporte["proposed_concept_preview_valid"] = True
        if aporte["proposed_concept_label"]:
            try:
                aporte["proposed_concept_preview"] = normalize_concept_label(
                    aporte["proposed_concept_label"]
                )
            except InvalidConceptLabel:
                aporte["proposed_concept_preview_valid"] = False

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
    related_submission_id = request.form.get("related_submission_id") or None

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

        if decision == "accept_proposed":
            proposal = conexion.execute("""
                SELECT
                    proposal_type,
                    proposed_alternative_id,
                    proposed_related_alternative_id,
                    proposed_related_submission_id,
                    proposed_relation_answer,
                    proposed_phonological_parameter,
                    proposed_concept_id,
                    proposed_concept_status,
                    proposed_concept_label,
                    proposed_concept_note,
                    status
                FROM submission
                WHERE submission_id = ?
            """, (submission_id,)).fetchone()
            if proposal is None:
                return "El aporte no existe.", 404
            if not submission_accepts_proposed(conexion, proposal):
                return "La clasificación propuesta no está suficientemente determinada para aceptarla automáticamente.", 400

            if proposal["proposal_type"] == "existing_alternative":
                if proposal["proposed_alternative_id"] is None:
                    return "La alternativa propuesta no es válida.", 400
                crear_o_reemplazar_assignment(
                    conexion,
                    aporte["occurrence_id"],
                    int(proposal["proposed_alternative_id"])
                )

            elif proposal["proposal_type"] == "new_alternative":
                if proposal["proposed_relation_answer"] == "yes":
                    if proposal["proposed_related_submission_id"] is not None:
                        related = conexion.execute("""
                            SELECT a.alternative_id
                            FROM assignment AS a
                            WHERE a.occurrence_id = (
                                SELECT occurrence_id
                                FROM submission
                                WHERE submission_id = ?
                            )
                              AND a.is_current = 1
                        """, (proposal["proposed_related_submission_id"],)).fetchone()
                        if related is None:
                            return "El aporte relacionado no tiene una clasificación vigente que pueda usarse.", 400
                        related_alternative_id = related["alternative_id"]
                    else:
                        related_alternative_id = proposal["proposed_related_alternative_id"]

                    concept_id = proposal["proposed_concept_id"]
                    if concept_id is None or concept_id == "":
                        return "El concepto propuesto no es válido.", 400
                    working_label = generated_working_label(
                        conexion,
                        int(concept_id),
                        int(related_alternative_id)
                    )
                    new_alt = conexion.execute("""
                        INSERT INTO alternative (concept_id, working_label)
                        VALUES (?, ?)
                    """, (concept_id, working_label))
                    a_id, b_id = sorted((int(related_alternative_id), new_alt.lastrowid))
                    conexion.execute("""
                        INSERT INTO alternative_relation (
                            alternative_a_id, alternative_b_id, phonological_parameter
                        ) VALUES (?, ?, ?)
                    """, (a_id, b_id, proposal["proposed_phonological_parameter"]))
                    crear_o_reemplazar_assignment(conexion, aporte["occurrence_id"], new_alt.lastrowid)
                else:
                    concept_id = proposal["proposed_concept_id"]
                    if concept_id is None or concept_id == "":
                        return "El concepto propuesto no es válido.", 400
                    working_label = generated_working_label(conexion, int(concept_id), None)
                    new_alt = conexion.execute("""
                        INSERT INTO alternative (concept_id, working_label)
                        VALUES (?, ?)
                    """, (concept_id, working_label))
                    crear_o_reemplazar_assignment(conexion, aporte["occurrence_id"], new_alt.lastrowid)

        elif decision == "assign_existing":
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
                if proposed is None or not proposed["proposed_concept_label"]:
                    return "No hay un concepto nuevo propuesto completo.", 400
                try:
                    preferred_label = normalize_concept_label(
                        proposed["proposed_concept_label"]
                    )
                    concept_cursor = conexion.execute("""
                        INSERT INTO concept (preferred_label)
                        VALUES (?)
                    """, (preferred_label,))
                except InvalidConceptLabel as error:
                    return str(error), 400
                except sqlite3.IntegrityError:
                    return (
                        "Ya existe un concepto con la etiqueta canónica propuesta.",
                        400
                    )
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

        elif decision not in ("accept_unclassified", "accept_proposed", "reject"):
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
