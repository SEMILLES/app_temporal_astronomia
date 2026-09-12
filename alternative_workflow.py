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
from phonological_parameters import validate_phonological_parameter
from alternative_morphology import store_submission_morphology,materialize_submission_morphology
from activity import record_activity
from conflicts import detect_conflicts_after_change
from submission_concept_resolution import current_resolution
from submission_lexical_decision import save_decision, LexicalDecisionError


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


def comparable_pending_proposals(connection, occurrence_id):
    """Share the proposal service's conceptual comparability with comparison screens."""
    try:
        concept_id, proposal_id = _context_reference(connection, occurrence_id)
    except AlternativeWorkflowError:
        return []
    resolved = _context_concept(connection, concept_id, proposal_id)
    rows = connection.execute("""
        SELECT s.submission_id,o.occurrence_id,o.original_gloss,src.source_name,
               a.reference_concept_id,a.reference_concept_proposal_id
        FROM submission s JOIN alternative_submission a USING(submission_id)
        JOIN occurrence o USING(occurrence_id) JOIN source src USING(source_id)
        WHERE s.status='pending' AND s.submission_type='ALTERNATIVE'
          AND a.proposal_kind='NEW' AND s.occurrence_id!=?
        ORDER BY o.occurrence_id
    """, (occurrence_id,)).fetchall()
    def comparable(row):
        target = _context_concept(connection, row['reference_concept_id'], row['reference_concept_proposal_id'])
        return (target == resolved if target is not None and resolved is not None else
                (row['reference_concept_id'], row['reference_concept_proposal_id']) == (concept_id, proposal_id))
    return [row for row in rows if comparable(row)]


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
            raise AlternativeWorkflowError("El destino ya no es una propuesta de nueva alternativa pendiente. Actualice la comparación.")
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
            raise AlternativeWorkflowError("Seleccione una alternativa existente.")
        if resolved_concept is None or not _valid_alternative(connection, int(proposed_existing_alternative_id), resolved_concept):
            raise AlternativeWorkflowError("La alternative no pertenece al contexto conceptual vigente.")
    elif proposed_existing_alternative_id is not None:
        raise AlternativeWorkflowError("Solo EXISTING puede proponer una alternative existente.")
    answer = (phonological_relation_answer or "").upper() or None
    if proposal_kind == "NEW" and answer not in ("YES", "NO", "UNSURE"):
        raise AlternativeWorkflowError("La respuesta sobre la relación fonológica es obligatoria.")
    if proposal_kind == "UNSURE" and note is None:
        raise AlternativeWorkflowError("Una propuesta incierta exige una nota de análisis.")
    if proposal_kind == "NEW" and morphology is None:
        raise AlternativeWorkflowError(
            "Una propuesta NEW exige cantidad de componentes o N/A."
        )
    if proposal_kind == "EXISTING": answer = None
    if proposal_kind == "NEW" and answer == "YES":
        available = resolved_concept is not None and connection.execute(
            "SELECT 1 FROM alternative WHERE concept_id=? AND retired_at IS NULL", (resolved_concept,)).fetchone()
        if not available and not comparable_pending_proposals(connection, occurrence_id):
            raise AlternativeWorkflowError("No existen otras alternativas o propuestas comparables disponibles para registrar una relación fonológica.")
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
            if int(target_submission_id) not in {row['submission_id'] for row in comparable_pending_proposals(connection, occurrence_id)}:
                raise AlternativeWorkflowError("La propuesta destino no pertenece al contexto comparable disponible.")
    keys = [(a, s) for a, s, p, _ in validated]
    if len(keys) != len(set(keys)):
        raise AlternativeWorkflowError("Hay una relación propuesta duplicada: el destino está repetido. Cada destino admite un solo parámetro.")
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
            normalized_morphology = store_submission_morphology(
                connection,submission_id,**morphology
            )
            if (proposal_kind == "NEW"
                    and normalized_morphology["component_count"] is None
                    and not normalized_morphology["component_count_not_applicable"]):
                raise AlternativeWorkflowError(
                    "Una propuesta NEW exige cantidad de componentes o N/A."
                )
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


