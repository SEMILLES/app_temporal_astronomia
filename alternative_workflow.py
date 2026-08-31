import sqlite3

from alternative_nomenclature import (
    InconclusiveNomenclatureError,
    InvalidNomenclatureError,
    apply_nomenclature,
    calculate_nomenclature_preview,
)
from alternative_relations import (
    DuplicateCurrentRelationError,
    SelfRelationError,
    create_current_relation,
    current_relation,
)
from assignments import create_or_replace_assignment
from concept_labels import normalize_concept_label
from phonological_parameters import validate_phonological_parameter
from alternative_morphology import store_submission_morphology,materialize_submission_morphology
from activity import record_activity


class AlternativeWorkflowError(ValueError):
    pass


def _transaction(connection, name):
    owns = not connection.in_transaction
    connection.execute("BEGIN IMMEDIATE" if owns else f"SAVEPOINT {name}")
    return owns


def _finish(connection, name, owns):
    connection.commit() if owns else connection.execute(f"RELEASE SAVEPOINT {name}")


def _rollback(connection, name, owns):
    if owns: connection.rollback()
    else:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")


def _context_reference(connection, occurrence_id):
    row = connection.execute("""
        SELECT concept_id,concept_proposal_id FROM occurrence_concept_reference
        WHERE occurrence_id=? AND is_current=1
    """, (occurrence_id,)).fetchone()
    if row is None:
        raise AlternativeWorkflowError("La occurrence no tiene referencia conceptual current.")
    return row[0], row[1]


def _context_concept(connection, concept_id, proposal_id):
    if concept_id is not None:
        return concept_id
    if proposal_id is None:
        return None
    row = connection.execute(
        "SELECT status,resolved_concept_id FROM concept_proposal WHERE concept_proposal_id=?",
        (proposal_id,),
    ).fetchone()
    return row[1] if row is not None and row[0] == "resolved" else None


def _valid_alternative(connection, alternative_id, concept_id):
    row = connection.execute(
        "SELECT concept_id,retired_at FROM alternative WHERE alternative_id=?",
        (alternative_id,),
    ).fetchone()
    return row is not None and row[1] is None and row[0] == concept_id


def _validate_relation_target(connection, relation, submission_id=None):
    alternative_id = relation.get("target_alternative_id")
    target_submission_id = relation.get("target_submission_id")
    if (alternative_id is None) == (target_submission_id is None):
        raise AlternativeWorkflowError("Cada relación debe tener exactamente un destino.")
    if target_submission_id is not None:
        if submission_id is not None and int(target_submission_id) == int(submission_id):
            raise AlternativeWorkflowError("Una submission no puede relacionarse consigo misma.")
        target = connection.execute("""
            SELECT s.status,s.submission_type,a.proposal_kind
            FROM submission s JOIN alternative_submission a USING(submission_id)
            WHERE s.submission_id=?
        """, (target_submission_id,)).fetchone()
        if target is None or tuple(target) != ("pending", "ALTERNATIVE", "NEW"):
            raise AlternativeWorkflowError("La submission destino no es una propuesta NEW pending.")
    else:
        target = connection.execute(
            "SELECT retired_at FROM alternative WHERE alternative_id=?", (alternative_id,)
        ).fetchone()
        if target is None or target[0] is not None:
            raise AlternativeWorkflowError("La alternative destino no está vigente.")
    return (
        int(alternative_id) if alternative_id is not None else None,
        int(target_submission_id) if target_submission_id is not None else None,
        validate_phonological_parameter(relation.get("phonological_parameter")),
        1 if relation.get("uncertain") else 0,
    )


