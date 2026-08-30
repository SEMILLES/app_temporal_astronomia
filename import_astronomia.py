"""Validador dry-run del corpus reconstruido de Astronomía.

Este módulo prepara modelos intermedios, pero deliberadamente no contiene
ninguna operación ni dependencia de SQLite. El modo de escritura se añadirá
en una fase posterior.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REQUIRED_INPUTS = (
    "reconstruccion_sources_astronomia_v1.tsv",
    "reconstruccion_alternatives_astronomia_v2.md",
    "reconstruccion_occurrences_astronomia_v1.tsv",
    "revision_observaciones_astronomia_v1.tsv",
    "occurrences_excluidas_astronomia_v1.tsv",
    "resumen_occurrences_astronomia_v1.md",
)

SOURCE_HEADERS = (
    "source_reconstruction_key", "source_name", "legacy_source_code",
    "source_scope", "source_type", "source_reference", "format_original",
    "format_detail", "start_year", "end_year", "end_year_status",
    "region_description", "characterization", "reported_entry_count",
    "create_source_systematization", "systematization_status",
    "systematization_reviewed_at", "systematization_coverage_note",
    "legacy_source_number", "legacy_alpha_code", "legacy_repository_name",
    "legacy_period_literal", "legacy_academic_flag", "legacy_new_flag",
    "legacy_format_observation", "legacy_folder_name",
    "legacy_matrix_reviewer", "legacy_matrix_reviewed_at",
    "astronomia_occurrence_count", "astronomia_source_aliases",
    "source_reconstruction_note", "metadata_anomaly_note",
    "reconstruction_evidence",
)

OCCURRENCE_HEADERS = (
    "Área de conocimiento", "Legacy occurrence", "Glosa original",
    "Concepto canónico", "Alternative canónica", "Assignment", "Fuente",
    "Alcance", "Código fuente legacy", "Detalle fuente 1 legacy",
    "Detalle fuente 2 legacy", "Source locator reconstruido",
    "Hyperlink legacy", "Occurrence year", "Año fuente legacy", "Género",
    "Plural", "Negación", "Forma conjugada", "Agentivo", "Observaciones",
)

OBSERVATION_HEADERS = (
    "occurrence_reconstruction_key", "legacy_occurrence_id",
    "alternative_canonica", "fuente", "observacion_original", "categories",
    "provenance_note_candidate", "provenance_decision", "review_note",
)

EXCLUDED_HEADERS = ("Legacy occurrence", "Razón de exclusión")
VALID_STATUSES = {"", "known", "ongoing", "unknown"}
VALID_SCOPES = {"INSTITUTIONAL", "PERSONAL"}
VALID_PARAMETERS = {
    "CM_1", "CM_2", "LOC_1", "LOC_2", "MOV_M1", "MOV_M2", "OR_M1",
    "OR_M2", "N_MANOS", "CM_bimanual", "LOC_bimanual", "MOV_bimanual",
    "OR_bimanual",
}
VALID_CATEGORIES = {
    "OCCURRENCE_PROVENANCE", "SOURCE_RECONSTRUCTION",
    "ALTERNATIVE_RENUMBERING", "MORPHOLOGY_PENDING",
    "METHODOLOGICAL_ONLY", "OTHER_REVIEW",
}
LUZ_NOTE = (
    "La dirección de la seña depende de dónde se ubique la fuente de luz "
    "en el discurso."
)
LUZ_OCCURRENCES = {
    "legacy:2183-LUZ": ("2183-LUZ", "LUZ", "LUZ-1a", "DBLSC"),
    "legacy:11162-LUZ": (
        "11162-LUZ", "LUZ", "LUZ-1b", "Planetario de Medellín"
    ),
}
NEGATED_OCCURRENCE = (
    "unal:UNIVERSIDAD NACIONAL|CONTAMINACIÓN-LUMÍNICA-1a|CONTAMINACIÓN LUMÍNICA",
    None,
    "CONTAMINACIÓN LUMÍNICA",
    "CONTAMINACIÓN-LUMÍNICA-1a",
    "UNIVERSIDAD NACIONAL",
)


@dataclass(frozen=True)
class CorpusExpectations:
    source_count: int
    source_scopes: tuple[tuple[str, int], ...]
    shared_legacy_source_code: str
    shared_legacy_source_code_count: int
    concept_count: int
    alternative_count: int
    original_code_count: int
    relation_count: int
    occurrence_count: int
    legacy_occurrence_count: int
    grammar_notes: tuple[tuple[str, tuple[str | None, str, str, str], str], ...]
    negated_occurrence: tuple[str, str | None, str, str, str]
    excluded_count: int
    provenance_count: int
    provenance_component: int
    provenance_candidates: int
    provenance_review: int
    provenance_review_alternatives: frozenset[str]


DEFAULT_EXPECTATIONS = CorpusExpectations(
    44, (("INSTITUTIONAL", 40), ("PERSONAL", 4)), "0MISC", 4,
    150, 228, 27, 19, 253, 234,
    tuple((key, identity, LUZ_NOTE) for key, identity in LUZ_OCCURRENCES.items()),
    NEGATED_OCCURRENCE, 3,
    115, 21, 21, 0, frozenset(),
)


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
    """Única frontera admitida para una futura capa de persistencia."""

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


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _optional(value: str) -> str | None:
    return value if value != "" else None


def _integer(value: str, field: str, context: str, validation: Validation) -> int | None:
    if value == "":
        return None
    try:
        parsed = int(value)
    except ValueError:
        validation.error(f"{context}: {field} debe ser entero: {value!r}")
        return None
    return parsed


def _read_tsv(path: Path, expected: tuple[str, ...], validation: Validation) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            actual = tuple(reader.fieldnames or ())
            if actual != expected:
                validation.error(
                    f"{path.name}: headers inesperados; esperados {list(expected)!r}, "
                    f"recibidos {list(actual)!r}"
                )
                return []
            return list(reader)
    except (OSError, UnicodeError) as exc:
        validation.error(f"{path.name}: no se pudo leer: {exc}")
        return []


def _markdown_rows(lines: Iterable[str], heading: str) -> list[list[str]]:
    in_section = False
    rows: list[list[str]] = []
    skipped_header = False
    for raw_line in lines:
        line = raw_line.strip()
        if line == heading:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not skipped_header:
            skipped_header = True
            continue
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _parse_sources(path: Path, validation: Validation, expectations: CorpusExpectations) -> tuple[SourceInput, ...]:
    rows = _read_tsv(path, SOURCE_HEADERS, validation)
    result: list[SourceInput] = []
    names: set[str] = set()
    keys: set[str] = set()
    alias_owners: dict[str, str] = {}
    for number, row in enumerate(rows, 2):
        context = f"{path.name}:{number}"
        name, key = row["source_name"], row["source_reconstruction_key"]
        if not name or name in names:
            validation.error(f"{context}: source_name vacío o duplicado: {name!r}")
        if not key or key in keys:
            validation.error(f"{context}: source_reconstruction_key vacío o duplicado: {key!r}")
        names.add(name)
        keys.add(key)
        scope = row["source_scope"]
        if scope not in VALID_SCOPES:
            validation.error(f"{context}: source_scope inválido: {scope!r}")
        start = _integer(row["start_year"], "start_year", context, validation)
        end = _integer(row["end_year"], "end_year", context, validation)
        count = _integer(row["reported_entry_count"], "reported_entry_count", context, validation)
        status = row["end_year_status"]
        if status not in VALID_STATUSES:
            validation.error(f"{context}: end_year_status inválido: {status!r}")
        if status == "known" and end is None:
            validation.error(f"{context}: known exige end_year")
        if status in {"ongoing", "unknown"} and end is not None:
            validation.error(f"{context}: {status} exige end_year vacío")
        if status == "" and (start is not None or end is not None):
            validation.error(f"{context}: periodo con año exige end_year_status")
        if start is not None and end is not None and start > end:
            validation.error(f"{context}: start_year es posterior a end_year")
        if row["create_source_systematization"] != "0":
            validation.error(f"{context}: no se permite crear source_systematization")
        if any(row[field] for field in (
            "systematization_status", "systematization_reviewed_at",
            "systematization_coverage_note",
        )):
            validation.error(f"{context}: metadata de systematization inesperada")
        aliases = tuple(item for item in row["astronomia_source_aliases"].split("|") if item)
        for alias in (name, *aliases):
            owner = alias_owners.setdefault(alias, name)
            if owner != name:
                validation.error(f"{context}: alias ambiguo {alias!r}: {owner!r}/{name!r}")
        if row["metadata_anomaly_note"]:
            validation.warning(f"{name}: metadata_anomaly_note preservada; no se interpreta")
        result.append(SourceInput(
            key, name, _optional(row["legacy_source_code"]), scope,
            _optional(row["source_type"]), _optional(row["source_reference"]),
            _optional(row["format_original"]), _optional(row["format_detail"]),
            start, end, _optional(status), _optional(row["region_description"]),
            _optional(row["characterization"]), count, aliases,
            _optional(row["metadata_anomaly_note"]),
        ))
    if len(rows) != expectations.source_count:
        validation.error(f"sources: se esperaban {expectations.source_count} filas y se recibieron {len(rows)}")
    scopes = Counter(item.scope for item in result)
    if scopes != Counter(dict(expectations.source_scopes)):
        validation.error(f"sources: distribución de scope inesperada: {dict(scopes)}")
    shared_code = expectations.shared_legacy_source_code
    if any(item.name == shared_code for item in result):
        validation.error(f"sources: {shared_code} no puede ser source_name")
    shared_count = sum(item.legacy_code == shared_code for item in result)
    if shared_count != expectations.shared_legacy_source_code_count:
        validation.error(
            f"sources: se esperaban {expectations.shared_legacy_source_code_count} "
            f"legacy_source_code {shared_code}; recibidos {shared_count}"
        )
    return tuple(result)


def _parse_alternatives(path: Path, validation: Validation, expectations: CorpusExpectations) -> tuple[tuple[ConceptInput, ...], tuple[AlternativeInput, ...], tuple[RelationInput, ...]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        validation.error(f"{path.name}: no se pudo leer: {exc}")
        return (), (), ()
    alternative_rows = _markdown_rows(text.splitlines(), "## Alternatives")
    alternatives: list[AlternativeInput] = []
    concepts: dict[str, ConceptInput] = {}
    codes: set[str] = set()
    suffix_pattern = re.compile(r"[1-9][0-9]*[a-z]")
    for number, cells in enumerate(alternative_rows, 1):
        if len(cells) != 8:
            validation.error(f"alternatives fila {number}: se esperaban 8 columnas")
            continue
        concept, code, legacy = cells[:3]
        prefix = concept + "-"
        if not code.startswith(prefix):
            validation.error(f"{code}: prefijo conceptual inconsistente con {concept}")
            working_label = ""
        else:
            working_label = code[len(prefix):]
        if not suffix_pattern.fullmatch(working_label):
            validation.error(f"{code}: sufijo/working_label inválido: {working_label!r}")
        if code in codes:
            validation.error(f"alternative duplicada: {code}")
        codes.add(code)
        concepts.setdefault(concept, ConceptInput(concept))
        alternatives.append(AlternativeInput(
            code, concept, working_label, None if legacy == "—" else legacy
        ))
    if len(concepts) != expectations.concept_count:
        validation.error(f"concepts: se esperaban {expectations.concept_count} y se recibieron {len(concepts)}")
    if len(alternatives) != expectations.alternative_count:
        validation.error(f"alternatives: se esperaban {expectations.alternative_count} y se recibieron {len(alternatives)}")
    original_count = sum(item.original_code is not None for item in alternatives)
    if original_count != expectations.original_code_count:
        validation.error(f"alternatives: se esperaban {expectations.original_code_count} original_code y se recibieron {original_count}")

    relation_rows = _markdown_rows(text.splitlines(), "## Relaciones fonológicas confirmadas")
    relations: list[RelationInput] = []
    pairs: dict[tuple[str, str], str] = {}
    for number, cells in enumerate(relation_rows, 1):
        if len(cells) != 4:
            validation.error(f"relations fila {number}: se esperaban 4 columnas")
            continue
        a, b, parameter = cells[:3]
        if a not in codes or b not in codes:
            validation.error(f"relation {a}/{b}: alternative inexistente")
        if a == b:
            validation.error(f"relation {a}/{b}: self-relation inválida")
        if parameter not in VALID_PARAMETERS:
            validation.error(f"relation {a}/{b}: parámetro desconocido {parameter!r}")
        pair = tuple(sorted((a, b)))
        if pair in pairs:
            validation.error(f"relation {pair}: par duplicado o con más de un parámetro")
        pairs[pair] = parameter
        relations.append(RelationInput(pair[0], pair[1], parameter))
    relations.sort(key=lambda item: (item.alternative_a, item.alternative_b, item.parameter))
    if len(relations) != expectations.relation_count:
        validation.error(f"relations: se esperaban {expectations.relation_count} y se recibieron {len(relations)}")
    return tuple(sorted(concepts.values(), key=lambda item: item.preferred_label)), tuple(alternatives), tuple(relations)


def _parse_occurrences(path: Path, sources: tuple[SourceInput, ...], concepts: tuple[ConceptInput, ...], alternatives: tuple[AlternativeInput, ...], validation: Validation, expectations: CorpusExpectations) -> tuple[tuple[OccurrenceInput, ...], tuple[AssignmentInput, ...], tuple[GrammarInput, ...]]:
    rows = _read_tsv(path, OCCURRENCE_HEADERS, validation)
    source_lookup: dict[str, str] = {}
    for source in sources:
        for candidate in (source.name, *source.aliases):
            source_lookup[candidate] = source.name
    concept_names = {item.preferred_label for item in concepts}
    alternative_map = {item.canonical_code: item for item in alternatives}
    occurrences: list[OccurrenceInput] = []
    assignments: list[AssignmentInput] = []
    grammar: list[GrammarInput] = []
    keys: set[str] = set()
    legacy_ids: set[str] = set()
    exact_note_keys: set[str] = set()
    exact_negated_key: str | None = None
    note_expectations = {
        key: (identity, note) for key, identity, note in expectations.grammar_notes
    }
    for number, row in enumerate(rows, 2):
        context = f"{path.name}:{number}"
        legacy_id = row["Legacy occurrence"]
        source = row["Fuente"]
        alternative_code = row["Alternative canónica"]
        concept = row["Concepto canónico"]
        if legacy_id:
            key = "legacy:" + legacy_id
            if legacy_id in legacy_ids:
                validation.error(f"{context}: Legacy occurrence duplicada: {legacy_id}")
            legacy_ids.add(legacy_id)
        else:
            key = f"unal:{source}|{alternative_code}|{row['Glosa original']}"
        if key in keys:
            validation.error(f"{context}: occurrence_reconstruction_key duplicada: {key}")
        keys.add(key)
        resolved_source = source_lookup.get(source)
        if resolved_source is None:
            validation.error(f"{context}: source no resoluble: {source!r}")
            resolved_source = source
        if concept not in concept_names:
            validation.error(f"{context}: concept no resoluble: {concept!r}")
        alternative = alternative_map.get(alternative_code)
        if alternative is None:
            validation.error(f"{context}: alternative no resoluble: {alternative_code!r}")
        elif alternative.concept_label != concept:
            validation.error(f"{context}: assignment ambiguo/inconsistente entre {concept!r} y {alternative_code!r}")
        if row["Assignment"] != "ASIGNADA":
            validation.error(f"{context}: Assignment debe ser ASIGNADA")
        if row["Occurrence year"]:
            validation.error(f"{context}: occurrence_year debe permanecer vacío; Año fuente legacy no puede sustituirlo")
        occurrence_year = _integer(row["Occurrence year"], "Occurrence year", context, validation)
        identity = (
            _optional(legacy_id), row["Glosa original"], alternative_code, source
        )
        note_expectation = note_expectations.get(key)
        is_exact_note = note_expectation is not None and note_expectation[0] == identity
        if note_expectation is not None:
            if is_exact_note:
                exact_note_keys.add(key)
            else:
                validation.error(
                    f"{context}: identidad de occurrence con grammar_note inesperada; "
                    f"esperada {note_expectation[0]!r}, recibida {identity!r}"
                )
        negated_key, *negated_identity = expectations.negated_occurrence
        is_exact_negated = key == negated_key and tuple(negated_identity) == identity
        if key == negated_key:
            if is_exact_negated:
                exact_negated_key = key
            else:
                validation.error(
                    f"{context}: identidad de occurrence CON-NEG inesperada; "
                    f"esperada {tuple(negated_identity)!r}, recibida {identity!r}"
                )
        occurrences.append(OccurrenceInput(
            key, _optional(legacy_id), row["Glosa original"], concept,
            alternative_code, resolved_source, _optional(row["Código fuente legacy"]),
            _optional(row["Detalle fuente 1 legacy"]),
            _optional(row["Detalle fuente 2 legacy"]),
            _optional(row["Source locator reconstruido"]),
            _optional(row["Hyperlink legacy"]), occurrence_year,
            _optional(row["Año fuente legacy"]),
        ))
        assignments.append(AssignmentInput(key, alternative_code))
        values = {
            "gender": row["Género"], "plural": row["Plural"],
            "negation": row["Negación"],
            "conjugated_form": row["Forma conjugada"],
            "agentive": row["Agentivo"],
        }
        expected = {
            "gender": "SIN-MARCA", "plural": "SIN-MARCA",
            "negation": "CON-NEG" if is_exact_negated else "SIN-NEG",
            "conjugated_form": "SÍ" if is_exact_note else "SIN-MARCA",
            "agentive": "SIN-MARCA",
        }
        if values != expected:
            validation.error(f"{context}: grammar inválido; esperado {expected!r}, recibido {values!r}")
        if not any(values.values()):
            validation.error(f"{context}: grammar completamente vacío")
        note = note_expectation[1] if is_exact_note else None
        grammar.append(GrammarInput(key, values["gender"], values["plural"], values["negation"], values["conjugated_form"], values["agentive"], note))
    if len(rows) != expectations.occurrence_count:
        validation.error(f"occurrences: se esperaban {expectations.occurrence_count} filas y se recibieron {len(rows)}")
    legacy_count = sum(item.legacy_occurrence_id is not None for item in occurrences)
    if legacy_count != expectations.legacy_occurrence_count:
        validation.error(f"occurrences: distribución legacy/UNAL inesperada: {legacy_count}/{len(occurrences)-legacy_count}")
    if len(assignments) != expectations.occurrence_count:
        validation.error(f"assignments: se esperaban {expectations.occurrence_count} y se recibieron {len(assignments)}")
    if len(grammar) != expectations.occurrence_count:
        validation.error(f"grammar: se esperaban {expectations.occurrence_count} y se recibieron {len(grammar)}")
    if exact_note_keys != set(note_expectations):
        validation.error(
            "grammar: occurrences con grammar_note exactas incompletas: "
            f"{sorted(exact_note_keys)!r}"
        )
    if exact_negated_key != expectations.negated_occurrence[0]:
        validation.error("grammar: occurrence CON-NEG exacta ausente")
    grammar_expectations = {
        "gender": Counter(item.gender for item in grammar),
        "plural": Counter(item.plural for item in grammar),
        "agentive": Counter(item.agentive for item in grammar),
        "conjugated_form": Counter(item.conjugated_form for item in grammar),
        "negation": Counter(item.negation for item in grammar),
        "grammar_note": Counter(item.grammar_note for item in grammar),
    }
    total = expectations.occurrence_count
    note_count = len(expectations.grammar_notes)
    expected_distributions = {
        "gender": Counter({"SIN-MARCA": total}),
        "plural": Counter({"SIN-MARCA": total}),
        "agentive": Counter({"SIN-MARCA": total}),
        "conjugated_form": Counter({"SIN-MARCA": total - note_count, "SÍ": note_count}),
        "negation": Counter({"SIN-NEG": total - 1, "CON-NEG": 1}),
        "grammar_note": Counter([None] * (total - note_count) + [item[2] for item in expectations.grammar_notes]),
    }
    for field, actual in grammar_expectations.items():
        if actual != expected_distributions[field]:
            validation.error(
                f"grammar: distribución inesperada de {field}: {dict(actual)!r}"
            )
    return tuple(occurrences), tuple(assignments), tuple(grammar)


def _parse_excluded(path: Path, included: tuple[OccurrenceInput, ...], validation: Validation, expectations: CorpusExpectations) -> tuple[ExcludedOccurrence, ...]:
    rows = _read_tsv(path, EXCLUDED_HEADERS, validation)
    result = tuple(ExcludedOccurrence(row["Legacy occurrence"], row["Razón de exclusión"]) for row in rows)
    ids = {item.legacy_occurrence_id for item in result}
    if len(rows) != expectations.excluded_count:
        validation.error(
            f"excluded: se esperaban {expectations.excluded_count} filas y se recibieron {len(rows)}"
        )
    if any(not item.legacy_occurrence_id or not item.reason for item in result):
        validation.error("excluded: legacy occurrence y razón son obligatorios")
    if len(ids) != len(rows):
        validation.error("excluded: Legacy occurrence duplicada")
    included_ids = {item.legacy_occurrence_id for item in included if item.legacy_occurrence_id}
    overlap = sorted(ids & included_ids)
    if overlap:
        validation.error(f"excluded: IDs presentes también en corpus incluido: {overlap!r}")
    return result


def _validate_provenance(path: Path, occurrences: tuple[OccurrenceInput, ...], validation: Validation, expectations: CorpusExpectations) -> tuple[int, int, int, int]:
    rows = _read_tsv(path, OBSERVATION_HEADERS, validation)
    occurrence_keys = {item.key for item in occurrences}
    keys: set[str] = set()
    component = candidates = review = 0
    review_alternatives: set[str] = set()
    for number, row in enumerate(rows, 2):
        context = f"{path.name}:{number}"
        key = row["occurrence_reconstruction_key"]
        if key in keys:
            validation.error(f"{context}: occurrence_reconstruction_key duplicada: {key}")
        keys.add(key)
        if key not in occurrence_keys:
            validation.error(f"{context}: occurrence no resoluble: {key}")
        categories = set(filter(None, row["categories"].split("|")))
        unknown = categories - VALID_CATEGORIES
        if unknown:
            validation.error(f"{context}: categorías desconocidas: {sorted(unknown)!r}")
        if "OCCURRENCE_PROVENANCE" in categories:
            component += 1
        if row["provenance_note_candidate"]:
            candidates += 1
        if row["provenance_decision"] == "REVIEW":
            review += 1
            review_alternatives.add(row["alternative_canonica"])
    if len(rows) != expectations.provenance_count:
        validation.error(f"provenance: se esperaban {expectations.provenance_count} observaciones y se recibieron {len(rows)}")
    if (component, candidates, review) != (
        expectations.provenance_component,
        expectations.provenance_candidates,
        expectations.provenance_review,
    ):
        validation.error(f"provenance: conteos inesperados component/candidates/review={component}/{candidates}/{review}")
    if review_alternatives != expectations.provenance_review_alternatives:
        validation.error(f"provenance: alternatives REVIEW inesperadas: {sorted(review_alternatives)!r}")
    for alternative in sorted(review_alternatives):
        validation.warning(f"provenance pendiente de revisión: {alternative}")
    return len(rows), component, candidates, review


def _hash_inputs(directory: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, hashlib.sha256((directory / name).read_bytes()).hexdigest())
        for name in sorted(REQUIRED_INPUTS)
    )


def run_dry_run(input_directory: str | Path, expectations: CorpusExpectations = DEFAULT_EXPECTATIONS) -> DryRunResult:
    directory = Path(input_directory)
    validation = Validation()
    missing = [name for name in REQUIRED_INPUTS if not (directory / name).is_file()]
    if missing:
        for name in missing:
            validation.error(f"input obligatorio ausente: {name}")
        return DryRunResult(ImportModels(), None, (), tuple(validation.errors), (), (
            "Knowledge area: FUTURE_MODEL", "Morphology/composition: FUTURE_MODEL",
            "Media: DEFERRED", "Publication/permissions: DEFERRED",
            "Planetario split: DEFERRED",
        ))
    before = _hash_inputs(directory)
    sources = _parse_sources(directory / REQUIRED_INPUTS[0], validation, expectations)
    concepts, alternatives, relations = _parse_alternatives(directory / REQUIRED_INPUTS[1], validation, expectations)
    occurrences, assignments, grammar = _parse_occurrences(
        directory / REQUIRED_INPUTS[2], sources, concepts, alternatives, validation, expectations
    )
    reviewed, component, candidates, review = _validate_provenance(
        directory / REQUIRED_INPUTS[3], occurrences, validation, expectations
    )
    excluded = _parse_excluded(directory / REQUIRED_INPUTS[4], occurrences, validation, expectations)
    after = _hash_inputs(directory)
    if before != after:
        validation.error("los inputs cambiaron durante el dry-run")
    models = ImportModels(sources, concepts, alternatives, relations, occurrences, assignments, grammar, excluded)
    deferred = (
        "Knowledge area ASTRONOMÍA: FUTURE_MODEL",
        "Morphology/composition: FUTURE_MODEL",
        "Media: DEFERRED",
        "Publication/permissions: DEFERRED",
        "Planetario split: DEFERRED",
    )
    validated_plan = None
    if not validation.errors:
        validated_plan = ValidatedImportPlan(
            models.sources, models.concepts, models.alternatives,
            models.relations, models.occurrences, models.assignments,
            models.grammar, models.excluded,
        )
    return DryRunResult(
        models, validated_plan, before, tuple(validation.errors), tuple(validation.warnings),
        deferred, reviewed, component, candidates, review,
    )


def format_report(result: DryRunResult) -> str:
    models = result.models
    legacy = sum(item.legacy_occurrence_id is not None for item in models.occurrences)
    scopes = Counter(item.scope for item in models.sources)
    lines = [
        "ASTRONOMIA IMPORT DRY RUN", "=========================", "", "INPUTS",
    ]
    if result.hashes:
        lines.extend(f"[OK] {name} sha256={digest}" for name, digest in result.hashes)
    else:
        lines.append("[ERROR] Inputs incompletos")
    lines.extend((
        "", "SOURCES", f"[OK] {len(models.sources)}",
        f"[OK] Institutional: {scopes['INSTITUTIONAL']}",
        f"[OK] Personal: {scopes['PERSONAL']}", "", "CONCEPTS",
        f"[OK] {len(models.concepts)}", "", "ALTERNATIVES",
        f"[OK] {len(models.alternatives)}", "", "RELATIONS",
        f"[OK] {len(models.relations)}", "", "OCCURRENCES",
        f"[OK] {len(models.occurrences)}", f"[OK] Legacy IDs: {legacy}",
        f"[OK] New UNAL: {len(models.occurrences)-legacy}", "", "ASSIGNMENTS",
        f"[OK] {len(models.assignments)}", "", "GRAMMAR",
        f"[OK] {len(models.grammar)}", "", "EXCLUDED",
        f"[OK] {len(models.excluded)}", "", "PROVENANCE",
        f"Reviewed: {result.provenance_reviewed}",
        f"Occurrence provenance component: {result.provenance_component}",
        f"Candidates: {result.provenance_candidates}",
        f"Review: {result.provenance_review}",
        "provenance_note import currently disabled", "", "FUTURE/DEFERRED",
    ))
    lines.extend(f"- {item}" for item in result.deferred)
    lines.extend(("", "ERRORS"))
    lines.extend(f"[ERROR] {item}" for item in result.errors)
    lines.append(f"ERRORS: {len(result.errors)}")
    lines.extend(("", "WARNINGS"))
    lines.extend(f"[WARNING] {item}" for item in result.warnings)
    lines.append(f"WARNINGS: {len(result.warnings)}")
    lines.extend(("", f"READY FOR APPLY: {'YES' if result.ready_for_apply else 'NO'}"))
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    expectations: CorpusExpectations = DEFAULT_EXPECTATIONS,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valida los inputs reconstruidos de Astronomía y permite "
            "persistirlos explícitamente en SQLite."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true", help="Ejecuta únicamente validaciones de lectura.")
    action.add_argument("--apply", action="store_true", help="Valida y persiste en una SQLite explícita.")
    parser.add_argument("--database", help="Archivo SQLite destino obligatorio para --apply.")
    parser.add_argument("input_directory", help="Directorio que contiene los artefactos reconstruidos.")
    args = parser.parse_args(argv)
    if args.apply and not args.database:
        parser.error("--apply exige --database")
    if args.dry_run and args.database:
        parser.error("--database solo puede usarse con --apply")

    result = run_dry_run(args.input_directory, expectations)
    if not result.ready_for_apply:
        print(format_report(result))
        return 1
    if args.dry_run:
        print(format_report(result))
        return 0

    from astronomy_apply import apply_validated_plan_to_database

    try:
        apply_validated_plan_to_database(result.validated_plan, args.database)
    except Exception as exc:
        print(f"APPLY ERROR: {exc}", file=sys.stderr)
        return 1
    print(format_report(result))
    models = result.validated_plan
    print("")
    print("APPLY COMPLETED")
    print(f"DATABASE: {args.database}")
    print(
        "COUNTS: "
        f"sources={len(models.sources)} "
        f"concepts={len(models.concepts)} "
        f"alternatives={len(models.alternatives)} "
        f"relations={len(models.relations)} "
        f"occurrences={len(models.occurrences)} "
        f"assignments={len(models.assignments)} "
        f"grammar={len(models.grammar)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
