"""Scoped state shared by structural previews and nomenclature edits."""

from edit_concurrency import rows, fingerprint, sign, unsign, StaleEdit, STALE_PREVIEW


def relevant_state(db, source_id, destination_concept_id=None, target_id=None):
    source = rows(db, "SELECT alternative_id,concept_id,retired_at FROM alternative WHERE alternative_id=?", (source_id,))
    concepts = {r["concept_id"] for r in source}
    if destination_concept_id is not None:
        concepts.add(int(destination_concept_id))
    ids = tuple(sorted(concepts))
    marks = ",".join("?" for _ in ids) or "NULL"
    alternatives = rows(db, f"SELECT alternative_id,concept_id,working_label,created_at,retired_at FROM alternative WHERE concept_id IN ({marks}) AND retired_at IS NULL ORDER BY alternative_id", ids)
    aids = tuple(r["alternative_id"] for r in alternatives)
    amarks = ",".join("?" for _ in aids) or "NULL"
    mids = tuple(sorted({source_id} | ({int(target_id)} if target_id is not None else set())))
    mmarks = ",".join("?" for _ in mids)
    return {
        "source": source,
        "concepts": rows(db, f"SELECT * FROM concept WHERE concept_id IN ({marks}) ORDER BY concept_id", ids),
        "alternatives": alternatives,
        "assignments": rows(db, f"SELECT * FROM assignment WHERE alternative_id IN ({amarks}) AND is_current=1 ORDER BY assignment_id", aids),
        "evidence": rows(db, f"""SELECT o.occurrence_id,o.original_gloss,o.occurrence_year,
            s.source_id,s.source_name,s.source_reference,s.start_year,s.end_year,s.end_year_status,
            r.occurrence_concept_reference_id,r.concept_id,r.concept_proposal_id
            FROM occurrence o JOIN source s USING(source_id)
            LEFT JOIN occurrence_concept_reference r ON r.occurrence_id=o.occurrence_id AND r.is_current=1
            WHERE o.occurrence_id IN (SELECT occurrence_id FROM assignment WHERE alternative_id IN ({amarks}) AND is_current=1)
            ORDER BY o.occurrence_id""", aids),
        "relations": rows(db, f"SELECT * FROM alternative_relation WHERE is_current=1 AND (alternative_low_id IN ({amarks}) OR alternative_high_id IN ({amarks})) ORDER BY alternative_relation_id", aids + aids),
        "morphology": rows(db, f"SELECT * FROM alternative_morphology WHERE alternative_id IN ({mmarks}) AND is_current=1 ORDER BY alternative_morphology_id", mids),
        "components": rows(db, f"SELECT c.* FROM alternative_component c JOIN alternative_morphology m USING(alternative_morphology_id) WHERE m.alternative_id IN ({mmarks}) AND m.is_current=1 ORDER BY alternative_morphology_id,position", mids),
        "renumber": rows(db, f"SELECT concept_id,max(renumber_event_id) AS revision FROM renumber_event WHERE concept_id IN ({marks}) GROUP BY concept_id ORDER BY concept_id", ids),
    }


def state_token(db, source_id, spec):
    return sign({"purpose": "alternative-preview", "source_id": source_id, "spec": spec,
                 "fingerprint": fingerprint(relevant_state(db, source_id, spec.get("destination_concept_id")))})


def check_state(db, source_id, spec, token):
    payload = unsign(token, STALE_PREVIEW)
    expected = {"purpose": "alternative-preview", "source_id": source_id, "spec": spec,
                "fingerprint": fingerprint(relevant_state(db, source_id, spec.get("destination_concept_id")))}
    if payload != expected:
        raise StaleEdit(STALE_PREVIEW)
