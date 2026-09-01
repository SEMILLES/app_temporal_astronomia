"""Transactional structural operations for canonical alternatives."""

import json

from activity import record_activity
from alternative_admin import AlternativeAdminError, actor_name, _blocking_ids, _reject_new_blocking
from alternative_nomenclature import calculate_nomenclature_preview, apply_nomenclature
from assignments import create_or_replace_assignment
from conflict_rules import detect_all


class StructuralAlternativeError(AlternativeAdminError):
    pass


def _active(connection, alternative_id):
    row = connection.execute(
        "SELECT a.*,c.preferred_label concept_label FROM alternative a JOIN concept c USING(concept_id) "
        "WHERE alternative_id=? AND retired_at IS NULL", (alternative_id,)).fetchone()
    if row is None:
        raise StructuralAlternativeError("La alternativa no existe o estÃ¡ retirada.")
    return row


def _reason(value):
    value = (value or "").strip()
    if not value:
        raise StructuralAlternativeError("El motivo o comentario es obligatorio.")
    return value


def _occurrences(connection, alternative_id):
    return connection.execute("""
        SELECT o.occurrence_id,o.original_gloss,r.concept_id AS reference_concept_id,
               r.concept_proposal_id AS reference_concept_proposal_id,s.source_name,a.assignment_id
        FROM assignment a JOIN occurrence o USING(occurrence_id) JOIN source s USING(source_id)
        LEFT JOIN occurrence_concept_reference r ON r.occurrence_id=o.occurrence_id AND r.is_current=1
        WHERE a.alternative_id=? AND a.is_current=1 ORDER BY o.occurrence_id
    """, (alternative_id,)).fetchall()


def _relations(connection, alternative_id):
    return connection.execute("""
        SELECT r.*,CASE WHEN alternative_low_id=? THEN alternative_high_id ELSE alternative_low_id END other_id
        FROM alternative_relation r WHERE is_current=1
          AND (alternative_low_id=? OR alternative_high_id=?) ORDER BY alternative_relation_id
    """, (alternative_id, alternative_id, alternative_id)).fetchall()


def _conflict_preflight(connection):
    existing = {row[0] for row in connection.execute(
        "SELECT subject_signature FROM conflict WHERE status='open'")}
    findings = detect_all(connection)
    new = [finding for finding in findings if finding.subject_signature not in existing]
    return {
        "blocking": [finding.description for finding in new if finding.severity == "blocking"],
        "non_blocking": [finding.description for finding in new if finding.severity == "non_blocking"],
    }


def _persist_final_conflicts(connection, actor):
    from conflicts import persist_findings
    persist_findings(connection, detect_all(connection),
                     detection_source="alternative_structural",
                     actor_context=actor)


def _retire_parts(connection, alternative_id):
    connection.execute("UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=?", (alternative_id,))
    connection.execute("UPDATE alternative_morphology SET is_current=0 WHERE alternative_id=? AND is_current=1", (alternative_id,))
    connection.execute("UPDATE alternative_relation SET is_current=0 WHERE is_current=1 AND (alternative_low_id=? OR alternative_high_id=?)", (alternative_id, alternative_id))


def _nomenclature(connection, concept_id, reason, created_by):
    preview = calculate_nomenclature_preview(connection, concept_id)
    return apply_nomenclature(connection, concept_id, preview["suggestions"],
                              origin="automatic_assisted", reason=reason, created_by=created_by)


def _event(connection, event_type, source_id, actor, reason, **payload):
    data = {"reason": reason, "source_alternative_id": source_id, **payload}
    return record_activity(connection, event_type, entity_type="alternative", entity_id=source_id,
                           collaborator_id=actor.get("collaborator_id"), access_role=actor["access_role"],
                           comment=json.dumps(data, ensure_ascii=False, sort_keys=True))


