"""Read-only preflight and atomic retirement of all evidence in a source."""
import hashlib
import json

from activity import record_activity, resolve_collaborator
from source_forms import SOURCE_FIELDS, source_insert_values


class SourceStructuralError(ValueError):
    pass


def active_source(db, source_id):
    row = db.execute("SELECT * FROM source WHERE source_id=? AND retired_at IS NULL", (source_id,)).fetchone()
    if row is None:
        raise SourceStructuralError("La fuente no existe o está retirada.")
    return dict(row)


def year_conflicts(occurrences, target):
    start, end = target["start_year"], target["end_year"]
    return [dict(row) for row in occurrences if row["occurrence_year"] is not None and
            ((start is not None and row["occurrence_year"] < start) or
             (end is not None and row["occurrence_year"] > end))]


def expanded_period(target, conflicts):
    start, end = target["start_year"], target["end_year"]
    years = [row["occurrence_year"] for row in conflicts]
    return (min([start, *years]) if start is not None else None,
            max([end, *years]) if end is not None else None,
            target["end_year_status"])


def preview_retirement(db, source_id, destination_id=None, new_source=None):
    source = active_source(db, source_id)
    occurrences = [dict(r) for r in db.execute("SELECT * FROM occurrence WHERE source_id=? ORDER BY occurrence_id", (source_id,))]
    if destination_id and new_source is not None:
        raise SourceStructuralError("Elija un único destino.")
    target = None
    if new_source is not None:
        try:
            target = dict(zip(SOURCE_FIELDS, source_insert_values(new_source)))
        except (ValueError, AttributeError) as error:
            raise SourceStructuralError("Los metadatos de la nueva fuente no son válidos; nombre y tipo son obligatorios.") from error
        if db.execute("SELECT 1 FROM source WHERE lower(trim(source_name))=lower(trim(?))", (target["source_name"],)).fetchone():
            raise SourceStructuralError("Ya existe una fuente con ese nombre.")
        target.update(source_id=None, analyst_protected=1, retired_at=None)
    elif destination_id:
        target = active_source(db, destination_id)
        if target["source_id"] == source["source_id"]:
            raise SourceStructuralError("El destino debe ser distinto del origen.")
    if occurrences and target is None:
        raise SourceStructuralError("Una fuente con occurrences exige un destino.")
    conflicts = year_conflicts(occurrences, target) if target else []
    # Fingerprint all original evidence and both source states, not just counts.
    state = {"source": source, "target": target, "occurrences": occurrences}
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**state, "fingerprint": fingerprint, "conflicts": conflicts,
            "different_types": target is not None and source["source_type"] != target["source_type"],
            "expanded_period": expanded_period(target, conflicts) if conflicts else None}


def snapshot(db, table, row, reason, actor_name):
    """Use the existing pre-change revision model, preserving every shared field."""
    revision = table + "_revision"
    revision_fields = {r[1] for r in db.execute(f"PRAGMA table_info({revision})")}
    fields = [key for key in row.keys() if key in revision_fields and key not in
              (revision + "_id", "changed_at", "changed_by", "change_note")]
    return db.execute(f"INSERT INTO {revision} ({','.join(fields)},changed_by,change_note) "
                      f"VALUES ({','.join('?' for _ in fields)},?,?)",
                      (*[row[key] for key in fields], actor_name, reason)).lastrowid


def apply_retirement(db, source_id, destination_id=None, new_source=None, *, fingerprint,
                     reason, resolution=None, actor):
    if actor.get("access_role") not in ("reviewer", "master"):
        raise SourceStructuralError("Rol no autorizado.")
    reason = (reason or "").strip()
    if not reason:
        raise SourceStructuralError("El motivo de la operación es obligatorio.")
    db.execute("BEGIN IMMEDIATE")
    try:
        plan = preview_retirement(db, source_id, destination_id, new_source)
        if not fingerprint or plan["fingerprint"] != fingerprint:
            raise SourceStructuralError("El preview quedó obsoleto. Vuelva a previsualizar la operación.")
        if plan["conflicts"] and resolution not in ("expand", "clear"):
            raise SourceStructuralError("Elija cómo resolver los años fuera del periodo destino.")
        if resolution not in (None, "", "expand", "clear"):
            raise SourceStructuralError("Resolución de años no válida.")
        _, name = resolve_collaborator(db, actor.get("collaborator_id"))
        target = plan["target"]
        target_revision = None
        if new_source is not None:
            target_id = db.execute(f"INSERT INTO source ({','.join(SOURCE_FIELDS)},analyst_protected,created_by) "
                                   f"VALUES ({','.join('?' for _ in SOURCE_FIELDS)},1,?)",
                                   (*[target[key] for key in SOURCE_FIELDS], name)).lastrowid
            target = active_source(db, target_id)
            record_activity(db, "source_created", entity_type="source", entity_id=target_id,
                            access_role=actor["access_role"], collaborator_id=actor.get("collaborator_id"))
        target_id = target["source_id"] if target else None
        if plan["conflicts"] and resolution == "expand":
            target_revision = snapshot(db, "source", target, reason, name)
            db.execute("UPDATE source SET start_year=?,end_year=?,end_year_status=?,updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
                       (*plan["expanded_period"], target_id))
        conflict_ids = {r["occurrence_id"] for r in plan["conflicts"]}
        revisions, cleared = [], []
        for occurrence in plan["occurrences"]:
            revisions.append(snapshot(db, "occurrence", occurrence, reason, name))
            year = occurrence["occurrence_year"]
            if resolution == "clear" and occurrence["occurrence_id"] in conflict_ids:
                cleared.append({"occurrence_id": occurrence["occurrence_id"], "previous_year": year})
                year = None
            db.execute("UPDATE occurrence SET source_id=?,occurrence_year=?,updated_at=CURRENT_TIMESTAMP WHERE occurrence_id=?",
                       (target_id, year, occurrence["occurrence_id"]))
        source_revision = snapshot(db, "source", plan["source"], reason, name)
        db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE source_id=?", (source_id,))
        payload = {"reason": reason, "source_id": source_id, "destination_id": target_id,
                   "source_name": plan["source"]["source_name"],
                   "destination_name": target["source_name"] if target else None,
                   "occurrence_count": len(revisions),
                   "occurrence_ids": [r["occurrence_id"] for r in plan["occurrences"]],
                   "occurrence_revision_ids": revisions, "source_revision_id": source_revision,
                   "destination_created": new_source is not None,
                   "destination_revision_id": target_revision,
                   "period_before": [target[k] for k in ("start_year", "end_year", "end_year_status")] if target else None,
                   "period_after": plan["expanded_period"] if target_revision else None,
                   "cleared_years": cleared}
        event = record_activity(db, "source_retired", entity_type="source", entity_id=source_id,
                                access_role=actor["access_role"], collaborator_id=actor.get("collaborator_id"),
                                comment=json.dumps(payload, ensure_ascii=False, sort_keys=True))
        db.commit()
        return event
    except Exception:
        db.rollback()
        raise
