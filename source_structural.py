"""Read-only preflight and atomic structural operations on sources."""
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


def new_source_values(db, form):
    try:
        target = dict(zip(SOURCE_FIELDS, source_insert_values(form)))
    except (ValueError, AttributeError) as error:
        raise SourceStructuralError("Los metadatos de la nueva fuente no son válidos; nombre y tipo son obligatorios.") from error
    if db.execute("SELECT 1 FROM source WHERE lower(trim(source_name))=lower(trim(?))", (target["source_name"],)).fetchone():
        raise SourceStructuralError("Ya existe una fuente con ese nombre.")
    return {**target, "source_id": None, "analyst_protected": 1, "retired_at": None}


def create_destination(db, target, name, actor):
    target_id = db.execute(f"INSERT INTO source ({','.join(SOURCE_FIELDS)},analyst_protected,created_by) "
                           f"VALUES ({','.join('?' for _ in SOURCE_FIELDS)},1,?)",
                           (*[target[key] for key in SOURCE_FIELDS], name)).lastrowid
    record_activity(db, "source_created", entity_type="source", entity_id=target_id,
                    access_role=actor["access_role"], collaborator_id=actor.get("collaborator_id"))
    return active_source(db, target_id)


def move_occurrence(db, occurrence, target_id, year, reason, name):
    revision = snapshot(db, "occurrence", occurrence, reason, name)
    db.execute("UPDATE occurrence SET source_id=?,occurrence_year=?,updated_at=CURRENT_TIMESTAMP WHERE occurrence_id=?",
               (target_id, year, occurrence["occurrence_id"]))
    return revision


def change_period(db, target, period, reason, name):
    revision = snapshot(db, "source", target, reason, name)
    db.execute("UPDATE source SET start_year=?,end_year=?,end_year_status=?,updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
               (*period, target["source_id"]))
    return revision


def retire_empty_source(db, source, reason, name):
    revision = snapshot(db, "source", source, reason, name)
    db.execute("UPDATE source SET retired_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE source_id=?", (source["source_id"],))
    return revision


def preview_retirement(db, source_id, destination_id=None, new_source=None):
    source = active_source(db, source_id)
    occurrences = [dict(r) for r in db.execute("SELECT * FROM occurrence WHERE source_id=? ORDER BY occurrence_id", (source_id,))]
    if destination_id and new_source is not None:
        raise SourceStructuralError("Elija un único destino.")
    target = None
    if new_source is not None:
        target = new_source_values(db, new_source)
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
            target = create_destination(db, target, name, actor)
        target_id = target["source_id"] if target else None
        if plan["conflicts"] and resolution == "expand":
            target_revision = change_period(db, target, plan["expanded_period"], reason, name)
        conflict_ids = {r["occurrence_id"] for r in plan["conflicts"]}
        revisions, cleared = [], []
        for occurrence in plan["occurrences"]:
            year = occurrence["occurrence_year"]
            if resolution == "clear" and occurrence["occurrence_id"] in conflict_ids:
                cleared.append({"occurrence_id": occurrence["occurrence_id"], "previous_year": year})
                year = None
            revisions.append(move_occurrence(db, occurrence, target_id, year, reason, name))
        source_revision = retire_empty_source(db, plan["source"], reason, name)
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


def source_template(source):
    """Form defaults only; no persistent parent relationship."""
    return {key: "" if source[key] is None else str(source[key]) for key in SOURCE_FIELDS}


def preview_distribution(db, spec):
    """Split or merge using the same active-source, period and revision model."""
    kind = spec.get("kind")
    ids = spec.get("source_ids", [])
    if kind not in ("split", "merge") or not isinstance(ids, list):
        raise SourceStructuralError("Operación no válida.")
    if len(ids) != (1 if kind == "split" else 2) or len(set(ids)) != len(ids):
        raise SourceStructuralError("La fusión exige dos Sources activas distintas.")
    origins = [active_source(db, sid) for sid in ids]
    if len({s["source_id"] for s in origins}) != len(origins):
        raise SourceStructuralError("Los orígenes deben ser distintos.")
    mode = spec.get("mode")
    if kind == "split" and mode not in ("keep", "replace"):
        raise SourceStructuralError("Elija conservar o reemplazar la Source original.")
    occurrences = [dict(r) for source in origins for r in db.execute(
        "SELECT * FROM occurrence WHERE source_id=? ORDER BY occurrence_id", (source["source_id"],))]
    new_sources = spec.get("new_sources", {})
    if not isinstance(new_sources, dict):
        raise SourceStructuralError("Destinos nuevos no válidos.")
    targets = {}
    for key, form in new_sources.items():
        if not isinstance(key, str) or not key.startswith("new:") or not key[4:].isdigit():
            raise SourceStructuralError("Identificador de destino nuevo no válido.")
        targets[key] = new_source_values(db, form)
    names = [s["source_name"].casefold() for s in targets.values()]
    if len(names) != len(set(names)):
        raise SourceStructuralError("Las Sources nuevas deben tener nombres distintos.")
    if kind == "merge":
        if set(new_sources) != {"new:1"}:
            raise SourceStructuralError("La fusión exige una única Source destino nueva.")
        template_id = spec.get("template_source_id")
        if template_id is not None and template_id not in [s["source_id"] for s in origins]:
            raise SourceStructuralError("La plantilla debe ser uno de los orígenes.")
        distribution = {str(o["occurrence_id"]): "new:1" for o in occurrences}
    else:
        distribution = spec.get("distribution", {})
        if not isinstance(distribution, dict) or set(distribution) != {str(o["occurrence_id"]) for o in occurrences}:
            raise SourceStructuralError("Cada occurrence debe tener exactamente un destino; revise todas las asignaciones.")
        for key in distribution.values():
            if not isinstance(key, str):
                raise SourceStructuralError("Cada occurrence debe tener exactamente un destino.")
            if key in targets:
                continue
            if not key.startswith("source:") or not key[7:].isdigit() or str(int(key[7:])) != key[7:]:
                raise SourceStructuralError("Seleccione un destino válido para cada occurrence.")
            targets[key] = active_source(db, int(key[7:]))
        original_key = f"source:{origins[0]['source_id']}"
        used = set(distribution.values())
        if set(new_sources) - used:
            raise SourceStructuralError("Asigne occurrences a cada Source nueva o quite ese destino.")
        if mode == "keep" and not (used - {original_key}):
            raise SourceStructuralError("Debe mover al menos una occurrence.")
        if mode == "replace" and (original_key in used or len(used) < 2):
            raise SourceStructuralError("Reemplazar exige distribuir todas las occurrences entre al menos dos destinos distintos del origen.")
    groups = []
    for key, target in targets.items():
        assigned = [o for o in occurrences if distribution[str(o["occurrence_id"])] == key]
        moved = [o for o in assigned if o["source_id"] != target["source_id"]]
        conflicts = year_conflicts(moved, target)
        source_types = {s["source_id"]: s["source_type"] for s in origins}
        groups.append({"key": key, "target": target, "occurrences": assigned,
                       "conflicts": conflicts, "expanded_period": expanded_period(target, conflicts) if conflicts else None,
                       "different_types": any(source_types[o["source_id"]] != target["source_type"] for o in moved)})
    divergent = [field for field in SOURCE_FIELDS if kind == "merge" and origins[0][field] != origins[1][field]]
    state = {"spec": spec, "origins": origins, "occurrences": occurrences, "targets": targets}
    fingerprint = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"kind": kind, "mode": mode, "origins": origins, "occurrences": occurrences,
            "distribution": distribution, "groups": groups, "divergent_fields": divergent,
            "retire_ids": [s["source_id"] for s in origins] if kind == "merge" or mode == "replace" else [],
            "fingerprint": fingerprint}


