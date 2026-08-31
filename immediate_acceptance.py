from alternative_workflow import (create_alternative_submission,
    review_as_existing,review_as_new)
from grammar_workflow import create_grammar_submission,resolve_grammar_submission
from occurrence_registration import complete_registration
from concept_labels import normalize_concept_label
from activity import record_activity
from conflict_presentation import format_subject


class ImmediateAcceptanceError(ValueError): pass
class ImmediateBlockingError(ImmediateAcceptanceError):
    def __init__(self,conflicts):
        self.conflicts=conflicts
        super().__init__("No se puede aceptar inmediatamente porque esta decisión generaría conflictos que bloquean la publicación.")


def _new_conflicts(connection,before_id):
    rows=connection.execute("SELECT * FROM conflict WHERE conflict_id>? ORDER BY conflict_id",(before_id,)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        subjects=connection.execute("SELECT subject_type,subject_id FROM conflict_subject WHERE conflict_id=? ORDER BY conflict_subject_id",(row["conflict_id"],)).fetchall()
        item["subject_labels"]=[format_subject(connection,s[0],s[1]) for s in subjects]
        result.append(item)
    return result


def preview_operation(connection,operation):
    if connection.in_transaction:raise ImmediateAcceptanceError("El preview exige una conexión sin transacción activa.")
    before=connection.execute("SELECT coalesce(max(conflict_id),0) FROM conflict").fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        result=operation(connection)
        conflicts=_new_conflicts(connection,before)
        return {"result":result,"conflicts":conflicts,"blocking":[c for c in conflicts if c["severity"]=="blocking"],"non_blocking":[c for c in conflicts if c["severity"]=="non_blocking"]}
    finally:connection.rollback()


def confirm_operation(connection,operation):
    if connection.in_transaction:raise ImmediateAcceptanceError("La confirmación exige una conexión sin transacción activa.")
    before=connection.execute("SELECT coalesce(max(conflict_id),0) FROM conflict").fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        result=operation(connection);conflicts=_new_conflicts(connection,before)
        blocking=[c for c in conflicts if c["severity"]=="blocking"]
        if blocking:raise ImmediateBlockingError(blocking)
        connection.commit();return {"result":result,"conflicts":conflicts,"blocking":[],"non_blocking":[c for c in conflicts if c["severity"]=="non_blocking"]}
    except Exception:
        connection.rollback();raise


def grammar_operation(occurrence_id,values,*,actor_context,reviewed_by=None,review_note=None):
    def operation(connection):
        submission_id=create_grammar_submission(connection,occurrence_id,values,submitted_by=reviewed_by,collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        resolve_grammar_submission(connection,submission_id,"accepted",reviewed_by=reviewed_by,review_note=review_note,collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        return submission_id
    return operation


def alternative_operation(occurrence_id,proposal,decision,*,actor_context,reviewed_by=None,review_note=None):
    def operation(connection):
        submission_id=create_alternative_submission(connection,occurrence_id,proposal["proposal_kind"],proposed_existing_alternative_id=proposal.get("proposed_existing_alternative_id"),phonological_relation_answer=proposal.get("phonological_relation_answer"),relations=proposal.get("relations",()),analysis_note=proposal.get("analysis_note"),submitted_by=reviewed_by,morphology=proposal.get("morphology"),collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        canonical=decision.get("decision")
        concept_resolution=decision.get("concept_resolution")
        if canonical=="existing":
            alternative_id=review_as_existing(connection,submission_id,decision.get("alternative_id"),concept_resolution=concept_resolution,relation_policy=decision.get("relation_policy","preserve"),reviewed_by=reviewed_by,review_note=review_note,collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        elif canonical=="new":
            alternative_id=review_as_new(connection,submission_id,concept_resolution=concept_resolution,approve_relations=decision.get("approve_relations",False),nomenclature_mode=decision.get("nomenclature_mode","automatic"),labels=decision.get("labels"),reason=decision.get("nomenclature_reason"),reviewed_by=reviewed_by,review_note=review_note,approve_morphology=decision.get("approve_morphology",False),collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        else:raise ImmediateAcceptanceError("Debe decidir si resuelve como alternative existente o nueva.")
        return {"submission_id":submission_id,"alternative_id":alternative_id}
    return operation


def concept_registration_operation(evidence,proposed_label,decision,*,actor_context):
    def operation(connection):
        occurrence_id=complete_registration(connection,**evidence,proposed_label=proposed_label,force_new_proposal=True,collaborator_id=actor_context.get("collaborator_id"),access_role=actor_context.get("access_role"))
        proposal=connection.execute("""SELECT cp.concept_proposal_id,cp.proposed_label FROM occurrence_concept_reference r
          JOIN concept_proposal cp USING(concept_proposal_id) WHERE r.occurrence_id=? AND r.is_current=1""",(occurrence_id,)).fetchone()
        if proposal is None:raise ImmediateAcceptanceError("No se creó la propuesta conceptual histórica.")
        if decision.get("action")=="existing":
            concept_id=decision.get("concept_id")
            if connection.execute("SELECT 1 FROM concept WHERE concept_id=?",(concept_id,)).fetchone() is None:raise ImmediateAcceptanceError("El concept seleccionado no existe.")
        elif decision.get("action")=="new":
            label=normalize_concept_label(decision.get("label") or proposal["proposed_label"])
            if connection.execute("SELECT 1 FROM concept WHERE UPPER(preferred_label)=UPPER(?)",(label,)).fetchone():raise ImmediateAcceptanceError("Ese concept ya existe; resuelva hacia el existente.")
            concept_id=connection.execute("INSERT INTO concept(preferred_label) VALUES(?)",(label,)).lastrowid
        else:raise ImmediateAcceptanceError("Debe resolver la propuesta hacia un concept existente o crear uno nuevo.")
        connection.execute("UPDATE concept_proposal SET status='resolved',resolved_concept_id=?,resolved_at=CURRENT_TIMESTAMP WHERE concept_proposal_id=?",(concept_id,proposal["concept_proposal_id"]))
        role=actor_context.get("access_role")
        if role:record_activity(connection,"concept_proposal_resolved",entity_type="concept_proposal",entity_id=proposal["concept_proposal_id"],collaborator_id=actor_context.get("collaborator_id"),access_role=role)
        return {"occurrence_id":occurrence_id,"concept_id":concept_id,"concept_proposal_id":proposal["concept_proposal_id"]}
    return operation


def run_normal_review(connection,operation,review_note):
    if connection.in_transaction:raise ImmediateAcceptanceError("La revisión exige una conexión sin transacción activa.")
    before=connection.execute("SELECT coalesce(max(conflict_id),0) FROM conflict").fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        result=operation(connection);blocking=[dict(row) for row in connection.execute("SELECT * FROM conflict WHERE conflict_id>? AND severity='blocking'",(before,)).fetchall()]
        if blocking and not (review_note or "").strip():
            raise ImmediateAcceptanceError("Esta aprobación generaría conflictos bloqueantes. Debe explicar por qué desea aprobarla.")
        connection.commit();return result
    except Exception:connection.rollback();raise
