"""Read-only lexical simulation. Deliberately not wired into write workflows.

None destination means no assignment change (including rejection), not removal.
Only explicitly listed canonical relation removals retire relations.
"""
from dataclasses import dataclass, replace

from alternative_nomenclature import calculate_nomenclature_rows, temporal_reference


AlternativeRef = int | str


@dataclass(frozen=True)
class VirtualAlternative:
    concept_id: int
    virtual_order: int = 1
    working_label: str | None = None

    def __post_init__(self):
        if type(self.virtual_order) is not int or self.virtual_order < 1:
            raise ValueError("virtual_order must be a positive integer")

    @property
    def ref(self):
        return f"new_alternative:{self.virtual_order}"


@dataclass(frozen=True)
class Relation:
    ref: int | str
    left: AlternativeRef
    right: AlternativeRef
    parameter: str


@dataclass(frozen=True)
class ValidityChange:
    alternative_ref: AlternativeRef
    active: bool


@dataclass(frozen=True)
class LexicalOperation:
    occurrence_id: int
    expected_assignment_id: int | None
    destination: AlternativeRef | None = None
    destination_concept_id: int | None = None
    new_alternatives: tuple[VirtualAlternative, ...] = ()
    added_relations: tuple[Relation, ...] = ()
    removed_relation_ids: tuple[int, ...] = ()
    validity_changes: tuple[ValidityChange, ...] = ()


@dataclass(frozen=True)
class Alternative:
    ref: AlternativeRef
    concept_id: int
    working_label: str | None
    created_at: str | None
    active: bool
    virtual_order: int | None = None


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: int
    occurrence_year: int | None
    start_year: int | None
    end_year: int | None
    end_year_status: str | None
    source_name: str | None
    source_reference: str | None


@dataclass(frozen=True)
class Assignment:
    assignment_id: int | None
    occurrence_id: int
    alternative_ref: AlternativeRef


@dataclass(frozen=True)
class LexicalState:
    concepts: tuple[int, ...]
    alternatives: tuple[Alternative, ...]
    occurrences: tuple[Occurrence, ...]
    assignments: tuple[Assignment, ...]
    relations: tuple[Relation, ...]


@dataclass(frozen=True)
class ConceptState:
    concept_id: int
    lexical_state: LexicalState