def create_alternative_submission(connection, occurrence_id, proposal_kind, *,
                                  proposed_existing_alternative_id=None,
                                  phonological_relation_answer=None,
                                  relations=(), analysis_note=None,
                                  submitted_by=None,morphology=None,
                                  collaborator_id=None,access_role=None):
    proposal_kind = (proposal_kind or "").upper()
    if proposal_kind not in ("EXISTING", "NEW", "UNSURE"):
        raise AlternativeWorkflowError("Tipo de propuesta no válido.")
    concept_id, proposal_id = _context_reference(connection, occurrence_id)
    resolved_concept = _context_concept(connection, concept_id, proposal_id)
    note = (analysis_note or "").strip() or None
    if proposal_kind == "EXISTING":
        if proposed_existing_alternative_id is None:
            raise AlternativeWorkflowError("Debe seleccionar una alternative existente.")
        if resolved_concept is None or not _valid_alternative(connection, int(proposed_existing_alternative_id), resolved_concept):
            raise AlternativeWorkflowError("La alternative no pertenece al contexto conceptual vigente.")
    elif proposed_existing_alternative_id is not None:
        raise AlternativeWorkflowError("Solo EXISTING puede proponer una alternative existente.")
    answer = (phonological_relation_answer or "").upper() or None
    if proposal_kind == "NEW" and answer not in ("YES", "NO", "UNSURE"):
        raise AlternativeWorkflowError("Debe responder sobre la relación fonológica.")
    if proposal_kind == "UNSURE" and note is None:
        raise AlternativeWorkflowError("Una propuesta incierta exige una nota de análisis.")
    if proposal_kind == "EXISTING": answer = None
    validated = [_validate_relation_target(connection, relation) for relation in relations]
    for target_alternative_id, target_submission_id, _, _ in validated:
        if target_alternative_id is not None and resolved_concept is not None:
            target_concept = connection.execute(
                "SELECT concept_id FROM alternative WHERE alternative_id=?",
                (target_alternative_id,),
            ).fetchone()[0]
            if target_concept != resolved_concept:
                raise AlternativeWorkflowError("La relación propuesta sale del contexto conceptual.")
        if target_submission_id is not None:
            target_context = connection.execute(
                "SELECT reference_concept_id,reference_concept_proposal_id "
                "FROM alternative_submission WHERE submission_id=?",
                (target_submission_id,),
            ).fetchone()
            target_resolved = _context_concept(
                connection, target_context[0], target_context[1]
            )
            same_context = (
                target_resolved == resolved_concept
                if target_resolved is not None and resolved_concept is not None
                else tuple(target_context) == (concept_id, proposal_id)
            )
            if not same_context:
                raise AlternativeWorkflowError("La submission destino pertenece a otro contexto conceptual.")
    keys = [(a, s, p) for a, s, p, _ in validated]
    if len(keys) != len(set(keys)):
        raise AlternativeWorkflowError("Hay una relación propuesta duplicada.")
    if proposal_kind == "NEW" and answer == "YES" and not validated:
        raise AlternativeWorkflowError("La respuesta SÍ exige al menos una relación.")
    name = "create_alternative_submission"; owns = _transaction(connection, name)
    try:
        cursor = connection.execute(
            "INSERT INTO submission(occurrence_id,submission_type,status,submitted_by) VALUES(?,'ALTERNATIVE','pending',?)",
            (occurrence_id, submitted_by),
        )
        submission_id = cursor.lastrowid
        connection.execute("""
            INSERT INTO alternative_submission(
                submission_id,proposal_kind,reference_concept_id,
                reference_concept_proposal_id,proposed_existing_alternative_id,
                phonological_relation_answer,analysis_note,is_legacy
            ) VALUES(?,?,?,?,?,?,?,0)
        """, (submission_id, proposal_kind, concept_id, proposal_id,
              proposed_existing_alternative_id, answer, note))
        if morphology is not None:
            store_submission_morphology(connection,submission_id,**morphology)
        for alternative_id, target_id, parameter, uncertain in validated:
            if target_id == submission_id:
                raise AlternativeWorkflowError("Una submission no puede relacionarse consigo misma.")
            connection.execute("""
                INSERT INTO alternative_submission_relation(
                    submission_id,target_alternative_id,target_submission_id,
                    phonological_parameter,uncertain
                ) VALUES(?,?,?,?,?)
            """, (submission_id, alternative_id, target_id, parameter, uncertain))
        if access_role:
            record_activity(connection,"alternative_submission_created",
                            entity_type="submission",entity_id=submission_id,
                            collaborator_id=collaborator_id,access_role=access_role)
        _finish(connection, name, owns)
        return submission_id
    except sqlite3.IntegrityError as error:
        _rollback(connection, name, owns)
        raise AlternativeWorkflowError("Ya existe una propuesta ALTERNATIVE pending o una relación duplicada.") from error
    except Exception:
        _rollback(connection, name, owns); raise


