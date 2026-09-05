"""Reviewer/Master retirement UI; signed preview binds the reviewed state."""
import json
import secrets
import sqlite3

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from access_control import requires_reviewer
from database import conectar
from source_details import SOURCE_TYPES
from source_forms import SOURCE_FIELDS
from source_structural import SourceStructuralError, active_source, apply_retirement, preview_retirement

source_retirement_bp = Blueprint("source_retirement", __name__)
_process_key = secrets.token_hex(32)


def serializer():
    return URLSafeTimedSerializer(current_app.config.get("SECRET_KEY") or _process_key, salt="source-retirement-v1")


@source_retirement_bp.route("/fuentes/<int:source_id>/retirar", methods=["GET", "POST"])
@requires_reviewer
def retire_source(source_id):
    db = conectar()
    try:
        if request.method == "POST" and request.form.get("action") == "confirm":
            if request.form.get("confirm") != "yes":
                raise SourceStructuralError("Confirme explícitamente el retiro.")
            try:
                payload = serializer().loads(request.form.get("token", ""), max_age=3600)
            except BadSignature as error:
                raise SourceStructuralError("Preview inválido o vencido. Vuelva a previsualizar.") from error
            if payload["source_id"] != source_id or payload["role"] != g.current_access_role:
                raise SourceStructuralError("El preview no corresponde a esta operación.")
            apply_retirement(db, source_id, payload["destination_id"], payload["new_source"],
                             fingerprint=payload["fingerprint"], reason=request.form.get("reason"),
                             resolution=request.form.get("resolution"),
                             actor={"access_role": g.current_access_role, "collaborator_id": request.form.get("collaborator_id")})
            return redirect(url_for("sources.source_retirement.source_history", source_id=source_id))
        db.execute("BEGIN")
        try:
            source = active_source(db, source_id)
        except SourceStructuralError:
            abort(404)
        plan = token = None
        if request.method == "POST":
            if request.form.get("action") != "preview":
                raise SourceStructuralError("Acción no válida.")
            mode = request.form.get("destination_mode")
            if mode not in ("existing", "new", "none"):
                raise SourceStructuralError("Elija el destino.")
            destination_id = request.form.get("destination_id") if mode == "existing" else None
            if mode == "existing" and not destination_id:
                raise SourceStructuralError("Seleccione la fuente destino.")
            new_source = {k: request.form.get(k, "") for k in SOURCE_FIELDS} if mode == "new" else None
            plan = preview_retirement(db, source_id, destination_id, new_source)
            token = serializer().dumps({"source_id": source_id, "destination_id": destination_id,
                                       "new_source": new_source, "fingerprint": plan["fingerprint"],
                                       "role": g.current_access_role})
        targets = db.execute("SELECT * FROM source WHERE retired_at IS NULL AND source_id!=? ORDER BY source_name", (source_id,)).fetchall()
        count = db.execute("SELECT count(*) FROM occurrence WHERE source_id=?", (source_id,)).fetchone()[0]
        return render_template("retirar_fuente.html", source=source, fuentes=targets, count=count,
                               plan=plan, token=token, source_types=SOURCE_TYPES)
    except (SourceStructuralError, sqlite3.IntegrityError) as error:
        return render_template("source_operation_error.html", source_id=source_id, error=str(error)), 400
    finally:
        db.close()


@source_retirement_bp.get("/fuentes/retiradas")
@requires_reviewer
def retired_sources():
    db = conectar()
    try:
        sources = db.execute("SELECT * FROM source WHERE retired_at IS NOT NULL ORDER BY retired_at DESC,source_id").fetchall()
        return render_template("fuentes_retiradas.html", sources=sources)
    finally:
        db.close()


@source_retirement_bp.get("/fuentes/<int:source_id>/historial")
@requires_reviewer
def source_history(source_id):
    db = conectar()
    try:
        source = db.execute("SELECT * FROM source WHERE source_id=?", (source_id,)).fetchone()
        if source is None:
            abort(404)
        events = []
        for row in db.execute("SELECT * FROM activity_event WHERE event_type='source_retired' ORDER BY activity_event_id DESC"):
            event = dict(row)
            payload = json.loads(row["comment"])
            if source_id in (payload["source_id"], payload["destination_id"]):
                events.append({**event, "operation": payload})
        revisions = db.execute("SELECT * FROM source_revision WHERE source_id=? ORDER BY source_revision_id DESC", (source_id,)).fetchall()
        return render_template("source_history.html", source=source, events=events, revisions=revisions)
    finally:
        db.close()