def load_lexical_state(connection, operation):
    """Load whole relevant concepts, but no submissions, media or history."""
    current = connection.execute(
        "SELECT assignment_id,occurrence_id,alternative_id FROM assignment "
        "WHERE occurrence_id=? AND is_current=1", (operation.occurrence_id,)
    ).fetchone()
    refs = {current[2]} if current else set()
    if operation.destination is not None:
        refs.add(operation.destination)
    for relation in operation.added_relations:
        refs.update((relation.left, relation.right))
    refs.update(change.alternative_ref for change in operation.validity_changes)
    for relation_id in operation.removed_relation_ids:
        row = connection.execute(
            "SELECT alternative_low_id,alternative_high_id FROM alternative_relation "
            "WHERE alternative_relation_id=? AND is_current=1", (relation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Canonical relation to remove is not current")
        refs.update(row)
    concepts = {new.concept_id for new in operation.new_alternatives}
    if operation.destination_concept_id is not None:
        concepts.add(operation.destination_concept_id)
    for ref in refs:
        if isinstance(ref, int):
            row = connection.execute(
                "SELECT concept_id FROM alternative WHERE alternative_id=?", (ref,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown alternative")
            concepts.add(row[0])
    alternatives, assignments, relations = [], [], {}
    for concept_id in sorted(concepts):
        if connection.execute("SELECT 1 FROM concept WHERE concept_id=?", (concept_id,)).fetchone() is None:
            raise ValueError("Unknown concept")
        alternatives.extend(Alternative(*row) for row in connection.execute(
            "SELECT alternative_id,concept_id,working_label,created_at,retired_at IS NULL "
            "FROM alternative WHERE concept_id=? ORDER BY alternative_id", (concept_id,)))
        assignments.extend(Assignment(*row) for row in connection.execute(
            "SELECT a.assignment_id,a.occurrence_id,a.alternative_id FROM assignment a "
            "JOIN alternative al USING(alternative_id) WHERE al.concept_id=? "
            "AND a.is_current=1 ORDER BY a.assignment_id", (concept_id,)))
        for row in connection.execute(
            "SELECT r.alternative_relation_id,r.alternative_low_id,r.alternative_high_id,"
            "r.phonological_parameter FROM alternative_relation r "
            "JOIN alternative lo ON lo.alternative_id=r.alternative_low_id "
            "JOIN alternative hi ON hi.alternative_id=r.alternative_high_id "
            "WHERE r.is_current=1 AND (lo.concept_id=? OR hi.concept_id=?)",
            (concept_id, concept_id)):
            relations[row[0]] = Relation(*row)
    occurrences = []
    for occurrence_id in sorted({operation.occurrence_id} | {a.occurrence_id for a in assignments}):
        row = connection.execute(
            "SELECT o.occurrence_id,o.occurrence_year,s.start_year,s.end_year,"
            "s.end_year_status,s.source_name,s.source_reference FROM occurrence o "
            "JOIN source s USING(source_id) WHERE o.occurrence_id=?", (occurrence_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown occurrence")
        occurrences.append(Occurrence(*row))
    return LexicalState(tuple(sorted(concepts)), tuple(alternatives), tuple(occurrences),
                        tuple(assignments), tuple(relations[k] for k in sorted(relations)))


def build_effective_state(state, operation):
    """Transform immutable snapshots without a database or clock."""
    current = next((a for a in state.assignments if a.occurrence_id == operation.occurrence_id), None)
    if (current.assignment_id if current else None) != operation.expected_assignment_id:
        raise ValueError("Expected current assignment no longer matches")
    if operation.occurrence_id not in {o.occurrence_id for o in state.occurrences}:
        raise ValueError("Occurrence not loaded")
    alternatives = {a.ref: a for a in state.alternatives}
    for new in operation.new_alternatives:
        if new.ref in alternatives or new.concept_id not in state.concepts:
            raise ValueError("Duplicate virtual reference or missing concept")
        alternatives[new.ref] = Alternative(new.ref, new.concept_id, new.working_label,
                                            None, True, new.virtual_order)
    seen = set()
    for change in operation.validity_changes:
        if change.alternative_ref not in alternatives or change.alternative_ref in seen:
            raise ValueError("Unknown or duplicate validity change")
        seen.add(change.alternative_ref)
        alternatives[change.alternative_ref] = replace(alternatives[change.alternative_ref], active=change.active)
    assignments = list(state.assignments)
    if operation.destination is not None:
        destination = alternatives.get(operation.destination)
        if destination is None or not destination.active:
            raise ValueError("Destination must be a loaded, active alternative")
        if destination.concept_id != operation.destination_concept_id:
            raise ValueError("Destination concept does not match alternative")
        if current is None or current.alternative_ref != destination.ref:
            assignments = [a for a in assignments if a.occurrence_id != operation.occurrence_id]
            assignments.append(Assignment(None, operation.occurrence_id, destination.ref))
    relations = {r.ref: r for r in state.relations}
    for ref in operation.removed_relation_ids:
        if ref not in relations:
            raise ValueError("Canonical relation to remove is not current")
        del relations[ref]
    for relation in operation.added_relations:
        if not isinstance(relation.ref, str) or not relation.ref.startswith("new_relation:"):
            raise ValueError("New relations require an explicit virtual reference")
        if relation.ref in relations or any(endpoint not in alternatives for endpoint in (relation.left, relation.right)):
            raise ValueError("Duplicate relation reference or unknown endpoint")
        relations[relation.ref] = relation
    return replace(state, alternatives=tuple(alternatives.values()),
                   assignments=tuple(assignments), relations=tuple(relations.values()))


def derive_affected_concepts(state, operation):
    """Roles come from canonical assignments and explicit effects, never UI lists."""
    after = build_effective_state(state, operation)
    alternatives = {a.ref: a for a in after.alternatives}
    roles = {}

    def mark(ref, role):
        roles.setdefault(alternatives[ref].concept_id, set()).add(role)

    current = next((a for a in state.assignments if a.occurrence_id == operation.occurrence_id), None)
    if operation.destination is not None and (current is None or current.alternative_ref != operation.destination):
        if current:
            mark(current.alternative_ref, "origin")
        mark(operation.destination, "destination")
    for new in operation.new_alternatives:
        mark(new.ref, "creation")
    for relation in (*operation.added_relations,
                     *(r for r in state.relations if r.ref in operation.removed_relation_ids)):
        mark(relation.left, "relation")
        mark(relation.right, "relation")
    before = {a.ref: a for a in state.alternatives}
    for change in operation.validity_changes:
        if change.alternative_ref not in before or before[change.alternative_ref].active != change.active:
            mark(change.alternative_ref, "validity")
    return {concept: tuple(sorted(value)) for concept, value in sorted(roles.items())}


def calculate_concept_nomenclature(concept_state):
    state = concept_state.lexical_state
    occurrences = {o.occurrence_id: o for o in state.occurrences}
    rows = []
    for alternative in state.alternatives:
        if alternative.concept_id != concept_state.concept_id or not alternative.active:
            continue
        evidence = [occurrences[a.occurrence_id] for a in state.assignments
                    if a.alternative_ref == alternative.ref]
        references = [(*temporal_reference(o.occurrence_year, o.start_year, o.end_year,
                                           o.end_year_status), o.source_reference or o.source_name)
                      for o in evidence]
        reference = min((r for r in references if r[0] is not None),
                        default=(None, None, None), key=lambda r: r[0])
        rows.append(dict(alternative_id=alternative.ref, current_label=alternative.working_label,
                         created_at=alternative.created_at, virtual_order=alternative.virtual_order,
                         reference_year=reference[0], reference_basis=reference[1],
                         reference_source=reference[2]))
    result = calculate_nomenclature_rows(rows, [(r.left, r.right) for r in state.relations])
    return dict(automatic_labels=result["suggestions"], preview_rows=result["rows"],
                components=result["components"], conflicts=result["problems"],
                warnings=[], conclusive=result["conclusive"])


def simulate_lexical_operation(connection, operation):
    state = load_lexical_state(connection, operation)
    effective = build_effective_state(state, operation)
    roles = derive_affected_concepts(state, operation)
    return dict(
        affected_concepts={cid: dict(roles=role, **calculate_concept_nomenclature(ConceptState(cid, effective)))
                           for cid, role in roles.items()},
        assignment_effects={"before": tuple(a for a in state.assignments if a.occurrence_id == operation.occurrence_id),
                            "after": tuple(a for a in effective.assignments if a.occurrence_id == operation.occurrence_id)},
        relation_effects={"added": operation.added_relations, "removed": tuple(
            r for r in state.relations if r.ref in operation.removed_relation_ids)},
        effective_state=effective)
