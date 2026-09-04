from flask import Blueprint, render_template, request, redirect, url_for
from database import conectar
from access_control import requires_master
from activity import record_activity
from source_details import analysts_may_create_sources


main_bp = Blueprint(
    "main",
    __name__
)


@main_bp.route("/trabajo")
def trabajo():
    db=conectar()
    try: enabled=analysts_may_create_sources(db)
    finally: db.close()
    return render_template("trabajo.html", analyst_source_creation=enabled)

@main_bp.post("/configuracion/creacion-fuentes")
@requires_master
def update_source_creation_setting():
    enabled="1" if request.form.get("enabled")=="1" else "0"
    db=conectar()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE application_setting SET setting_value=?,updated_at=CURRENT_TIMESTAMP,updated_by_collaborator_id=? WHERE setting_key='analyst_source_creation'",(enabled,request.form.get("collaborator_id") or None))
        record_activity(db,"analyst_source_creation_setting_changed",entity_type="application_setting",collaborator_id=request.form.get("collaborator_id"),access_role="master",comment="Activado" if enabled=="1" else "Desactivado")
        db.commit()
    except Exception: db.rollback(); raise
    finally: db.close()
    return redirect(url_for("main.trabajo"))
