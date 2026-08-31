import sqlite3

from activity import record_activity, resolve_collaborator
from conflict_rules import RULES, ConflictSubject, detect_all, subject_signature, validate_finding_resolved


class ConflictError(ValueError): pass


def _actor(connection, actor_context, required=False):
    actor_context=actor_context or {}; role=actor_context.get("access_role")
    if required and role not in ("reviewer","master"):
        raise ConflictError("Se requiere acceso reviewer o master.")
    collaborator_id,name=resolve_collaborator(connection,actor_context.get("collaborator_id"))
    return collaborator_id,name,role


def create_or_get_automatic_conflict(connection,finding,*,detection_source="workflow",triggering_entity_type=None,triggering_entity_id=None,actor_context=None):
    if finding.rule_code not in RULES or finding.severity != RULES[finding.rule_code].severity:
        raise ConflictError("Finding no corresponde al registry de reglas.")
    existing=connection.execute("SELECT conflict_id FROM conflict WHERE origin_kind='automatic' AND status='open' AND rule_code=? AND subject_signature=?",(finding.rule_code,finding.subject_signature)).fetchone()
    if existing:return existing[0],False
    actor_id,name,role=_actor(connection,actor_context)
    if role not in (None,"reviewer","master"): actor_id=name=role=None
    try:
        cursor=connection.execute("""INSERT INTO conflict(origin_kind,rule_code,severity,status,description,subject_signature,detection_source,triggering_entity_type,triggering_entity_id,created_by_collaborator_id,created_by_name_snapshot,created_access_role)
          VALUES('automatic',?,?,'open',?,?,?,?,?,?,?,?)""",(finding.rule_code,finding.severity,finding.description,finding.subject_signature,detection_source,triggering_entity_type,triggering_entity_id,actor_id,name,role))
    except sqlite3.IntegrityError:
        row=connection.execute("SELECT conflict_id FROM conflict WHERE origin_kind='automatic' AND status='open' AND rule_code=? AND subject_signature=?",(finding.rule_code,finding.subject_signature)).fetchone()
        if row:return row[0],False
        raise
    conflict_id=cursor.lastrowid
    connection.executemany("INSERT INTO conflict_subject(conflict_id,subject_type,subject_id,subject_role) VALUES(?,?,?,?)",[(conflict_id,s.subject_type,s.subject_id,s.subject_role) for s in finding.subjects])
    if role in ("reviewer","master"):
        record_activity(connection,"conflict_created",entity_type="conflict",entity_id=conflict_id,collaborator_id=actor_id,access_role=role)
    return conflict_id,True


def persist_findings(connection,findings,*,detection_source="workflow",triggering_entity_type=None,triggering_entity_id=None,actor_context=None):
    created=[];existing=[]
    for finding in findings:
        conflict_id,is_new=create_or_get_automatic_conflict(connection,finding,detection_source=detection_source,triggering_entity_type=triggering_entity_type,triggering_entity_id=triggering_entity_id,actor_context=actor_context)
        (created if is_new else existing).append(conflict_id)
    return created,existing


def create_manual_conflict(connection,*,description,subjects,severity,justification,resolution_criteria,actor_context):
    actor_id,name,role=_actor(connection,actor_context,True)
    description=(description or "").strip();justification=(justification or "").strip();criteria=(resolution_criteria or "").strip()
    if not description or not justification or not criteria:raise ConflictError("Descripción, justificación y criterio son obligatorios.")
    if severity not in ("blocking","non_blocking"):raise ConflictError("Severidad no válida.")
    normalized=tuple(ConflictSubject(str(s.subject_type).strip(),int(s.subject_id),str(s.subject_role).strip()) for s in subjects)
    signature=subject_signature(normalized)
    cursor=connection.execute("""INSERT INTO conflict(origin_kind,rule_code,severity,status,description,justification,resolution_criteria,subject_signature,detection_source,created_by_collaborator_id,created_by_name_snapshot,created_access_role)
      VALUES('manual',NULL,?,'open',?,?,?,?,'manual',?,?,?)""",(severity,description,justification,criteria,signature,actor_id,name,role))
    conflict_id=cursor.lastrowid
    connection.executemany("INSERT INTO conflict_subject(conflict_id,subject_type,subject_id,subject_role) VALUES(?,?,?,?)",[(conflict_id,s.subject_type,s.subject_id,s.subject_role) for s in normalized])
    record_activity(connection,"manual_conflict_created",entity_type="conflict",entity_id=conflict_id,collaborator_id=actor_id,access_role=role)
    return conflict_id


def list_conflicts(connection,*,status="open",severity=None,origin_kind=None):
    where=[];params=[]
    if status in ("open","resolved"):where.append("c.status=?");params.append(status)
    elif status != "all":raise ConflictError("Filtro de estado no válido.")
    if severity:where.append("c.severity=?");params.append(severity)
    if origin_kind:where.append("c.origin_kind=?");params.append(origin_kind)
    sql="SELECT c.*,(SELECT count(*) FROM conflict_subject cs WHERE cs.conflict_id=c.conflict_id) subject_count FROM conflict c"
    if where:sql+=" WHERE "+" AND ".join(where)
    return connection.execute(sql+" ORDER BY c.status,c.created_at DESC,c.conflict_id DESC",params).fetchall()