def _simulate(connection, callback):
    connection.execute("SAVEPOINT structural_preview")
    try:
        result = callback()
        result["conflicts"] = _conflict_preflight(connection)
        return result
    finally:
        connection.execute("ROLLBACK TO structural_preview")
        connection.execute("RELEASE structural_preview")


def retire_preview(connection, alternative_id, resolutions=None):
    source = _active(connection, alternative_id); occurrences = _occurrences(connection, alternative_id)
    resolutions = {int(k): (None if v in (None, "", "unassigned") else int(v)) for k, v in (resolutions or {}).items()}
    if occurrences and {row["occurrence_id"] for row in occurrences} != set(resolutions):
        raise StructuralAlternativeError("Debe indicar un destino para cada ocurrencia.")
    for destination in (item for item in resolutions.values() if item is not None):
        row = _active(connection, destination)
        if row["concept_id"] != source["concept_id"] or destination == alternative_id:
            raise StructuralAlternativeError("El destino debe ser otra alternativa vigente del mismo concepto.")
    def operation():
        for row in occurrences:
            destination = resolutions[row["occurrence_id"]]
            if destination is None:
                connection.execute("UPDATE assignment SET is_current=0 WHERE assignment_id=?", (row["assignment_id"],))
            else:
                create_or_replace_assignment(connection, row["occurrence_id"], destination)
        _retire_parts(connection, alternative_id)
        labels = calculate_nomenclature_preview(connection, source["concept_id"])
        return {"kind":"retire", "source":dict(source), "occurrences":[dict(r) for r in occurrences],
                "resolutions":resolutions, "relations":[dict(r) for r in _relations_before],
                "labels":labels["rows"], "morphology_transferred":False}
    _relations_before = _relations(connection, alternative_id)
    return _simulate(connection, operation)


def apply_retire(connection, alternative_id, resolutions, *, reason, actor):
    reason=_reason(reason); retire_preview(connection, alternative_id, resolutions)
    source=_active(connection, alternative_id); occurrences=_occurrences(connection, alternative_id)
    before=_blocking_ids(connection); connection.execute("BEGIN IMMEDIATE")
    try:
        for row in occurrences:
            destination=resolutions.get(row["occurrence_id"], resolutions.get(str(row["occurrence_id"])))
            if destination in (None,"","unassigned"):
                connection.execute("UPDATE assignment SET is_current=0 WHERE assignment_id=?",(row["assignment_id"],))
            else:create_or_replace_assignment(connection,row["occurrence_id"],int(destination),created_by=actor_name(connection,actor.get("collaborator_id")))
        _retire_parts(connection,alternative_id); event=_nomenclature(connection,source["concept_id"],reason,actor_name(connection,actor.get("collaborator_id")))
        _persist_final_conflicts(connection,actor)
        _reject_new_blocking(connection,before)
        _event(connection,"alternative_retired",alternative_id,actor,reason,resolutions=resolutions,renumber_event_id=event)
        connection.commit(); return event
    except Exception: connection.rollback(); raise


def merge_preview(connection, source_id, target_id, relation_mode):
    source=_active(connection,source_id); target=_active(connection,target_id)
    if source_id==target_id or source["concept_id"]!=target["concept_id"]: raise StructuralAlternativeError("La fusiÃ³n exige dos alternativas distintas y vigentes del mismo concepto.")
    if relation_mode not in ("keep_target","union"): raise StructuralAlternativeError("Debe elegir cÃ³mo resolver las relaciones.")
    occurrences=_occurrences(connection,source_id); source_relations=_relations(connection,source_id)
    def operation():
        for row in occurrences:create_or_replace_assignment(connection,row["occurrence_id"],target_id)
        _retire_parts(connection,source_id)
        created=[]
        if relation_mode=="union":
            for relation in source_relations:
                other=relation["other_id"]
                if other==target_id:continue
                low,high=sorted((target_id,other))
                exists=connection.execute("SELECT 1 FROM alternative_relation WHERE alternative_low_id=? AND alternative_high_id=? AND phonological_parameter=? AND is_current=1",(low,high,relation["phonological_parameter"])).fetchone()
                if not exists:
                    connection.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,supersedes_alternative_relation_id) VALUES(?,?,?,?)",(low,high,relation["phonological_parameter"],relation["alternative_relation_id"]))
                    created.append((low,high,relation["phonological_parameter"]))
        labels=calculate_nomenclature_preview(connection,source["concept_id"])
        return {"kind":"merge","source":dict(source),"target":dict(target),"occurrences":[dict(r) for r in occurrences],"relations_retired":[dict(r) for r in source_relations],"relations_created":created,"relation_mode":relation_mode,"labels":labels["rows"],"morphology_transferred":False}
    return _simulate(connection,operation)


