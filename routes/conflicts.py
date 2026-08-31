from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from access_control import requires_reviewer
from conflict_rules import ConflictSubject, RULES
from conflicts import (ConflictError, attempt_conflict_resolution,
    create_manual_conflict, get_conflict_detail, list_conflicts,
    run_global_conflict_validation)
from database import conectar

conflicts_bp=Blueprint("conflicts",__name__)

def _actor():
    return {"collaborator_id":request.form.get("collaborator_id"),"access_role":g.current_access_role}

@conflicts_bp.route("/conflictos")
@requires_reviewer
def conflicts_list():
    db=conectar()
    try: rows=list_conflicts(db,status=request.args.get("status","open"),severity=request.args.get("severity") or None,origin_kind=request.args.get("origin") or None)
    except ConflictError as error: abort(400,str(error))
    finally: db.close()
    return render_template("conflictos.html",conflicts=rows,filters=request.args)

@conflicts_bp.route("/conflictos/nuevo",methods=["GET","POST"])
@requires_reviewer
def new_conflict():
    if request.method=="GET":return render_template("nuevo_conflicto.html")
    db=conectar()
    try:
        subjects=[]
        for line in request.form.get("subjects","").splitlines():
            parts=[p.strip() for p in line.split(":")]
            if len(parts)!=3:raise ConflictError("Cada subject debe usar tipo:id:rol.")
            subjects.append(ConflictSubject(parts[0],int(parts[1]),parts[2]))
        conflict_id=create_manual_conflict(db,description=request.form.get("description"),subjects=subjects,severity=request.form.get("severity"),justification=request.form.get("justification"),resolution_criteria=request.form.get("resolution_criteria"),actor_context=_actor())
        db.commit();return redirect(url_for("conflicts.conflict_detail",conflict_id=conflict_id))
    except (ConflictError,ValueError) as error:
        db.rollback();return render_template("nuevo_conflicto.html",error=str(error),form=request.form),400
    finally:db.close()

@conflicts_bp.route("/conflictos/<int:conflict_id>")
@requires_reviewer
def conflict_detail(conflict_id):
    db=conectar()
    try: detail=get_conflict_detail(db,conflict_id)
    finally:db.close()
    if detail is None:abort(404)
    conflict,subjects,attempts=detail
    condition=RULES[conflict["rule_code"]].validator_condition if conflict["origin_kind"]=="automatic" else None
    return render_template("detalle_conflicto.html",conflict=conflict,subjects=subjects,attempts=attempts,validator_condition=condition)

@conflicts_bp.post("/conflictos/<int:conflict_id>/resolver")
@requires_reviewer
def resolve_conflict(conflict_id):
    db=conectar()
    try:
        resolved,failure=attempt_conflict_resolution(db,conflict_id,comment=request.form.get("comment"),manual_confirmed=request.form.get("manual_confirmed")=="yes",actor_context=_actor())
        flash("Conflicto resuelto." if resolved else f"La condición persiste: {failure}")
        return redirect(url_for("conflicts.conflict_detail",conflict_id=conflict_id))
    except ConflictError as error:
        return str(error),400
    finally:db.close()

@conflicts_bp.post("/conflictos/validar")
@requires_reviewer
def validate_conflicts():
    db=conectar()
    try:
        result=run_global_conflict_validation(db,actor_context=_actor());db.commit()
        flash(f"Validación completa: {len(result['created_conflict_ids'])} nuevos; {len(result['already_open_conflict_ids'])} ya abiertos.")
    except Exception:db.rollback();raise
    finally:db.close()
    return redirect(url_for("conflicts.conflicts_list"))
