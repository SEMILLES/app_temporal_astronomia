"""Administrative orchestration for canonical alternative analysis.

This module deliberately composes the existing morphology, relation and
nomenclature services.  It owns the transaction whenever several canonical
objects must change together.
"""

from edit_concurrency import check_edit
from alternative_preconditions import check_state

from alternative_morphology import create_or_replace_alternative_morphology
from alternative_nomenclature import (
    apply_nomenclature, calculate_nomenclature_preview, validate_final_labels,
)
from alternative_relations import (
    create_current_relation, retire_current_relation, current_relation,
    RelationNotFoundError, DuplicateCurrentRelationError, SelfRelationError,
)
from phonological_parameters import validate_phonological_parameter
from activity import record_activity


_INTERNAL = object()

class AlternativeAdminError(ValueError):
    pass


def actor_name(connection, collaborator_id):
    if collaborator_id in (None, ""):
        return None
    try:
        row = connection.execute(
            "SELECT display_name FROM collaborator WHERE collaborator_id=? AND active=1",
            (int(collaborator_id),),
        ).fetchone()
    except (TypeError, ValueError):
        row = None
    return row[0] if row else None


def _active_alternative(connection, alternative_id):
    row = connection.execute(
        "SELECT alternative_id,concept_id,working_label,retired_at FROM alternative WHERE alternative_id=?",
        (alternative_id,),
    ).fetchone()
    if row is None or row["retired_at"] is not None:
        raise AlternativeAdminError("La alternativa no existe o está retirada.")
    return row


def _validate_relation_participants(connection, left_id, right_id):
    if int(left_id) == int(right_id):
        raise SelfRelationError("Una alternativa no puede relacionarse consigo misma.")
    left = _active_alternative(connection, int(left_id))
    right = _active_alternative(connection, int(right_id))
    if left["concept_id"] != right["concept_id"]:
        raise AlternativeAdminError("Las relaciones administrativas deben pertenecer al mismo concepto.")
    return left, right


def _blocking_ids(connection):
    return {row[0] for row in connection.execute(
        "SELECT conflict_id FROM conflict WHERE status='open' AND severity='blocking'"
    )}


def _reject_new_blocking(connection, before):
    new = _blocking_ids(connection) - before
    if new:
        # Canonical services run workflow detection at each intermediate step.
        # In a compound operation (relation then labels), discard findings that
        # existed only during that uncommitted intermediate state.
        from conflict_rules import detect_all
        final_signatures = {finding.subject_signature for finding in detect_all(connection)}
        transient = {row[0] for row in connection.execute(
            "SELECT conflict_id,subject_signature FROM conflict WHERE conflict_id IN (%s)"
            % ",".join("?" * len(new)), tuple(sorted(new))
        ) if row[1] not in final_signatures}
        if transient:
            marks = ",".join("?" * len(transient))
            connection.execute(f"DELETE FROM conflict_subject WHERE conflict_id IN ({marks})", tuple(sorted(transient)))
            connection.execute(f"DELETE FROM conflict WHERE conflict_id IN ({marks})", tuple(sorted(transient)))
            new -= transient
    if new:
        descriptions = [row[0] for row in connection.execute(
            "SELECT description FROM conflict WHERE conflict_id IN (%s) ORDER BY conflict_id"
            % ",".join("?" * len(new)), tuple(sorted(new))
        )]
        raise AlternativeAdminError("La operación crearía un conflicto bloqueante: " + "; ".join(descriptions))


def update_morphology(connection, alternative_id, values, actor, *, edit_token=_INTERNAL):
    connection.execute("BEGIN IMMEDIATE")
    try:
        if edit_token is not _INTERNAL:
            check_edit(connection, "morphology", alternative_id, edit_token)
        _active_alternative(connection, alternative_id)
        before = _blocking_ids(connection)
        morphology_id, changed = create_or_replace_alternative_morphology(
            connection, alternative_id, created_by=actor_name(connection, actor.get("collaborator_id")),
            created_from_submission_id=None, **values
        )
        if changed:
            _reject_new_blocking(connection, before)
            record_activity(connection, "alternative_morphology_updated",
                            entity_type="alternative", entity_id=alternative_id,
                            collaborator_id=actor.get("collaborator_id"),
                            access_role=actor["access_role"])
        connection.commit()
        return morphology_id, changed
    except Exception:
        connection.rollback()
        raise


def _preview_after_retire(connection, concept_id, relation_id):
    connection.execute("SAVEPOINT relation_preview")
    try:
        cursor = connection.execute(
            "UPDATE alternative_relation SET is_current=0 WHERE alternative_relation_id=? AND is_current=1",
            (relation_id,),
        )
        if cursor.rowcount != 1:
            raise RelationNotFoundError("La relación vigente no existe.")
        return calculate_nomenclature_preview(connection, concept_id)
    finally:
        connection.execute("ROLLBACK TO SAVEPOINT relation_preview")
        connection.execute("RELEASE SAVEPOINT relation_preview")