def get_conflict_detail(connection,conflict_id):
    conflict=connection.execute("SELECT * FROM conflict WHERE conflict_id=?",(conflict_id,)).fetchone()
    if conflict is None:return None
    subjects=connection.execute("SELECT * FROM conflict_subject WHERE conflict_id=? ORDER BY subject_type,subject_id,subject_role",(conflict_id,)).fetchall()
    attempts=connection.execute("SELECT * FROM conflict_resolution_attempt WHERE conflict_id=? ORDER BY attempted_at,conflict_resolution_attempt_id",(conflict_id,)).fetchall()
    return conflict,subjects,attempts


def attempt_conflict_resolution(connection,conflict_id,*,comment,actor_context,manual_confirmed=False):
    actor_id,name,role=_actor(connection,actor_context,True);comment=(comment or "").strip()
    if not comment:raise ConflictError("El comentario es obligatorio.")
    owns=not connection.in_transaction
    connection.execute("BEGIN IMMEDIATE" if owns else "SAVEPOINT resolve_conflict")
    try:
        conflict=connection.execute("SELECT * FROM conflict WHERE conflict_id=?",(conflict_id,)).fetchone()
        if conflict is None:raise ConflictError("El conflicto no existe.")
        if conflict["status"]!="open":raise ConflictError("El conflicto ya está resuelto.")
        if conflict["origin_kind"]=="manual":
            if not manual_confirmed:raise ConflictError("Debe confirmar que se cumplió el criterio de resolución.")
            resolved=True;failure=None
        else:resolved,failure=validate_finding_resolved(connection,conflict)
        outcome="succeeded" if resolved else "failed"
        connection.execute("""INSERT INTO conflict_resolution_attempt(conflict_id,outcome,comment,failure_reason,collaborator_id,collaborator_name_snapshot,access_role)
          VALUES(?,?,?,?,?,?,?)""",(conflict_id,outcome,comment,failure,actor_id,name,role))
        if resolved:
            connection.execute("UPDATE conflict SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE conflict_id=?",(conflict_id,))
            event="conflict_resolved"
        else:event="conflict_resolution_attempt_failed"
        record_activity(connection,event,entity_type="conflict",entity_id=conflict_id,collaborator_id=actor_id,access_role=role,comment=comment)
        connection.commit() if owns else connection.execute("RELEASE SAVEPOINT resolve_conflict")
        return resolved,failure
    except Exception:
        if owns:connection.rollback()
        else:connection.execute("ROLLBACK TO SAVEPOINT resolve_conflict");connection.execute("RELEASE SAVEPOINT resolve_conflict")
        raise


def detect_conflicts_after_change(connection,changed_entity_type,changed_entity_id,detection_source="workflow",actor_context=None):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conflict'").fetchone() is None:
        return {"created_conflict_ids":[],"already_open_conflict_ids":[]}
    relevant=[r for r in RULES.values() if changed_entity_type in r.scopes]
    scope={}
    if changed_entity_type in ("alternative","occurrence","submission","concept"):
        scope[f"{changed_entity_type}_id"]=int(changed_entity_id)
    if changed_entity_type=="renumber_event":
        row=connection.execute("SELECT concept_id FROM renumber_event WHERE renumber_event_id=?",(changed_entity_id,)).fetchone();scope["concept_id"]=row[0] if row else None
    if changed_entity_type=="alternative_relation":
        row=connection.execute("SELECT alternative_low_id FROM alternative_relation WHERE alternative_relation_id=?",(changed_entity_id,)).fetchone()
        if row:scope["alternative_id"]=row[0]
    findings=[f for rule in relevant for f in rule.detector(connection,**scope)]
    created,existing=persist_findings(connection,findings,detection_source=detection_source,triggering_entity_type=changed_entity_type,triggering_entity_id=changed_entity_id,actor_context=actor_context)
    return {"created_conflict_ids":created,"already_open_conflict_ids":existing}


def run_global_conflict_validation(connection,actor_context=None):
    findings=detect_all(connection);created,existing=persist_findings(connection,findings,detection_source="global_validation",actor_context=actor_context)
    blocking=[r[0] for r in connection.execute("SELECT conflict_id FROM conflict WHERE status='open' AND severity='blocking' ORDER BY conflict_id")]
    nonblocking=[r[0] for r in connection.execute("SELECT conflict_id FROM conflict WHERE status='open' AND severity='non_blocking' ORDER BY conflict_id")]
    return {"created_conflict_ids":created,"already_open_conflict_ids":existing,"blocking_open_ids":blocking,"non_blocking_open_ids":nonblocking}


def has_blocking_open_conflicts(connection):
    return connection.execute("SELECT 1 FROM conflict WHERE status='open' AND severity='blocking' LIMIT 1").fetchone() is not None
