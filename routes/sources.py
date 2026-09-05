from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

import sqlite3

from database import conectar
from flask import g, abort
from activity import record_activity
from source_forms import source_form_values, source_insert_values, parse_source_years
from source_structural import year_conflicts
from source_details import SOURCE_TYPES, analysts_may_create_sources


from routes.source_retirement import source_retirement_bp

sources_bp = Blueprint("sources", __name__)
sources_bp.register_blueprint(source_retirement_bp)


def source_edit_allowed(connection, source):
    role = getattr(g, "current_access_role", None)
    return source is not None and source["retired_at"] is None and (
        role in ("reviewer", "master") or
        (role == "analyst" and not source["analyst_protected"]
         and analysts_may_create_sources(connection))
    )


@sources_bp.route("/fuentes/<int:source_id>/proteccion", methods=["GET", "POST"])
def source_protection(source_id):
    role = getattr(g, "current_access_role", None)
    if role not in ("reviewer", "master"):
        abort(404)
    db = conectar()
    try:
        if request.method == "POST":
            db.execute("BEGIN IMMEDIATE")
        source = db.execute("SELECT * FROM source WHERE source_id=? AND retired_at IS NULL", (source_id,)).fetchone()
        if source is None:
            abort(404)
        if request.method == "GET":
            return render_template("source_protection.html", fuente=source)
        value = request.form.get("protected")
        if value not in ("0", "1") or (value == "0" and request.form.get("confirm") != "1"):
            abort(400)
        protected = int(value)
        if protected != source["analyst_protected"]:
            fields = ("source_id", "source_name", "source_type", "source_reference",
                      "legacy_source_code", "source_scope", "format_original", "format_detail",
                      "start_year", "end_year", "end_year_status", "region_description",
                      "characterization", "reported_entry_count", "analyst_protected")
            note = "Fuente protegida" if protected else "Fuente desprotegida"
            db.execute(f"INSERT INTO source_revision ({','.join(fields)},change_note) VALUES ({','.join('?' for _ in fields)},?)",
                       (*[source[field] for field in fields], note))
            db.execute("UPDATE source SET analyst_protected=?,updated_at=CURRENT_TIMESTAMP WHERE source_id=?", (protected, source_id))
            record_activity(db, "source_protected" if protected else "source_unprotected",
                            entity_type="source", entity_id=source_id, access_role=role,
                            collaborator_id=request.form.get("collaborator_id"))
        db.commit()
    finally:
        db.close()
    return redirect(url_for("sources.fuentes"))


@sources_bp.route("/fuentes")
def fuentes():

    conexion = conectar()
    fuentes = conexion.execute("""
        SELECT source_id, source_name, legacy_source_code, source_scope,
               source_type, source_reference, format_original, format_detail, start_year, end_year,
               end_year_status, region_description, characterization,
               reported_entry_count, analyst_protected, retired_at
        FROM source WHERE retired_at IS NULL ORDER BY source_name
    """).fetchall()
    can_create=getattr(g,"current_access_role",None) in ("reviewer","master") or analysts_may_create_sources(conexion)
    conexion.close()
    return render_template("fuentes.html", fuentes=fuentes, can_create=can_create, source_types=SOURCE_TYPES)