def apply_merge(connection,source_id,target_id,relation_mode,*,reason,actor):
    reason=_reason(reason); merge_preview(connection,source_id,target_id,relation_mode)
    source=_active(connection,source_id); occurrences=_occurrences(connection,source_id); relations=_relations(connection,source_id); before=_blocking_ids(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        for row in occurrences:create_or_replace_assignment(connection,row["occurrence_id"],target_id,created_by=actor_name(connection,actor.get("collaborator_id")))
        _retire_parts(connection,source_id); created=[]
        if relation_mode=="union":
            for relation in relations:
                other=relation["other_id"]
                if other==target_id:continue
                low,high=sorted((target_id,other))
                if not connection.execute("SELECT 1 FROM alternative_relation WHERE alternative_low_id=? AND alternative_high_id=? AND phonological_parameter=? AND is_current=1",(low,high,relation["phonological_parameter"])).fetchone():
                    rid=connection.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,supersedes_alternative_relation_id,created_by) VALUES(?,?,?,?,?)",(low,high,relation["phonological_parameter"],relation["alternative_relation_id"],actor_name(connection,actor.get("collaborator_id")))).lastrowid;created.append(rid)
        event=_nomenclature(connection,source["concept_id"],reason,actor_name(connection,actor.get("collaborator_id")));_persist_final_conflicts(connection,actor);_reject_new_blocking(connection,before)
        _event(connection,"alternative_merged",source_id,actor,reason,target_alternative_id=target_id,relation_mode=relation_mode,created_relation_ids=created,renumber_event_id=event)
        connection.commit();return event
    except Exception:connection.rollback();raise


def split_preview(connection,source_id,distribution,new_count):
    source=_active(connection,source_id); occurrences=_occurrences(connection,source_id)
    try:new_count=int(new_count)
    except (TypeError,ValueError):raise StructuralAlternativeError("La cantidad de alternativas nuevas no es vÃ¡lida.")
    if new_count<2:raise StructuralAlternativeError("Una divisiÃ³n debe crear al menos dos alternativas.")
    distribution={int(k):int(v) for k,v in (distribution or {}).items()}
    if {r["occurrence_id"] for r in occurrences}!=set(distribution) or any(v<1 or v>new_count for v in distribution.values()):raise StructuralAlternativeError("Cada ocurrencia debe asignarse exactamente a una alternativa nueva.")
    def operation():
        _retire_parts(connection,source_id); ids=[]
        for _ in range(new_count):ids.append(connection.execute("INSERT INTO alternative(concept_id,working_label) VALUES(?,NULL)",(source["concept_id"],)).lastrowid)
        for row in occurrences:create_or_replace_assignment(connection,row["occurrence_id"],ids[distribution[row["occurrence_id"]]-1])
        labels=calculate_nomenclature_preview(connection,source["concept_id"])
        return {"kind":"split","source":dict(source),"occurrences":[dict(r) for r in occurrences],"distribution":distribution,"virtual_ids":ids,"relations_retired":[dict(r) for r in _relations_before],"labels":labels["rows"],"morphology_transferred":False}
    _relations_before=_relations(connection,source_id);return _simulate(connection,operation)


