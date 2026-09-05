from edit_concurrency import edit_token, check_edit, StaleEdit
from flask import (
    Blueprint, g,
    render_template,
    request,
    redirect,
    url_for
)

import sqlite3
import json
from access_control import requires_reviewer
from activity import record_activity

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
@requires_reviewer
def nuevo_concepto():

    try:
        preferred_label = normalize_concept_label(
            request.form.get("preferred_label", "")
        )
    except InvalidConceptLabel as error:
        return str(error), 400

    conexion = conectar()

    try:

        cursor = conexion.execute("""
            INSERT INTO concept
            (preferred_label)

            VALUES (?)
        """, (preferred_label,))

        record_activity(conexion, "concept_created", entity_type="concept",
                        entity_id=cursor.lastrowid, access_role=g.current_access_role,
                        collaborator_id=request.form.get("collaborator_id"),
                        comment=json.dumps({"old_label": None, "new_label": preferred_label}, ensure_ascii=False))
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
@requires_reviewer
def editar_concepto(concept_id):

    conexion = conectar()
    conexion.execute("BEGIN")

    concepto = conexion.execute("""
        SELECT
            concept_id,
            preferred_label

        FROM concept

        WHERE concept_id = ?
    """, (concept_id,)).fetchone()

    token = edit_token(conexion, "concept", concept_id)
    conexion.close()

    if concepto is None:

        return (
            "El concepto no existe.",
            404
        )

    return render_template(
        "editar_concepto.html",
        concepto=concepto, edit_token=token
    )


# =========================================================
# ACTUALIZAR CONCEPTO
# =========================================================

@concepts_bp.route(
    "/conceptos/<int:concept_id>/actualizar",
    methods=["POST"]
)
@requires_reviewer
def actualizar_concepto(concept_id):

    try:
        preferred_label = normalize_concept_label(
            request.form.get("preferred_label", "")
        )
    except InvalidConceptLabel as error:
        return str(error), 400

    conexion = conectar()

    try:

        conexion.execute("BEGIN IMMEDIATE")
        previous = conexion.execute("SELECT preferred_label FROM concept WHERE concept_id=?", (concept_id,)).fetchone()
        if previous is None:
            conexion.close()
            return "El concepto no existe.", 404
        check_edit(conexion, "concept", concept_id, request.form.get("edit_token"))
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

        if previous[0] != preferred_label:
            record_activity(conexion, "concept_renamed", entity_type="concept",
                            entity_id=concept_id, access_role=g.current_access_role,
                            collaborator_id=request.form.get("collaborator_id"),
                            comment=json.dumps({"old_label": previous[0], "new_label": preferred_label}, ensure_ascii=False))
        conexion.commit()

    except StaleEdit as error:
        conexion.rollback()
        conexion.close()
        return str(error), 409
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