@sources_bp.route("/fuentes/nueva", methods=["POST"])
def nueva_fuente():
    role=getattr(g,"current_access_role",None)
    conexion=conectar()
    if role=="analyst" and not analysts_may_create_sources(conexion):
        conexion.close(); abort(404)
    try:
        values = source_form_values(request.form)
    except ValueError:
        conexion.close()
        return "Los metadatos de la fuente no son válidos.", 400
    if not values[0]:
        conexion.close()
        return "El nombre de la fuente es obligatorio.", 400

    try:
        duplicate=conexion.execute("SELECT source_id FROM source WHERE lower(trim(source_name))=lower(trim(?))",(values[0],)).fetchone()
        if duplicate:return "Ya existe una fuente con ese nombre.",400
        conexion.execute("""
            INSERT INTO source (
                source_name, source_type, source_reference, legacy_source_code, source_scope,
                format_original, format_detail, start_year, end_year,
                end_year_status, region_description, characterization,
                reported_entry_count, created_by, analyst_protected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (*values, None, int(role != "analyst")))
        source_id=conexion.execute("SELECT last_insert_rowid()").fetchone()[0]
        if role: record_activity(conexion,"source_created",entity_type="source",entity_id=source_id,collaborator_id=request.form.get("collaborator_id"),access_role=role)
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "Ya existe una fuente con ese nombre.", 400
    finally:
        conexion.close()
    return redirect(url_for("sources.fuentes"))


@sources_bp.route("/fuentes/<int:source_id>/editar")
def editar_fuente(source_id):

    conexion = conectar()
    fuente = conexion.execute("""
        SELECT source_id, source_name, source_type, source_reference, legacy_source_code, source_scope,
               format_original, format_detail, start_year, end_year,
               end_year_status, region_description, characterization,
               reported_entry_count, analyst_protected, retired_at
        FROM source WHERE source_id = ?
    """, (source_id,)).fetchone()
    occurrence_count = conexion.execute("SELECT count(*) FROM occurrence WHERE source_id=?",(source_id,)).fetchone()[0]
    allowed = source_edit_allowed(conexion, fuente)
    conexion.close()
    if not allowed: abort(404)
    if fuente is None:
        return "La fuente no existe.", 404
    return render_template("editar_fuente.html", fuente=fuente, source_types=SOURCE_TYPES, occurrence_count=occurrence_count)


@sources_bp.route("/fuentes/<int:source_id>/actualizar", methods=["POST"])
def actualizar_fuente(source_id):

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        actual = conexion.execute("""
            SELECT source_name, source_type, source_reference,
                   legacy_source_code, source_scope, format_original,
                   format_detail, start_year, end_year, end_year_status,
                   region_description, characterization, reported_entry_count, analyst_protected, retired_at
            FROM source WHERE source_id = ?
        """, (source_id,)).fetchone()
        if not source_edit_allowed(conexion, actual):
            abort(404)
        if actual is None:
            conexion.rollback()
            return "La fuente no existe.", 404
        try:
            values = source_form_values(request.form)
        except ValueError:
            return "Los metadatos de la fuente no son válidos.", 400
        if not values[0]:
            return "El nombre de la fuente es obligatorio.", 400

        conflicts = year_conflicts(conexion.execute("SELECT occurrence_id,occurrence_year FROM occurrence WHERE source_id=?", (source_id,)).fetchall(), {"start_year": values[7], "end_year": values[8]})
        if conflicts and (actual["start_year"], actual["end_year"], actual["end_year_status"]) != values[7:10]:
            return render_template("source_period_conflicts.html", source_id=source_id, conflicts=conflicts), 400
        previous_editable_state = (
            actual["source_name"], actual["source_type"], actual["source_reference"], actual["legacy_source_code"],
            actual["source_scope"], actual["format_original"],
            actual["format_detail"], actual["start_year"],
            actual["end_year"], actual["end_year_status"],
            actual["region_description"], actual["characterization"],
            actual["reported_entry_count"],
        )
        if previous_editable_state != values:
            conexion.execute("""
                INSERT INTO source_revision (
                    source_id, source_name, source_type, source_reference,
                    legacy_source_code, source_scope, format_original,
                    format_detail, start_year, end_year, end_year_status,
                    region_description, characterization,
                    reported_entry_count, change_note, analyst_protected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id, actual["source_name"], actual["source_type"],
                actual["source_reference"], actual["legacy_source_code"],
                actual["source_scope"], actual["format_original"],
                actual["format_detail"], actual["start_year"],
                actual["end_year"], actual["end_year_status"],
                actual["region_description"], actual["characterization"],
                actual["reported_entry_count"],
                request.form.get("change_note") or None, actual["analyst_protected"],
            ))
        conexion.execute("""
            UPDATE source SET
                source_name = ?, source_type = ?, source_reference = ?, legacy_source_code = ?, source_scope = ?,
                format_original = ?, format_detail = ?, start_year = ?,
                end_year = ?, end_year_status = ?, region_description = ?,
                characterization = ?, reported_entry_count = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE source_id = ?
        """, (*values, source_id))
        if previous_editable_state != values and getattr(g,"current_access_role",None):
            record_activity(conexion,"source_updated",entity_type="source",entity_id=source_id,collaborator_id=request.form.get("collaborator_id"),access_role=getattr(g,"current_access_role",None),comment=request.form.get("change_note"))
        conexion.commit()
    except (sqlite3.IntegrityError, ValueError):
        conexion.rollback()
        return "Los metadatos o el tipo de la fuente no son válidos.", 400
    finally:
        conexion.close()
    return redirect(url_for("sources.fuentes"))