def _submission(connection, submission_id):
    row = connection.execute("""
        SELECT s.*,a.* FROM submission s JOIN alternative_submission a USING(submission_id)
        WHERE s.submission_id=? AND s.submission_type='ALTERNATIVE' AND s.status='pending'
    """, (submission_id,)).fetchone()
    if row is None:
        raise AlternativeWorkflowError("La submission ALTERNATIVE no está pendiente.")
    return row


def _resolve_concept(connection, submission, resolution):
    direct = submission["reference_concept_id"]
    proposal_id = submission["reference_concept_proposal_id"]
    if direct is not None: return direct
    if proposal_id is None:
        selected = (resolution or {}).get("concept_id")
        if selected is None or connection.execute("SELECT 1 FROM concept WHERE concept_id=?", (selected,)).fetchone() is None:
            raise AlternativeWorkflowError("La submission legacy requiere un concept canónico explícito.")
        connection.execute("UPDATE alternative_submission SET reference_concept_id=? WHERE submission_id=?", (selected, submission["submission_id"]))
        return int(selected)
    proposal = connection.execute("SELECT * FROM concept_proposal WHERE concept_proposal_id=?", (proposal_id,)).fetchone()
    if proposal["status"] == "resolved": return proposal["resolved_concept_id"]
    action = (resolution or {}).get("action")
    if action == "existing":
        concept_id = (resolution or {}).get("concept_id")
        if connection.execute("SELECT 1 FROM concept WHERE concept_id=?", (concept_id,)).fetchone() is None:
            raise AlternativeWorkflowError("El concept seleccionado no existe.")
    elif action == "new":
        label = normalize_concept_label((resolution or {}).get("label") or proposal["proposed_label"])
        if connection.execute("SELECT 1 FROM concept WHERE preferred_label=?", (label,)).fetchone():
            raise AlternativeWorkflowError("Ese concept ya existe; resuelva contra el existente.")
        concept_id = connection.execute("INSERT INTO concept(preferred_label) VALUES(?)", (label,)).lastrowid
    elif action == "reject":
        concept_id = (resolution or {}).get("concept_id")
        if connection.execute("SELECT 1 FROM concept WHERE concept_id=?", (concept_id,)).fetchone() is None:
            raise AlternativeWorkflowError("Al rechazar la proposal debe elegir un concept canónico para aceptar el análisis.")
        connection.execute("UPDATE concept_proposal SET status='rejected',resolved_concept_id=NULL,resolved_at=CURRENT_TIMESTAMP WHERE concept_proposal_id=? AND status='pending'", (proposal_id,))
        return int(concept_id)
    else:
        raise AlternativeWorkflowError("Debe resolver la concept proposal antes de aceptar.")
    connection.execute("UPDATE concept_proposal SET status='resolved',resolved_concept_id=?,resolved_at=CURRENT_TIMESTAMP WHERE concept_proposal_id=? AND status='pending'", (concept_id, proposal_id))
    return int(concept_id)


def _proposed_relations(connection, submission_id):
    return connection.execute("SELECT * FROM alternative_submission_relation WHERE submission_id=? ORDER BY alternative_submission_relation_id", (submission_id,)).fetchall()


