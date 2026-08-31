from dataclasses import dataclass
import re

from alternative_nomenclature import connected_components


@dataclass(frozen=True, order=True)
class ConflictSubject:
    subject_type: str
    subject_id: int
    subject_role: str = "subject"


@dataclass(frozen=True)
class ConflictFinding:
    rule_code: str
    severity: str
    description: str
    subjects: tuple

    @property
    def subject_signature(self):
        return subject_signature(self.subjects)


@dataclass(frozen=True)
class RuleDefinition:
    code: str
    severity: str
    validator_condition: str
    detector: object
    scopes: frozenset


def subject_signature(subjects):
    normalized=sorted(ConflictSubject(s.subject_type,int(s.subject_id),s.subject_role) for s in subjects)
    if not normalized: raise ValueError("Un conflicto exige al menos un subject.")
    return "|".join(f"{s.subject_type}:{s.subject_id}:{s.subject_role}" for s in normalized)


def _finding(code,severity,description,subjects):
    return ConflictFinding(code,severity,description,tuple(sorted(subjects)))


def detect_duplicate_working_label(connection, **scope):
    params=[]; where="a.retired_at IS NULL AND length(trim(coalesce(a.working_label,'')))>0"
    if scope.get("concept_id") is not None: where+=" AND a.concept_id=?";params.append(scope["concept_id"])
    rows=connection.execute(f"""SELECT a.concept_id,a.working_label,group_concat(a.alternative_id),count(*)
        FROM alternative a WHERE {where} GROUP BY a.concept_id,a.working_label HAVING count(*)>1""",params).fetchall()
    return [_finding("DUPLICATE_WORKING_LABEL","blocking",
        f"El concept {r[0]} tiene la working_label duplicada {r[1]!r}.",
        [ConflictSubject("concept",r[0],"context"),*[ConflictSubject("alternative",i,"member") for i in map(int,r[2].split(','))]]) for r in rows]


def detect_active_relation_to_retired(connection, **scope):
    where="r.is_current=1 AND (lo.retired_at IS NOT NULL OR hi.retired_at IS NOT NULL)";params=[]
    if scope.get("alternative_id") is not None:
        where+=" AND (r.alternative_low_id=? OR r.alternative_high_id=?)";params += [scope["alternative_id"]]*2
    rows=connection.execute(f"""SELECT r.alternative_relation_id,r.alternative_low_id,r.alternative_high_id
      FROM alternative_relation r JOIN alternative lo ON lo.alternative_id=r.alternative_low_id
      JOIN alternative hi ON hi.alternative_id=r.alternative_high_id WHERE {where}""",params).fetchall()
    return [_finding("ACTIVE_RELATION_TO_RETIRED_ALTERNATIVE","blocking",
      "Una relación current conserva al menos un endpoint retirado.",
      (ConflictSubject("alternative_relation",r[0],"relation"),ConflictSubject("alternative",r[1],"endpoint"),ConflictSubject("alternative",r[2],"endpoint"))) for r in rows]


def detect_current_assignment_to_retired(connection, **scope):
    where="s.is_current=1 AND a.retired_at IS NOT NULL";params=[]
    if scope.get("occurrence_id") is not None: where+=" AND s.occurrence_id=?";params.append(scope["occurrence_id"])
    if scope.get("alternative_id") is not None: where+=" AND s.alternative_id=?";params.append(scope["alternative_id"])
    rows=connection.execute(f"""SELECT s.assignment_id,s.occurrence_id,s.alternative_id FROM assignment s
      JOIN alternative a USING(alternative_id) WHERE {where}""",params).fetchall()
    return [_finding("CURRENT_ASSIGNMENT_TO_RETIRED_ALTERNATIVE","blocking",
      "Una asignación current apunta a una alternative retirada.",
      (ConflictSubject("assignment",r[0],"assignment"),ConflictSubject("occurrence",r[1],"occurrence"),ConflictSubject("alternative",r[2],"target"))) for r in rows]


def detect_missing_working_label(connection, **scope):
    where="retired_at IS NULL AND length(trim(coalesce(working_label,'')))=0";params=[]
    if scope.get("concept_id") is not None: where+=" AND concept_id=?";params.append(scope["concept_id"])
    if scope.get("alternative_id") is not None: where+=" AND alternative_id=?";params.append(scope["alternative_id"])
    rows=connection.execute(f"SELECT alternative_id,concept_id FROM alternative WHERE {where}",params).fetchall()
    return [_finding("MISSING_WORKING_LABEL_ACTIVE_ALTERNATIVE","blocking",
      "Una alternative vigente no tiene working_label utilizable.",
      (ConflictSubject("alternative",r[0],"alternative"),ConflictSubject("concept",r[1],"context"))) for r in rows]