def apply_split(connection,source_id,distribution,new_count,*,reason,actor):
    reason=_reason(reason);split_preview(connection,source_id,distribution,new_count);source=_active(connection,source_id);occurrences=_occurrences(connection,source_id);before=_blocking_ids(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        _retire_parts(connection,source_id);ids=[connection.execute("INSERT INTO alternative(concept_id,working_label,created_by) VALUES(?,NULL,?)",(source["concept_id"],actor_name(connection,actor.get("collaborator_id")))).lastrowid for _ in range(int(new_count))]
        for row in occurrences:
            index=distribution.get(row["occurrence_id"],distribution.get(str(row["occurrence_id"])))
            create_or_replace_assignment(connection,row["occurrence_id"],ids[int(index)-1],created_by=actor_name(connection,actor.get("collaborator_id")))
        event=_nomenclature(connection,source["concept_id"],reason,actor_name(connection,actor.get("collaborator_id")));_persist_final_conflicts(connection,actor);_reject_new_blocking(connection,before)
        _event(connection,"alternative_split",source_id,actor,reason,new_alternative_ids=ids,distribution=distribution,renumber_event_id=event)
        connection.commit();return ids,event
    except Exception:connection.rollback();raise


def move_preview(connection,source_id,destination_concept_id):
    source=_active(connection,source_id);destination=connection.execute("SELECT * FROM concept WHERE concept_id=?",(destination_concept_id,)).fetchone()
    if destination is None:raise StructuralAlternativeError("El concepto destino no existe.")
    if source["concept_id"]==int(destination_concept_id):raise StructuralAlternativeError("La alternativa ya pertenece a ese concepto.")
    relations=_relations(connection,source_id)
    def operation():
        connection.execute("UPDATE alternative_relation SET is_current=0 WHERE is_current=1 AND (alternative_low_id=? OR alternative_high_id=?)",(source_id,source_id));connection.execute("UPDATE alternative SET concept_id=? WHERE alternative_id=?",(destination_concept_id,source_id))
        return {"kind":"move","source":dict(source),"destination":dict(destination),"occurrences":[dict(r) for r in _occurrences(connection,source_id)],"relations_retired":[dict(r) for r in relations],"origin_labels":calculate_nomenclature_preview(connection,source["concept_id"])["rows"],"destination_labels":calculate_nomenclature_preview(connection,destination_concept_id)["rows"],"assignments_changed":False,"morphology_transferred":True}
    return _simulate(connection,operation)


def apply_move(connection,source_id,destination_concept_id,*,reason,actor):
    reason=_reason(reason);move_preview(connection,source_id,destination_concept_id);source=_active(connection,source_id);before=_blocking_ids(connection);old_context={r["occurrence_id"]:{"concept_id":r["reference_concept_id"],"concept_proposal_id":r["reference_concept_proposal_id"]} for r in _occurrences(connection,source_id)}
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("UPDATE alternative_relation SET is_current=0 WHERE is_current=1 AND (alternative_low_id=? OR alternative_high_id=?)",(source_id,source_id));connection.execute("UPDATE alternative SET concept_id=? WHERE alternative_id=?",(destination_concept_id,source_id))
        origin_event=_nomenclature(connection,source["concept_id"],reason,actor_name(connection,actor.get("collaborator_id")));destination_event=_nomenclature(connection,int(destination_concept_id),reason,actor_name(connection,actor.get("collaborator_id")));_persist_final_conflicts(connection,actor);_reject_new_blocking(connection,before)
        _event(connection,"alternative_moved",source_id,actor,reason,origin_concept_id=source["concept_id"],destination_concept_id=int(destination_concept_id),origin_renumber_event_id=origin_event,destination_renumber_event_id=destination_event,occurrence_context_snapshot=old_context)
        connection.commit();return origin_event,destination_event
    except Exception:connection.rollback();raise