def _resolve_concept(connection, submission, resolution=None):
    from submission_concept_resolution import require_concept, ConceptResolutionError
    if resolution:
        raise AlternativeWorkflowError("Guarde la resolución del concepto mediante su acción independiente.")
    try:
        return require_concept(connection, submission["submission_id"])
    except ConceptResolutionError as error:
        raise AlternativeWorkflowError(str(error)) from error


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
            if resolved is not None and resolved[0] == "pending":
                raise AlternativeWorkflowError("Una de las relaciones apunta a una propuesta que todavía no ha sido resuelta. Para aceptar las relaciones, primero debe resolverse esa propuesta.")
            if resolved is None or tuple(resolved[:2]) != ("resolved", "accepted") or resolved[2] is None:
                raise AlternativeWorkflowError("Una relación apunta a una propuesta rechazada o sin alternativa resultante. Revise el destino.")
            target = resolved[2]
        if any(existing == target for existing, _ in targets):
            raise AlternativeWorkflowError("Dos destinos propuestos se resuelven a la misma alternativa. Revise explícitamente las relaciones antes de aceptarlas.")
        targets.append((target, relation["phonological_parameter"]))
    return targets


def _materialize_relations(connection, source_id, submission_id):
    for target_id, parameter in _relation_targets(connection, submission_id):
        if target_id == source_id:
            raise AlternativeWorkflowError("No puede aprobarse una autorrelación: el destino es la misma alternativa de origen.")
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


def _current_assignment_id(connection, occurrence_id):
    row = connection.execute(
        'SELECT assignment_id FROM assignment WHERE occurrence_id=? AND is_current=1',
        (occurrence_id,)).fetchone()
    return row[0] if row else None


def _rejected_group(connection, submission_id, table, decision=None, *, reject_rest=False):
    proposed = connection.execute(
        f'SELECT 1 FROM {table} WHERE submission_id=?', (submission_id,)).fetchone()
    expected = 'REJECTED' if proposed else 'NOT_PROPOSED'
    if reject_rest or (not proposed and decision is None):
        return expected
    if decision != expected:
        raise AlternativeWorkflowError(
            'Resuelva explícitamente los grupos propuestos como REJECTED antes de aceptar una alternativa existente.')
    return expected


