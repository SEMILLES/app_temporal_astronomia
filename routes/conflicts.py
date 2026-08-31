from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from access_control import requires_reviewer
from conflict_rules import ConflictSubject, RULES
from conflicts import (ConflictError, attempt_conflict_resolution,
    create_manual_conflict, get_conflict_detail, list_conflicts,
    run_global_conflict_validation)
from database import conectar
from conflict_presentation import format_subject, local_timestamp, subject_choices, ui_label

conflicts_bp=Blueprint("conflicts",__name__)

def _actor():
    return {"collaborator_id":request.form.get("collaborator_id"),"access_role":g.current_access_role}

@conflicts_bp.route("/conflictos")
@requires_reviewer
def conflicts_list():
    db=conectar()
    try:
        rows=list_conflicts(db,status=request.args.get("status","open"),severity=request.args.get("severity") or None,origin_kind=request.args.get("origin") or None)
        presented=[]
        for row in rows:
            item=dict(row)
            subjects=db.execute("SELECT subject_type,subject_id FROM conflict_subject WHERE conflict_id=? ORDER BY conflict_subject_id",(row["conflict_id"],)).fetchall()
            item["subject_labels"]=[format_subject(db,s[0],s[1]) for s in subjects]
            presented.append(item)
        rows=presented
    except ConflictError as error: abort(400,str(error))
    finally: db.close()
    return render_template("conflictos.html",conflicts=rows,filters=request.args,message=request.args.get("message"),ui_label=ui_label,local_timestamp=local_timestamp)

@conflicts_bp.route("/conflictos/nuevo",methods=["GET","POST"])
@requires_reviewer
def new_conflict():
    db=conectar()
    if request.method=="GET":
        try:return render_template("nuevo_conflicto.html",choices=subject_choices(db))
        finally:db.close()
    try:
        choices=subject_choices(db);valid={(item["type"],int(item["id"])) for item in choices}
        types=request.form.getlist("subject_type");ids=request.form.getlist("subject_id")
        subjects=[]
        for subject_type,subject_id in zip(types,ids):
            if not subject_type and not subject_id:continue
            pair=(subject_type,int(subject_id))
            if pair not in valid:raise ConflictError("El elemento afectado seleccionado no existe o no coincide con su tipo.")
            subjects.append(ConflictSubject(subject_type,int(subject_id),"subject"))
        conflict_id=create_manual_conflict(db,description=request.form.get("description"),subjects=subjects,severity=request.form.get("severity"),justification=request.form.get("justification"),resolution_criteria=request.form.get("resolution_criteria"),actor_context=_actor())
        db.commit();return redirect(url_for("conflicts.conflict_detail",conflict_id=conflict_id,message="Conflicto manual creado."))
    except (ConflictError,ValueError) as error:
        db.rollback();return render_template("nuevo_conflicto.html",error=str(error),form=request.form,choices=subject_choices(db)),400
    finally:db.close()

@conflicts_bp.route("/conflictos/<int:conflict_id>")
@requires_reviewer
def conflict_detail(conflict_id):
    db=conectar()
    try:
        detail=get_conflict_detail(db,conflict_id)
        if detail is not None:
            conflict,subjects,attempts=detail
            subject_labels=[format_subject(db,s["subject_type"],s["subject_id"]) for s in subjects]
    finally:db.close()
    if detail is None:abort(404)
    conflict,subjects,attempts=detail
    condition=RULES[conflict["rule_code"]].validator_condition if conflict["origin_kind"]=="automatic" else None
    return render_template("detalle_conflicto.html",conflict=conflict,subjects=subjects,subject_labels=subject_labels,attempts=attempts,validator_condition=condition,message=request.args.get("message"),ui_label=ui_label,local_timestamp=local_timestamp)

@conflicts_bp.post("/conflictos/<int:conflict_id>/resolver")
@requires_reviewer
def resolve_conflict(conflict_id):
    db=conectar()
    try:
        resolved,failure=attempt_conflict_resolution(db,conflict_id,comment=request.form.get("comment"),manual_confirmed=request.form.get("manual_confirmed")=="yes",actor_context=_actor())
        message="Conflicto resuelto." if resolved else f"La condición persiste: {failure}"
        return redirect(url_for("conflicts.conflict_detail",conflict_id=conflict_id,message=message))
    except ConflictError as error:
        return str(error),400
    finally:db.close()

@conflicts_bp.post("/conflictos/validar")
@requires_reviewer
def validate_conflicts():
    db=conectar()
    try:
        result=run_global_conflict_validation(db,actor_context=_actor());db.commit()
        message=f"Validación completa: {len(result['created_conflict_ids'])} nuevos; {len(result['already_open_conflict_ids'])} ya abiertos."
    except Exception:db.rollback();raise
    finally:db.close()
    return redirect(url_for("conflicts.conflicts_list",message=message))
