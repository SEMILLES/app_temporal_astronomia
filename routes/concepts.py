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
from access_control import requires_reviewer, requires_master
from concept_classification import apply_metadata, parse_form, editor_context, administer, catalog_state
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

    sort = request.args.get("sort", "id_asc")
    orders = {"id_desc": "concept_id DESC", "id_asc": "concept_id ASC", "az": "preferred_label, concept_id"}
    if sort not in orders:
        sort = "id_asc"
    conceptos = conexion.execute(f"""
        SELECT
            concept_id,
            preferred_label,
            EXISTS(SELECT 1 FROM alternative a WHERE a.concept_id=concept.concept_id
                   AND a.retired_at IS NULL) AS has_active_alternatives

        FROM concept

        ORDER BY {orders[sort]}
    """).fetchall()

    metadata = editor_context(conexion)

    conexion.close()

    return render_template(
        "conceptos.html",
        conceptos=[c for c in conceptos if c['has_active_alternatives']],
        empty_concepts=[c for c in conceptos if not c['has_active_alternatives']], sort=sort, metadata=metadata
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

        apply_metadata(conexion, cursor.lastrowid, parse_form(request.form),
                       access_role=g.current_access_role, collaborator_id=request.form.get('collaborator_id'))

        record_activity(conexion, "concept_created", entity_type="concept",
                        entity_id=cursor.lastrowid, access_role=g.current_access_role,
                        collaborator_id=request.form.get("collaborator_id"),
                        comment=json.dumps({"old_label": None, "new_label": preferred_label}, ensure_ascii=False))
        conexion.commit()

    except sqlite3.IntegrityError:
        conexion.rollback()
        conexion.close()
        return 'No se pudo guardar: etiqueta duplicada o clasificación incompatible.', 400
    except ValueError as error:
        conexion.rollback()
        conexion.close()
        return str(error), 400

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
    metadata = editor_context(conexion, concept_id)
    conexion.close()

    if concepto is None:

        return (
            "El concepto no existe.",
            404
        )

    return render_template(
        "editar_concepto.html",
        concepto=concepto, edit_token=token, metadata=metadata
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
        apply_metadata(conexion, concept_id, parse_form(request.form),
                       access_role=g.current_access_role, collaborator_id=request.form.get('collaborator_id'))
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
        conexion.rollback()
        conexion.close()
        return 'No se pudo guardar: etiqueta duplicada o clasificación incompatible.', 400
    except ValueError as error:
        conexion.rollback()
        conexion.close()
        return str(error), 400

    conexion.close()

    return redirect(
        url_for("concepts.conceptos")
    )


@concepts_bp.get('/conceptos/<int:concept_id>/clasificaciones')
def clasificaciones_concepto(concept_id):
    db = conectar()
    try:
        concept = db.execute('SELECT * FROM concept WHERE concept_id=?',(concept_id,)).fetchone()
        if concept is None:
            return 'El Concept no existe.',404
        return render_template('clasificaciones_concepto.html', concepto=concept,
                               metadata=editor_context(db,concept_id))
    finally:
        db.close()


@concepts_bp.route('/administracion/clasificaciones', methods=['GET','POST'])
@requires_master
def administrar_clasificaciones():
    db = conectar()
    try:
        if request.method == 'POST':
            db.execute('BEGIN IMMEDIATE')
            check_edit(db,'classification_catalog',0,request.form.get('edit_token'))
            administer(db,request.form.get('kind'),identifier=request.form.get('identifier') or None,
                       code=request.form.get('code'),name=request.form.get('name'),
                       active=request.form.get('active','1'),parent_id=request.form.get('parent_id'),
                       collaborator_id=request.form.get('collaborator_id'),access_role=g.current_access_role)
            db.commit()
            return redirect(url_for('concepts.administrar_clasificaciones'))
        db.execute('BEGIN')
        return render_template('administrar_clasificaciones.html', catalogs=catalog_state(db),
                               edit_token=edit_token(db,'classification_catalog',0))
    except StaleEdit as error:
        db.rollback()
        return str(error),409
    except sqlite3.IntegrityError:
        db.rollback()
        return 'Código o nombre repetido, o ámbito incompatible.',400
    except ValueError as error:
        db.rollback()
        return str(error),400
    finally:
        db.close()