def apply_distribution(db, spec, *, fingerprint, reason, resolutions=None, actor):
    if actor.get("access_role") not in ("reviewer", "master"):
        raise SourceStructuralError("Rol no autorizado.")
    reason = (reason or "").strip()
    if not reason:
        raise SourceStructuralError("El motivo de la operación es obligatorio.")
    resolutions = resolutions or {}
    db.execute("BEGIN IMMEDIATE")
    try:
        plan = preview_distribution(db, spec)
        if not fingerprint or plan["fingerprint"] != fingerprint:
            raise SourceStructuralError("El preview quedó obsoleto. Vuelva a previsualizar la operación.")
        keys = {group["key"] for group in plan["groups"]}
        if set(resolutions) - keys:
            raise SourceStructuralError("Resolución para un destino ajeno a la operación.")
        for group in plan["groups"]:
            choice = resolutions.get(group["key"])
            if choice not in (None, "", "expand", "clear") or (group["conflicts"] and choice not in ("expand", "clear")):
                raise SourceStructuralError("Resuelva los años fuera de rango de cada destino.")
        _, name = resolve_collaborator(db, actor.get("collaborator_id"))
        destinations, changes, cleared, moves, created = [], [], [], [], []
        for group in plan["groups"]:
            target = group["target"]
            if target["source_id"] is None:
                target = create_destination(db, target, name, actor)
                created.append(target["source_id"])
            target_id = target["source_id"]
            destinations.append({"source_id": target_id, "source_name": target["source_name"],
                                 "occurrence_ids": [o["occurrence_id"] for o in group["occurrences"]]})
            choice = resolutions.get(group["key"])
            if group["conflicts"] and choice == "expand":
                revision = change_period(db, target, group["expanded_period"], reason, name)
                changes.append({"source_id": target_id, "before": [target[k] for k in ("start_year", "end_year", "end_year_status")],
                                "after": group["expanded_period"], "source_revision_id": revision})
            conflict_ids = {o["occurrence_id"] for o in group["conflicts"]}
            for occurrence in group["occurrences"]:
                if occurrence["source_id"] == target_id:
                    continue
                year = occurrence["occurrence_year"]
                if choice == "clear" and occurrence["occurrence_id"] in conflict_ids:
                    cleared.append({"occurrence_id": occurrence["occurrence_id"], "previous_year": year})
                    year = None
                revision = move_occurrence(db, occurrence, target_id, year, reason, name)
                moves.append({"occurrence_id": occurrence["occurrence_id"], "source_id": occurrence["source_id"],
                              "destination_id": target_id, "occurrence_revision_id": revision})
        retired_revisions = []
        for source in plan["origins"]:
            if source["source_id"] in plan["retire_ids"]:
                retired_revisions.append(retire_empty_source(db, source, reason, name))
        payload = {"kind": plan["kind"], "mode": plan["mode"], "reason": reason,
                   "origins": [{"source_id": s["source_id"], "source_name": s["source_name"]} for s in plan["origins"]],
                   "destinations": destinations, "moves": moves, "created_source_ids": created,
                   "retired_source_ids": plan["retire_ids"], "retired_source_revision_ids": retired_revisions,
                   "period_changes": changes, "cleared_years": cleared,
                   "template_source_id": spec.get("template_source_id")}
        event = record_activity(db, "source_split" if plan["kind"] == "split" else "sources_merged",
                                entity_type="source", entity_id=plan["origins"][0]["source_id"],
                                access_role=actor["access_role"], collaborator_id=actor.get("collaborator_id"),
                                comment=json.dumps(payload, ensure_ascii=False, sort_keys=True))
        db.commit()
        return event
    except Exception:
        db.rollback()
        raise
