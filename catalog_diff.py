"""Deterministic lexical change summaries between frozen catalog snapshots."""


def _index(items, key):
    return {item[key]: item for item in items}


def _label(item, *keys):
    return {key: item.get(key) for key in keys}


def build_catalog_diff(previous, current):
    """Return a JSON-safe summary based exclusively on the two projections."""
    previous = previous or {"concepts": []}
    old_concepts = _index(previous.get("concepts", []), "concept_id")
    new_concepts = _index(current.get("concepts", []), "concept_id")
    summary = {
        "initial_publication": not bool(old_concepts),
        "concepts_added": [], "concepts_removed": [], "concepts_changed": [],
        "alternatives_added": [], "alternatives_removed": [],
        "alternatives_changed": [], "occurrences_added": [],
        "occurrences_removed": [], "occurrences_changed_or_moved": [],
        "relations_added": [], "relations_removed": [],
        "morphology_changed": [], "grammar_or_evidence_changed": [],
    }
    for concept_id in sorted(new_concepts.keys() - old_concepts.keys()):
        summary["concepts_added"].append(_label(new_concepts[concept_id], "concept_id", "preferred_label"))
    for concept_id in sorted(old_concepts.keys() - new_concepts.keys()):
        summary["concepts_removed"].append(_label(old_concepts[concept_id], "concept_id", "preferred_label"))
    for concept_id in sorted(old_concepts.keys() & new_concepts.keys()):
        if old_concepts[concept_id]["preferred_label"] != new_concepts[concept_id]["preferred_label"]:
            summary["concepts_changed"].append({"concept_id": concept_id, "before": old_concepts[concept_id]["preferred_label"], "after": new_concepts[concept_id]["preferred_label"]})

    def flatten(projection):
        alternatives = {}; occurrences = {}; relations = {}
        for concept in projection.get("concepts", []):
            for alternative in concept.get("alternatives", []):
                alternatives[alternative["alternative_id"]] = (concept["concept_id"], alternative)
                for occurrence in alternative.get("occurrences", []):
                    occurrences[occurrence["occurrence_id"]] = (alternative["alternative_id"], occurrence)
            for relation in concept.get("relations", []):
                relations[relation["alternative_relation_id"]] = relation
        return alternatives, occurrences, relations

    old_alt, old_occ, old_rel = flatten(previous)
    new_alt, new_occ, new_rel = flatten(current)
    for identifier in sorted(new_alt.keys() - old_alt.keys()):
        summary["alternatives_added"].append(_label(new_alt[identifier][1], "alternative_id", "name"))
    for identifier in sorted(old_alt.keys() - new_alt.keys()):
        summary["alternatives_removed"].append(_label(old_alt[identifier][1], "alternative_id", "name"))
    for identifier in sorted(old_alt.keys() & new_alt.keys()):
        old_parent, old = old_alt[identifier]; new_parent, new = new_alt[identifier]
        if ((old_parent, old.get("working_label"), old.get("name")) !=
                (new_parent, new.get("working_label"), new.get("name")) or
                old.get("media", []) != new.get("media", [])):
            summary["alternatives_changed"].append({"alternative_id": identifier, "name": new.get("name")})
        if old.get("morphology") != new.get("morphology"):
            summary["morphology_changed"].append({"alternative_id": identifier, "name": new.get("name")})
    for identifier in sorted(new_occ.keys() - old_occ.keys()):
        summary["occurrences_added"].append(_label(new_occ[identifier][1], "occurrence_id", "original_gloss"))
    for identifier in sorted(old_occ.keys() - new_occ.keys()):
        summary["occurrences_removed"].append(_label(old_occ[identifier][1], "occurrence_id", "original_gloss"))
    for identifier in sorted(old_occ.keys() & new_occ.keys()):
        old_parent, old = old_occ[identifier]; new_parent, new = new_occ[identifier]
        if old_parent != new_parent or old != new:
            item = {"occurrence_id": identifier, "original_gloss": new.get("original_gloss")}
            summary["occurrences_changed_or_moved"].append(item)
            if old.get("grammar") != new.get("grammar") or {k: v for k, v in old.items() if k != "grammar"} != {k: v for k, v in new.items() if k != "grammar"}:
                summary["grammar_or_evidence_changed"].append(item)
    for identifier in sorted(new_rel.keys() - old_rel.keys()):
        summary["relations_added"].append(new_rel[identifier])
    for identifier in sorted(old_rel.keys() - new_rel.keys()):
        summary["relations_removed"].append(old_rel[identifier])
    return summary


def summary_totals(summary):
    return {key: len(value) for key, value in summary.items() if isinstance(value, list) and value}