def detect_invalid_phonological_group_labeling(connection, **scope):
    concept_ids=[scope["concept_id"]] if scope.get("concept_id") is not None else [r[0] for r in connection.execute("SELECT DISTINCT concept_id FROM alternative WHERE retired_at IS NULL")]
    findings=[]
    for concept_id in concept_ids:
        labels=dict(connection.execute("SELECT alternative_id,working_label FROM alternative WHERE concept_id=? AND retired_at IS NULL",(concept_id,)).fetchall())
        if not labels or any(not str(v or '').strip() for v in labels.values()) or len(set(labels.values())) != len(labels): continue
        edges=[tuple(r) for r in connection.execute("""SELECT r.alternative_low_id,r.alternative_high_id FROM alternative_relation r
          JOIN alternative lo ON lo.alternative_id=r.alternative_low_id JOIN alternative hi ON hi.alternative_id=r.alternative_high_id
          WHERE r.is_current=1 AND lo.concept_id=? AND hi.concept_id=? AND lo.retired_at IS NULL AND hi.retired_at IS NULL""",(concept_id,concept_id))]
        for component in connected_components(set(labels),edges):
            if len(component)<2: continue
            parsed=[re.fullmatch(r"([1-9][0-9]*)([a-z])",str(labels[i])) for i in component]
            if any(p is None for p in parsed) or len({p.group(1) for p in parsed}) != 1:
                findings.append(_finding("INVALID_PHONOLOGICAL_GROUP_LABELING","blocking",
                  f"Un componente fonológico del concept {concept_id} no comparte grupo numerado con letras.",
                  [ConflictSubject("concept",concept_id,"context"),*[ConflictSubject("alternative",i,"member") for i in component]]))
    return findings


def detect_pending_morphology(connection, **scope):
    where="s.submission_type='ALTERNATIVE' AND s.status='resolved' AND s.resolution='accepted' AND aus.proposal_kind='NEW' AND asm.submission_id IS NOT NULL AND aus.resolved_alternative_id IS NOT NULL AND am.alternative_morphology_id IS NULL";params=[]
    if scope.get("submission_id") is not None: where+=" AND s.submission_id=?";params.append(scope["submission_id"])
    if scope.get("alternative_id") is not None: where+=" AND aus.resolved_alternative_id=?";params.append(scope["alternative_id"])
    rows=connection.execute(f"""SELECT s.submission_id,aus.resolved_alternative_id FROM submission s
      JOIN alternative_submission aus USING(submission_id)
      JOIN alternative_submission_morphology asm USING(submission_id)
      LEFT JOIN alternative_morphology am ON am.created_from_submission_id=s.submission_id AND am.alternative_id=aus.resolved_alternative_id
      WHERE {where}""",params).fetchall()
    return [_finding("PENDING_MORPHOLOGY","non_blocking",
      "La morphology propuesta al aceptar esta submission NEW no fue materializada.",
      (ConflictSubject("submission",r[0],"source"),ConflictSubject("alternative",r[1],"resolved_alternative"))) for r in rows]


RULES = {
 r.code:r for r in (
 RuleDefinition("DUPLICATE_WORKING_LABEL","blocking","Ya no existen dos alternatives vigentes del mismo concept con esa working_label exacta.",detect_duplicate_working_label,frozenset(("alternative","concept","renumber_event"))),
 RuleDefinition("ACTIVE_RELATION_TO_RETIRED_ALTERNATIVE","blocking","La relación ya no es current o ambos endpoints están vigentes.",detect_active_relation_to_retired,frozenset(("alternative","alternative_relation"))),
 RuleDefinition("CURRENT_ASSIGNMENT_TO_RETIRED_ALTERNATIVE","blocking","La asignación ya no es current o su alternative está vigente.",detect_current_assignment_to_retired,frozenset(("alternative","assignment","occurrence"))),
 RuleDefinition("INVALID_PHONOLOGICAL_GROUP_LABELING","blocking","Cada componente fonológico vigente de 2+ alternatives comparte número de grupo y usa letras.",detect_invalid_phonological_group_labeling,frozenset(("alternative","alternative_relation","concept","renumber_event"))),
 RuleDefinition("MISSING_WORKING_LABEL_ACTIVE_ALTERNATIVE","blocking","La alternative fue retirada o tiene una working_label utilizable.",detect_missing_working_label,frozenset(("alternative","concept","renumber_event"))),
 RuleDefinition("PENDING_MORPHOLOGY","non_blocking","La morphology propuesta fue materializada desde la submission, o la condición dejó de aplicar.",detect_pending_morphology,frozenset(("submission","alternative","alternative_morphology"))),
 )
}

def detect_all(connection, **scope):
    return [finding for rule in RULES.values() for finding in rule.detector(connection,**scope)]

def validate_finding_resolved(connection, conflict):
    rule=RULES[conflict["rule_code"]]
    subjects=[ConflictSubject(*row) for row in connection.execute("SELECT subject_type,subject_id,subject_role FROM conflict_subject WHERE conflict_id=?",(conflict["conflict_id"],)).fetchall()]
    anchor_types={
        "DUPLICATE_WORKING_LABEL":{"concept"},
        "INVALID_PHONOLOGICAL_GROUP_LABELING":{"concept"},
        "ACTIVE_RELATION_TO_RETIRED_ALTERNATIVE":{"alternative_relation"},
        "CURRENT_ASSIGNMENT_TO_RETIRED_ALTERNATIVE":{"assignment"},
        "MISSING_WORKING_LABEL_ACTIVE_ALTERNATIVE":{"alternative"},
        "PENDING_MORPHOLOGY":{"submission"},
    }[rule.code]
    anchors={(s.subject_type,s.subject_id) for s in subjects if s.subject_type in anchor_types}
    remains=any(anchors & {(s.subject_type,s.subject_id) for s in f.subjects} for f in rule.detector(connection))
    return (not remains, None if not remains else rule.validator_condition)
