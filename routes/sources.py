from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

import sqlite3

from database import conectar


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
    return (
        form.get("source_name", "").strip(),
        form.get("source_type", "").strip() or None,
        form.get("source_reference", "").strip() or None,
        start_year,
        end_year,
        status,
    )


@sources_bp.route("/fuentes")
def fuentes():

    conexion = conectar()
    fuentes = conexion.execute("""
        SELECT source_id, source_name, source_type, source_reference,
               start_year, end_year, end_year_status
        FROM source ORDER BY source_name
    """).fetchall()
    conexion.close()
    return render_template("fuentes.html", fuentes=fuentes)


@sources_bp.route("/fuentes/nueva", methods=["POST"])
def nueva_fuente():

    try:
        values = source_form_values(request.form)
    except ValueError:
        return "Los años o el estado de la fuente no son válidos.", 400
    if not values[0]:
        return "El nombre de la fuente es obligatorio.", 400

    conexion = conectar()
    try:
        conexion.execute("""
            INSERT INTO source (
                source_name, source_type, source_reference,
                start_year, end_year, end_year_status, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (*values, None))
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
        SELECT source_id, source_name, source_type, source_reference,
               start_year, end_year, end_year_status
        FROM source WHERE source_id = ?
    """, (source_id,)).fetchone()
    conexion.close()
    if fuente is None:
        return "La fuente no existe.", 404
    return render_template("editar_fuente.html", fuente=fuente)


@sources_bp.route("/fuentes/<int:source_id>/actualizar", methods=["POST"])
def actualizar_fuente(source_id):

    try:
        values = source_form_values(request.form)
    except ValueError:
        return "Los años o el estado de la fuente no son válidos.", 400
    if not values[0]:
        return "El nombre de la fuente es obligatorio.", 400

    conexion = conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE")
        actual = conexion.execute("""
            SELECT source_name, source_type, source_reference,
                   start_year, end_year, end_year_status
            FROM source WHERE source_id = ?
        """, (source_id,)).fetchone()
        if actual is None:
            conexion.rollback()
            return "La fuente no existe.", 404
        if tuple(actual) != values:
            conexion.execute("""
                INSERT INTO source_revision (
                    source_id, source_name, source_type, source_reference,
                    start_year, end_year, end_year_status, change_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (source_id, *tuple(actual), request.form.get("change_note") or None))
        conexion.execute("""
            UPDATE source SET
                source_name = ?, source_type = ?, source_reference = ?,
                start_year = ?, end_year = ?, end_year_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE source_id = ?
        """, (*values, source_id))
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "Ya existe otra fuente con ese nombre.", 400
    finally:
        conexion.close()
    return redirect(url_for("sources.fuentes"))