def _relation_targets(connection, submission_id):
    targets = []
    for relation in _proposed_relations(connection, submission_id):
        target = relation["target_alternative_id"]
        if target is None:
            resolved = connection.execute("""
                SELECT s.status,s.resolution,a.resolved_alternative_id
                FROM submission s JOIN alternative_submission a USING(submission_id)
                WHERE s.submission_id=?
            """, (relation["target_submission_id"],)).fetchone()
            if resolved is None or tuple(resolved[:2]) != ("resolved", "accepted") or resolved[2] is None:
                raise AlternativeWorkflowError("Una relación apunta a una submission sin alternative resuelta.")
            target = resolved[2]
        targets.append((target, relation["phonological_parameter"]))
    return targets


def _materialize_relations(connection, source_id, submission_id):
    for target_id, parameter in _relation_targets(connection, submission_id):
        if target_id == source_id:
            raise AlternativeWorkflowError("No puede aprobarse una self-relation.")
        pair = connection.execute("""
            SELECT source.concept_id,target.concept_id,target.retired_at
            FROM alternative source JOIN alternative target
            WHERE source.alternative_id=? AND target.alternative_id=?
        """, (source_id, target_id)).fetchone()
        if pair is None or pair[2] is not None or pair[0] != pair[1]:
            raise AlternativeWorkflowError(
                "La relación propuesta ya no tiene un destino vigente del mismo concept."
            )
        if current_relation(connection, source_id, target_id, parameter) is None:
            create_current_relation(connection, source_id, target_id, parameter, created_from_submission_id=submission_id)


def _resolve_submission(connection, submission_id, alternative_id, reviewer, note):
    connection.execute("UPDATE alternative_submission SET resolved_alternative_id=? WHERE submission_id=?", (alternative_id, submission_id))
    connection.execute("""UPDATE submission SET status='resolved',resolution='accepted',resolved_at=CURRENT_TIMESTAMP,reviewed_by=?,review_note=? WHERE submission_id=?""", (reviewer, (note or "").strip() or None, submission_id))


def _proposal_is_pending(connection, submission):
    proposal_id = submission["reference_concept_proposal_id"]
    if proposal_id is None: return False
    row = connection.execute("SELECT status FROM concept_proposal WHERE concept_proposal_id=?",(proposal_id,)).fetchone()
    return row is not None and row[0] == "pending"


