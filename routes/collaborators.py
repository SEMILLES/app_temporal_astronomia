import sqlite3

from flask import Blueprint, redirect, render_template, request, url_for, g

from activity import record_activity
from database import conectar

collaborators_bp = Blueprint("collaborators", __name__)


@collaborators_bp.route("/colaboradores")
def collaborators():
    db = conectar()
    try:
        rows = db.execute(
            "SELECT * FROM collaborator ORDER BY active DESC,display_name,collaborator_id"
        ).fetchall()
    finally: db.close()
    return render_template("colaboradores.html", collaborators=rows)


@collaborators_bp.route("/colaboradores", methods=["POST"])
def create_collaborator():
    name = (request.form.get("display_name") or "").strip()
    if not name: return "El nombre no puede estar vacío.", 400
    db = conectar()
    try:
        db.execute("BEGIN IMMEDIATE")
        collaborator_id = db.execute(
            "INSERT INTO collaborator(display_name) VALUES(?)", (name,)
        ).lastrowid
        record_activity(db, "collaborator_created", entity_type="collaborator",
                        entity_id=collaborator_id,
                        collaborator_id=request.form.get("collaborator_id"),
                        access_role=g.current_access_role)
        db.commit()
    except sqlite3.Error:
        db.rollback(); raise
    finally: db.close()
    return redirect(url_for("collaborators.collaborators"))


@collaborators_bp.route("/colaboradores/<int:collaborator_id>/editar", methods=["POST"])
def rename_collaborator(collaborator_id):
    name = (request.form.get("display_name") or "").strip()
    if not name: return "El nombre no puede estar vacío.", 400
    db = conectar()
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "UPDATE collaborator SET display_name=? WHERE collaborator_id=?",
            (name, collaborator_id),
        )
        if cursor.rowcount != 1:
            db.rollback(); return "El colaborador no existe.", 404
        record_activity(db, "collaborator_renamed", entity_type="collaborator",
                        entity_id=collaborator_id,
                        collaborator_id=request.form.get("collaborator_id"),
                        access_role=g.current_access_role)
        db.commit()
    except sqlite3.Error:
        db.rollback(); raise
    finally: db.close()
    return redirect(url_for("collaborators.collaborators"))
