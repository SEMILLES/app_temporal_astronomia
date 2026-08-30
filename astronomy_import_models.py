"""Modelos inmutables canónicos del importador de Astronomía."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceInput:
    key: str
    name: str
    legacy_code: str | None
    scope: str
    source_type: str | None
    source_reference: str | None
    format_original: str | None
    format_detail: str | None
    start_year: int | None
    end_year: int | None
    end_year_status: str | None
    region_description: str | None
    characterization: str | None
    reported_entry_count: int | None
    aliases: tuple[str, ...]
    metadata_anomaly_note: str | None


@dataclass(frozen=True)
class ConceptInput:
    preferred_label: str


@dataclass(frozen=True)
class AlternativeInput:
    canonical_code: str
    concept_label: str
    working_label: str
    original_code: str | None


@dataclass(frozen=True)
class RelationInput:
    alternative_a: str
    alternative_b: str
    parameter: str


@dataclass(frozen=True)
class OccurrenceInput:
    key: str
    legacy_occurrence_id: str | None
    original_gloss: str
    concept_label: str
    alternative_code: str
    source_name: str
    legacy_source_code: str | None
    legacy_source_detail_1: str | None
    legacy_source_detail_2: str | None
    source_locator: str | None
    hyperlink: str | None
    occurrence_year: int | None
    legacy_source_year: str | None


@dataclass(frozen=True)
class AssignmentInput:
    occurrence_key: str
    alternative_code: str


@dataclass(frozen=True)
class GrammarInput:
    occurrence_key: str
    gender: str
    plural: str
    negation: str
    conjugated_form: str
    agentive: str
    grammar_note: str | None


@dataclass(frozen=True)
class ExcludedOccurrence:
    legacy_occurrence_id: str
    reason: str


@dataclass(frozen=True)
class ImportModels:
    sources: tuple[SourceInput, ...] = ()
    concepts: tuple[ConceptInput, ...] = ()
    alternatives: tuple[AlternativeInput, ...] = ()
    relations: tuple[RelationInput, ...] = ()
    occurrences: tuple[OccurrenceInput, ...] = ()
    assignments: tuple[AssignmentInput, ...] = ()
    grammar: tuple[GrammarInput, ...] = ()
    excluded: tuple[ExcludedOccurrence, ...] = ()


@dataclass(frozen=True)
class ValidatedImportPlan:
    """Única frontera admitida para la capa de persistencia."""

    sources: tuple[SourceInput, ...]
    concepts: tuple[ConceptInput, ...]
    alternatives: tuple[AlternativeInput, ...]
    relations: tuple[RelationInput, ...]
    occurrences: tuple[OccurrenceInput, ...]
    assignments: tuple[AssignmentInput, ...]
    grammar: tuple[GrammarInput, ...]
    excluded: tuple[ExcludedOccurrence, ...]


@dataclass(frozen=True)
class DryRunResult:
    models: ImportModels
    validated_plan: ValidatedImportPlan | None
    hashes: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    deferred: tuple[str, ...]
    provenance_reviewed: int = 0
    provenance_component: int = 0
    provenance_candidates: int = 0
    provenance_review: int = 0

    @property
    def ready_for_apply(self) -> bool:
        return not self.errors and self.validated_plan is not None
