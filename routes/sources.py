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
from source_details import SOURCE_TYPES, analysts_may_create_sources


sources_bp = Blueprint("sources", __name__)


def parse_source_years(form):

    def parse(name):
        value = form.get(name, "").strip()
        if not value:
            return None
        if not value.isdigit() or len(value) != 4:
            raise ValueError
        return int(value)

    start_year = parse("start_year")
    end_year = parse("end_year")
    status = form.get("end_year_status", "").strip() or None
    if status not in (None, "known", "ongoing", "unknown"):
        raise ValueError
    if (start_year is not None or end_year is not None) and status is None:
        raise ValueError
    if status == "known" and end_year is None:
        raise ValueError
    if status in ("ongoing", "unknown") and end_year is not None:
        raise ValueError
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError
    return start_year, end_year, status


def source_form_values(form):
    start_year, end_year, status = parse_source_years(form)
    source_scope = form.get("source_scope", "").strip() or None
    if source_scope not in (None, "INSTITUTIONAL", "PERSONAL"):
        raise ValueError
    reported_entry_count_value = form.get("reported_entry_count", "").strip()
    if reported_entry_count_value:
        if not reported_entry_count_value.isdigit():
            raise ValueError
        reported_entry_count = int(reported_entry_count_value)
    else:
        reported_entry_count = None
    source_type=form.get("source_type", "").strip()
    if source_type not in SOURCE_TYPES: raise ValueError
    return (
        form.get("source_name", "").strip(), source_type,
        form.get("source_reference", "").strip() or None,
        form.get("legacy_source_code", "").strip() or None,
        source_scope,
        form.get("format_original", "").strip() or None,
        form.get("format_detail", "").strip() or None,
        start_year,
        end_year,
        status,
        form.get("region_description", "").strip() or None,
        form.get("characterization", "").strip() or None,
        reported_entry_count,
    )


def source_insert_values(form):
    values = source_form_values(form)
    if not values[0]:
        raise ValueError
    return values


@sources_bp.route("/fuentes")
def fuentes():

    conexion = conectar()
    fuentes = conexion.execute("""
        SELECT source_id, source_name, legacy_source_code, source_scope,
               source_type, source_reference, format_original, format_detail, start_year, end_year,
               end_year_status, region_description, characterization,
               reported_entry_count
        FROM source ORDER BY source_name
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
                reported_entry_count, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (*values, None))
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
    if getattr(g,"current_access_role",None)=="analyst": abort(404)

    conexion = conectar()
    fuente = conexion.execute("""
        SELECT source_id, source_name, source_type, source_reference, legacy_source_code, source_scope,
               format_original, format_detail, start_year, end_year,
               end_year_status, region_description, characterization,
               reported_entry_count
        FROM source WHERE source_id = ?
    """, (source_id,)).fetchone()
    occurrence_count = conexion.execute("SELECT count(*) FROM occurrence WHERE source_id=?",(source_id,)).fetchone()[0]
    conexion.close()
    if fuente is None:
        return "La fuente no existe.", 404
    return render_template("editar_fuente.html", fuente=fuente, source_types=SOURCE_TYPES, occurrence_count=occurrence_count)


@sources_bp.route("/fuentes/<int:source_id>/actualizar", methods=["POST"])
def actualizar_fuente(source_id):
    if getattr(g,"current_access_role",None)=="analyst": abort(404)

    try:
        values = source_form_values(request.form)
    except ValueError:
        return "Los metadatos de la fuente no son válidos.", 400
    if not values[0]:
        return "El nombre de la fuente es obligatorio.", 400

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        actual = conexion.execute("""
            SELECT source_name, source_type, source_reference,
                   legacy_source_code, source_scope, format_original,
                   format_detail, start_year, end_year, end_year_status,
                   region_description, characterization, reported_entry_count
            FROM source WHERE source_id = ?
        """, (source_id,)).fetchone()
        if actual is None:
            conexion.rollback()
            return "La fuente no existe.", 404
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
                    reported_entry_count, change_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id, actual["source_name"], actual["source_type"],
                actual["source_reference"], actual["legacy_source_code"],
                actual["source_scope"], actual["format_original"],
                actual["format_detail"], actual["start_year"],
                actual["end_year"], actual["end_year_status"],
                actual["region_description"], actual["characterization"],
                actual["reported_entry_count"],
                request.form.get("change_note") or None,
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