def review_as_existing(connection, submission_id, alternative_id, *,
                       concept_resolution=None, relation_policy="preserve",
                       reviewed_by=None, review_note=None, collaborator_id=None,
                       access_role=None, relations_resolution=None, morphology_resolution=None):
    name="review_alternative_existing"; owns=_transaction(connection,name)
    try:
        submission=_submission(connection,submission_id); concept_id=_resolve_concept(connection,submission,concept_resolution)
        if not _valid_alternative(connection,int(alternative_id),concept_id):
            raise AlternativeWorkflowError("La alternative seleccionada no pertenece al concept resuelto o está retirada.")
        if relation_policy != "preserve":
            raise AlternativeWorkflowError("USE_EXISTING no permite union ni modificar relaciones del destino.")
        relations_resolution = _rejected_group(connection, submission_id,
            'alternative_submission_relation', relations_resolution)
        morphology_resolution = _rejected_group(connection, submission_id,
            'alternative_submission_morphology', morphology_resolution)
        before = _current_assignment_id(connection, submission['occurrence_id'])
        conflict_before = connection.execute('SELECT coalesce(max(conflict_id),0) FROM conflict').fetchone()[0]
        result, changed = create_or_replace_assignment(connection,submission["occurrence_id"],int(alternative_id),created_by=reviewed_by,created_from_submission_id=submission_id)
        save_decision(connection, submission_id, 'USE_EXISTING',
            concept_resolution_id=current_resolution(connection, submission_id)['submission_concept_resolution_id'],
            resolved_alternative_id=int(alternative_id), assignment_before_id=before,
            assignment_result_id=result,
            assignment_effect='CREATED' if before is None else ('REPLACED' if changed else 'REUSED'),
            relations_resolution=relations_resolution, morphology_resolution=morphology_resolution,
            collaborator_id=collaborator_id, access_role=access_role, review_note=review_note)
        _resolve_submission(connection,submission_id,int(alternative_id),reviewed_by,review_note)
        if access_role:
            record_activity(connection,"assignment_created_or_replaced",entity_type="occurrence",entity_id=submission["occurrence_id"],collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"alternative_submission_accepted",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
        detect_conflicts_after_change(connection,"submission",submission_id,
            actor_context={"collaborator_id":collaborator_id,"access_role":access_role})
        if not (review_note or '').strip() and connection.execute(
                "SELECT 1 FROM conflict WHERE conflict_id>? AND severity='blocking'", (conflict_before,)).fetchone():
            raise AlternativeWorkflowError('La aprobación genera conflictos bloqueantes; explique la decisión en la nota de revisión.')
        _finish(connection,name,owns); return int(alternative_id)
    except LexicalDecisionError as error:
        _rollback(connection,name,owns)
        raise AlternativeWorkflowError(str(error)) from error
    except Exception: _rollback(connection,name,owns); raise


def _new_group_resolution(connection, submission_id, table, decision, approval):
    """Keep explicit boolean callers compatible; omission never rejects a group."""
    proposed = connection.execute(
        f'SELECT 1 FROM {table} WHERE submission_id=?', (submission_id,)).fetchone()
    if approval is not None and not isinstance(approval, bool):
        raise AlternativeWorkflowError('La aprobación del grupo no es válida.')
    if not proposed:
        if decision not in (None, 'NOT_PROPOSED') or approval is True:
            raise AlternativeWorkflowError('No existe propuesta para ese grupo.')
        return 'NOT_PROPOSED'
    boolean_decision = None if approval is None else ('ACCEPTED' if approval else 'REJECTED')
    if decision is None:
        decision = boolean_decision
    elif boolean_decision is not None and decision != boolean_decision:
        raise AlternativeWorkflowError('Las decisiones del grupo son contradictorias.')
    if decision not in ('ACCEPTED', 'REJECTED'):
        raise AlternativeWorkflowError('Resuelva explícitamente cada grupo propuesto como ACCEPTED o REJECTED.')
    return decision


def review_as_new(connection, submission_id, *, concept_resolution=None,
                  approve_relations=None, nomenclature_mode="automatic",
                  labels=None, reason=None, reviewed_by=None, review_note=None,
                  approve_morphology=None, collaborator_id=None, access_role=None,
                  relations_resolution=None, morphology_resolution=None):
    name="review_alternative_new"; owns=_transaction(connection,name)
    try:
        submission=_submission(connection,submission_id); concept_id=_resolve_concept(connection,submission,concept_resolution)
        relations_resolution = _new_group_resolution(connection, submission_id,
            'alternative_submission_relation', relations_resolution, approve_relations)
        morphology_resolution = _new_group_resolution(connection, submission_id,
            'alternative_submission_morphology', morphology_resolution, approve_morphology)
        approve_relations = relations_resolution == 'ACCEPTED'
        approve_morphology = morphology_resolution == 'ACCEPTED'
        before = _current_assignment_id(connection, submission['occurrence_id'])
        conflict_before = connection.execute('SELECT coalesce(max(conflict_id),0) FROM conflict').fetchone()[0]
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
        result, _ = create_or_replace_assignment(connection,submission["occurrence_id"],new_id,created_by=reviewed_by,created_from_submission_id=submission_id)
        morphology_id = None
        if approve_morphology:
            morphology_id, _ = materialize_submission_morphology(
                connection, submission_id, new_id, created_by=reviewed_by
            )
        save_decision(connection, submission_id, 'CREATE_NEW',
            concept_resolution_id=current_resolution(connection, submission_id)['submission_concept_resolution_id'],
            resolved_alternative_id=new_id, assignment_before_id=before,
            assignment_result_id=result, assignment_effect='CREATED' if before is None else 'REPLACED',
            relations_resolution=relations_resolution, morphology_resolution=morphology_resolution,
            morphology_result_id=morphology_id, collaborator_id=collaborator_id,
            access_role=access_role, review_note=review_note)
        _resolve_submission(connection,submission_id,new_id,reviewed_by,review_note)
        if access_role:
            record_activity(connection,"alternative_created",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"renumber_event_created",entity_type="renumber_event",entity_id=renumber_id,collaborator_id=collaborator_id,access_role=access_role)
            for target_id, _ in targets:
                record_activity(connection,"alternative_relation_created",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
            record_activity(connection,"alternative_submission_accepted",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
            record_activity(connection,"assignment_created_or_replaced",entity_type="occurrence",entity_id=submission["occurrence_id"],collaborator_id=collaborator_id,access_role=access_role)
            if approve_morphology: record_activity(connection,"alternative_morphology_created_or_replaced",entity_type="alternative",entity_id=new_id,collaborator_id=collaborator_id,access_role=access_role)
        detect_conflicts_after_change(connection,"submission",submission_id,
            actor_context={"collaborator_id":collaborator_id,"access_role":access_role})
        detect_conflicts_after_change(connection,"alternative",new_id,
            actor_context={"collaborator_id":collaborator_id,"access_role":access_role})
        if not (review_note or '').strip() and connection.execute(
                "SELECT 1 FROM conflict WHERE conflict_id>? AND severity='blocking'", (conflict_before,)).fetchone():
            raise AlternativeWorkflowError('La aprobación genera conflictos bloqueantes; explique la decisión en la nota de revisión.')
        _finish(connection,name,owns); return new_id
    except LexicalDecisionError as error:
        _rollback(connection,name,owns)
        raise AlternativeWorkflowError(str(error)) from error
    except Exception: _rollback(connection,name,owns); raise


def reject_alternative_submission(connection, submission_id, *, reviewed_by=None,
                                  review_note=None, collaborator_id=None, access_role=None):
    name="reject_alternative"; owns=_transaction(connection,name)
    try:
        submission = _submission(connection,submission_id)
        _resolve_concept(connection, submission)
        before = _current_assignment_id(connection, submission['occurrence_id'])
        save_decision(connection, submission_id, 'REJECT_REST',
            concept_resolution_id=current_resolution(connection, submission_id)['submission_concept_resolution_id'],
            assignment_before_id=before, assignment_result_id=before, assignment_effect='UNCHANGED',
            relations_resolution=_rejected_group(connection, submission_id, 'alternative_submission_relation', reject_rest=True),
            morphology_resolution=_rejected_group(connection, submission_id, 'alternative_submission_morphology', reject_rest=True),
            collaborator_id=collaborator_id, access_role=access_role, review_note=review_note)
        connection.execute('UPDATE alternative_submission SET resolved_alternative_id=NULL WHERE submission_id=?', (submission_id,))
        connection.execute("UPDATE submission SET status='resolved',resolution='rejected',resolved_at=CURRENT_TIMESTAMP,reviewed_by=?,review_note=? WHERE submission_id=?",(reviewed_by,(review_note or "").strip() or None,submission_id))
        if access_role: record_activity(connection,"alternative_submission_rejected",entity_type="submission",entity_id=submission_id,collaborator_id=collaborator_id,access_role=access_role,comment=review_note)
        _finish(connection,name,owns)
    except LexicalDecisionError as error:
        _rollback(connection,name,owns)
        raise AlternativeWorkflowError(str(error)) from error
    except Exception: _rollback(connection,name,owns); raise