def _record_concept_resolution(connection, submission, was_pending,
                               collaborator_id, access_role):
    proposal_id = submission["reference_concept_proposal_id"]
    if access_role and was_pending and proposal_id is not None:
        row = connection.execute(
            "SELECT status FROM concept_proposal WHERE concept_proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if row is not None and row[0] != "pending":
            record_activity(connection,"concept_proposal_resolved",
                            entity_type="concept_proposal",entity_id=proposal_id,
                            collaborator_id=collaborator_id,access_role=access_role)


def review_as_existing(connection, submission_id, alternative_id, *,
                       concept_resolution=None, relation_policy="preserve",
                       reviewed_by=None, review_note=None, collaborator_id=None,
                       access_role=None):
    name="review_alternative_existing"; owns=_transaction(connection,name)
    try:
        submission=_submission(connection,submission_id); proposal_was_pending=_proposal_is_pending(connection,submission); concept_id=_resolve_concept(connection,submission,concept_resolution)
        if not _valid_alternative(connection,int(alternative_id),concept_id):
            raise AlternativeWorkflowError("La alternative seleccionada no pertenece al concept resuelto o está retirada.")
        if relation_policy not in ("preserve","union"):
            raise AlternativeWorkflowError("Política de relaciones no válida.")
        if relation_policy == "union": _materialize_relations(connection,int(alternative_id),submission_id)
        create_or_replace_assignment(connection,submission["occurrence_id"],int(alternative_id),created_by=reviewed_by,created_from_submission_id=submission_id)
        _resolve_submission(connection,submission_id,int(alternative_id),reviewed_by,review_note)
        if access_role:
            _record_concept_resolution(connection,submission,proposal_was_pending,collaborator_id,access_role)
            record_activity(connection,"assignment_created_or_replaced",entity_type="occurrence",entity_id=submission["occurrence_id"],collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"alternative_submission_accepted",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
        _finish(connection,name,owns); return int(alternative_id)
    except Exception: _rollback(connection,name,owns); raise


def review_as_new(connection, submission_id, *, concept_resolution=None,
                  approve_relations=False, nomenclature_mode="automatic",
                  labels=None, reason=None, reviewed_by=None, review_note=None,
                  approve_morphology=False, collaborator_id=None, access_role=None):
    name="review_alternative_new"; owns=_transaction(connection,name)
    try:
        submission=_submission(connection,submission_id); proposal_was_pending=_proposal_is_pending(connection,submission); concept_id=_resolve_concept(connection,submission,concept_resolution)
        new_id=connection.execute("INSERT INTO alternative(concept_id,working_label) VALUES(?,NULL)",(concept_id,)).lastrowid
        targets=_relation_targets(connection,submission_id) if approve_relations else []
        edges=[(new_id,target) for target,_ in targets if target != new_id]
        preview=calculate_nomenclature_preview(connection,concept_id,extra_edges=edges,occurrence_overrides={new_id:submission["occurrence_id"]})
        supplied={
            (new_id if str(key) == "new" else int(key)): value
            for key, value in (labels or {}).items()
        }
        if nomenclature_mode == "automatic":
            if not preview["conclusive"]: raise InconclusiveNomenclatureError("El cálculo no es concluyente; asigne labels manualmente.")
            final=preview["suggestions"]; origin="automatic_assisted"
            event_reason=reason or "Reordenamiento cronológico asociado a aprobación de alternativa/relación."
        elif nomenclature_mode in ("manual","adjusted"):
            final=supplied; origin="manual"; event_reason=reason
            if nomenclature_mode == "adjusted" and preview["conclusive"] and final == preview["suggestions"]:
                origin="automatic_assisted"; event_reason=reason or "Reordenamiento cronológico asociado a aprobación de alternativa/relación."
        else: raise InvalidNomenclatureError("Modo de nomenclatura no válido.")
        renumber_id=apply_nomenclature(connection,concept_id,final,origin=origin,reason=event_reason,submission_id=submission_id,created_by=reviewed_by,required_edges=edges)
        if approve_relations: _materialize_relations(connection,new_id,submission_id)
        create_or_replace_assignment(connection,submission["occurrence_id"],new_id,created_by=reviewed_by,created_from_submission_id=submission_id)
        if approve_morphology:
            materialize_submission_morphology(
                connection, submission_id, new_id, created_by=reviewed_by
            )
        _resolve_submission(connection,submission_id,new_id,reviewed_by,review_note)
        if access_role:
            _record_concept_resolution(connection,submission,proposal_was_pending,collaborator_id,access_role)
            record_activity(connection,"alternative_created",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"renumber_event_created",entity_type="renumber_event",entity_id=renumber_id,collaborator_id=collaborator_id,access_role=access_role)
            for target_id, _ in targets:
                record_activity(connection,"alternative_relation_created",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"alternative_submission_accepted",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
            record_activity(connection,"assignment_created_or_replaced",entity_type="occurrence",entity_id=submission["occurrence_id"],collaborator_id=collaborator_id,access_role=access_role)
            if approve_morphology: record_activity(connection,"alternative_morphology_created_or_replaced",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
        _finish(connection,name,owns); return new_id
    except Exception: _rollback(connection,name,owns); raise


def reject_alternative_submission(connection, submission_id, *, reviewed_by=None,
                                  review_note=None, collaborator_id=None, access_role=None):
    name="reject_alternative"; owns=_transaction(connection,name)
    try:
        _submission(connection,submission_id)
        connection.execute("UPDATE submission SET status='resolved',resolution='rejected',resolved_at=CURRENT_TIMESTAMP,reviewed_by=?,review_note=? WHERE submission_id=?",(reviewed_by,(review_note or "").strip() or None,submission_id))
        if access_role: record_activity(connection,"alternative_submission_rejected",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
        _finish(connection,name,owns)
    except Exception: _rollback(connection,name,owns); raise
