from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

import sqlite3

from database import conectar


sources_bp = Blueprint(
    "sources",
    __name__
)


# =========================================================
# CONSULTAR FUENTES
# =========================================================

@sources_bp.route("/fuentes")
def fuentes():

    conexion = conectar()

    fuentes = conexion.execute("""
        SELECT
            source_id,
            source_name

        FROM source

        ORDER BY source_name
    """).fetchall()

    conexion.close()

    return render_template(
        "fuentes.html",
        fuentes=fuentes
    )


# =========================================================
# CREAR FUENTE
# =========================================================

@sources_bp.route(
    "/fuentes/nueva",
    methods=["POST"]
)
def nueva_fuente():

    source_name = request.form.get(
        "source_name",
        ""
    ).strip()

    if not source_name:

        return (
            "El nombre de la fuente es obligatorio.",
            400
        )

    conexion = conectar()

    try:

        conexion.execute("""
            INSERT INTO source
            (source_name)

            VALUES (?)
        """, (source_name,))

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.close()

        return (
            "Ya existe una fuente con ese nombre.",
            400
        )

    conexion.close()

    return redirect(
        url_for("sources.fuentes")
    )


# =========================================================
# FORMULARIO EDITAR
# =========================================================

@sources_bp.route(
    "/fuentes/<int:source_id>/editar"
)
def editar_fuente(source_id):

    conexion = conectar()

    fuente = conexion.execute("""
        SELECT
            source_id,
            source_name

        FROM source

        WHERE source_id = ?
    """, (source_id,)).fetchone()

    conexion.close()

    if fuente is None:

        return (
            "La fuente no existe.",
            404
        )

    return render_template(
        "editar_fuente.html",
        fuente=fuente
    )


# =========================================================
# ACTUALIZAR FUENTE
# =========================================================

@sources_bp.route(
    "/fuentes/<int:source_id>/actualizar",
    methods=["POST"]
)
def actualizar_fuente(source_id):

    source_name = request.form.get(
        "source_name",
        ""
    ).strip()

    if not source_name:

        return (
            "El nombre de la fuente es obligatorio.",
            400
        )

    conexion = conectar()

    try:

        cursor = conexion.execute("""
            UPDATE source

            SET source_name = ?

            WHERE source_id = ?
        """, (
            source_name,
            source_id
        ))

        if cursor.rowcount == 0:

            conexion.close()

            return (
                "La fuente no existe.",
                404
            )

        conexion.commit()

    except sqlite3.IntegrityError:

        conexion.close()

        return (
            "Ya existe otra fuente con ese nombre.",
            400
        )

    conexion.close()

    return redirect(
        url_for("sources.fuentes")
    )