def relation_preview(connection, alternative_id, *, action, target_id=None,
                     parameter=None, relation_id=None):
    source = _active_alternative(connection, alternative_id)
    if action == "add":
        target = _active_alternative(connection, int(target_id))
        _validate_relation_participants(connection, alternative_id, target_id)
        parameter = validate_phonological_parameter(parameter)
        if current_relation(connection, alternative_id, int(target_id), parameter):
            raise DuplicateCurrentRelationError("La relación ya existe.")
        preview = calculate_nomenclature_preview(
            connection, source["concept_id"], extra_edges=((alternative_id, int(target_id)),)
        )
        relation = {"action": "add", "target_id": int(target_id), "parameter": parameter,
                    "relation_id": None}
    elif action == "retire":
        row = connection.execute(
            "SELECT * FROM alternative_relation WHERE alternative_relation_id=? AND is_current=1 "
            "AND (alternative_low_id=? OR alternative_high_id=?)", (relation_id, alternative_id, alternative_id)
        ).fetchone()
        if row is None:
            raise RelationNotFoundError("La relación vigente no existe.")
        _validate_relation_participants(connection, row["alternative_low_id"], row["alternative_high_id"])
        preview = _preview_after_retire(connection, source["concept_id"], int(relation_id))
        relation = {"action": "retire", "target_id": row["alternative_high_id"] if row["alternative_low_id"] == alternative_id else row["alternative_low_id"],
                    "parameter": row["phonological_parameter"], "relation_id": int(relation_id)}
    else:
        raise AlternativeAdminError("Acción de relación no válida.")
    preview["changes"] = [row for row in preview["rows"] if row["current_label"] != row["proposed_label"]]
    preview["relation"] = relation
    return preview


def apply_relation_change(connection, alternative_id, *, action, target_id=None,
                          parameter=None, relation_id=None, labels=None,
                          mode="automatic", reason=None, actor=None, state_token=_INTERNAL):
    connection.execute("BEGIN IMMEDIATE")
    try:
        if state_token is not _INTERNAL:
            spec = {"action": action, "target_id": int(target_id), "parameter": parameter,
                    "relation_id": int(relation_id) if relation_id else None}
            check_state(connection, alternative_id, spec, state_token)
        actor = actor or {}
        expected = relation_preview(connection, alternative_id, action=action, target_id=target_id,
                                    parameter=parameter, relation_id=relation_id)
        concept_id = _active_alternative(connection, alternative_id)["concept_id"]
        suggested = expected["suggestions"]
        final = suggested if labels is None else {int(key): value for key, value in labels.items()}
        changed_from_auto = final != suggested
        origin = "manual" if mode == "manual" or changed_from_auto else "automatic_assisted"
        if origin == "manual" and not (reason or "").strip():
            raise AlternativeAdminError("La nomenclatura manual o ajustada exige una razón.")
        before = _blocking_ids(connection)
        if action == "add":
            relation_pk = create_current_relation(connection, alternative_id, int(target_id),
                validate_phonological_parameter(parameter), created_by=actor_name(connection, actor.get("collaborator_id")))
            event_type = "alternative_relation_added"
        else:
            relation_pk = int(relation_id)
            retire_current_relation(connection, relation_pk)
            event_type = "alternative_relation_retired"
        event_id = apply_nomenclature(connection, concept_id, final, origin=origin,
                                      reason=reason, created_by=actor_name(connection, actor.get("collaborator_id")))
        _reject_new_blocking(connection, before)
        record_activity(connection, event_type, entity_type="alternative_relation", entity_id=relation_pk,
                        collaborator_id=actor.get("collaborator_id"), access_role=actor["access_role"], comment=reason)
        if event_id is not None:
            record_activity(connection, "alternative_nomenclature_updated", entity_type="renumber_event",
                            entity_id=event_id, collaborator_id=actor.get("collaborator_id"),
                            access_role=actor["access_role"], comment=reason)
        connection.commit()
        return relation_pk, event_id
    except Exception:
        connection.rollback()
        raise


def apply_direct_nomenclature(connection, concept_id, labels, *, mode, reason, actor,
                              state_token=_INTERNAL, source_id=None):
    connection.execute("BEGIN IMMEDIATE")
    try:
        if state_token is not _INTERNAL:
            check_state(connection, source_id, {"kind": "nomenclature"}, state_token)
        preview = calculate_nomenclature_preview(connection, concept_id)
        labels = preview["suggestions"] if labels is None else {int(k): v for k, v in labels.items()}
        origin = "automatic_assisted" if mode == "automatic" and labels == preview["suggestions"] else "manual"
        if origin == "manual" and not (reason or "").strip():
            raise AlternativeAdminError("La nomenclatura manual o ajustada exige una razón.")
        before = _blocking_ids(connection)
        event_id = apply_nomenclature(connection, concept_id, labels, origin=origin, reason=reason,
                                      created_by=actor_name(connection, actor.get("collaborator_id")))
        if event_id is not None:
            _reject_new_blocking(connection, before)
            record_activity(connection, "alternative_nomenclature_updated", entity_type="renumber_event",
                            entity_id=event_id, collaborator_id=actor.get("collaborator_id"),
                            access_role=actor["access_role"], comment=reason)
        connection.commit()
        return event_id
    except Exception:
        connection.rollback()
        raise
