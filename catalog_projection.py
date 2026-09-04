"""Deterministic, read-only projection of the current canonical catalog."""

from youtube_media import (InvalidYouTubeURL, parse_youtube_url,
                           youtube_embed_url, youtube_watch_url)

GRAMMAR_FIELDS = ("gender", "plural", "agentive", "conjugated_form", "negation")


def _key(value):
    return ((value or "").casefold(), value or "")


def _alternative_name(concept_label, working_label):
    return f"{concept_label}-{working_label}" if working_label else concept_label


def project_source(row):
    fields = (
        "source_id", "source_name", "source_type", "source_reference",
        "start_year", "end_year", "end_year_status", "source_scope",
        "format_original", "format_detail", "region_description",
        "characterization", "reported_entry_count", "legacy_source_code",
    )
    return {field: row[field] for field in fields}


def project_grammar(row):
    if row is None:
        return None
    return {
        "fields": {
            field: {
                "value": row[field],
                "uncertain": bool(row[field + "_uncertain"]),
            }
            for field in GRAMMAR_FIELDS
        },
        "note": row["grammar_note"],
    }


def project_occurrence(connection, occurrence_id):
    row = connection.execute(
        """
        SELECT o.*, s.source_name, s.source_type, s.source_reference,
               s.start_year, s.end_year, s.end_year_status, s.source_scope,
               s.format_original, s.format_detail, s.region_description,
               s.characterization, s.reported_entry_count, s.legacy_source_code
        FROM occurrence AS o JOIN source AS s USING(source_id)
        WHERE o.occurrence_id = ?
        """,
        (occurrence_id,),
    ).fetchone()
    grammar = connection.execute(
        "SELECT * FROM occurrence_grammar "
        "WHERE occurrence_id = ? AND is_current = 1", (occurrence_id,)
    ).fetchone()
    return {
        "occurrence_id": row["occurrence_id"],
        "legacy_occurrence_id": row["legacy_occurrence_id"],
        "original_gloss": row["original_gloss"],
        "source_detail_1": row["source_detail_1"],
        "source_detail_2": row["source_detail_2"],
        "usage_examples_present": bool(row["usage_examples_present"]),
        "grammatical_info_present": bool(row["grammatical_info_present"]),
        "grammatical_note": row["grammatical_note"],
        "hyperlink": row["hyperlink"],
        "source_locator": row["source_locator"],
        "provenance_note": row["provenance_note"],
        "occurrence_year": row["occurrence_year"],
        "source": project_source(row),
        "grammar": project_grammar(grammar),
    }


def project_morphology(connection, alternative_id, names):
    row = connection.execute(
        "SELECT * FROM alternative_morphology "
        "WHERE alternative_id = ? AND is_current = 1", (alternative_id,)
    ).fetchone()
    if row is None:
        return None
    components = []
    query = """
        SELECT position, component_alternative_id, component_label, note
        FROM alternative_component WHERE alternative_morphology_id = ?
        ORDER BY position, alternative_component_id
    """
    for component in connection.execute(query, (row["alternative_morphology_id"],)):
        components.append({
            "position": component["position"],
            "component_alternative_id": component["component_alternative_id"],
            "component_alternative_name": names.get(component["component_alternative_id"]),
            "component_label": component["component_label"],
            "note": component["note"],
        })
    return {
        "component_count": row["component_count"],
        "component_count_not_applicable": bool(row["component_count_not_applicable"]),
        "free_permutation": row["free_permutation"],
        "note": row["note"],
        "components": components,
    }


def project_alternative_media(connection, alternative_id):
    """Project only the current, explicitly canonical YouTube video."""
    rows = connection.execute("""
        SELECT m.media_asset_id,m.storage_backend,m.storage_key,
               m.original_filename,m.mime_type,m.file_size,m.checksum,
               m.origin_kind,m.origin_label,m.origin_locator,m.provenance_note,
               am.role
        FROM alternative_media AS am
        JOIN media_asset AS m USING(media_asset_id)
        WHERE am.alternative_id=? AND am.role='catalog_video' AND am.is_current=1
        ORDER BY m.media_asset_id,m.storage_key
    """, (alternative_id,))
    fields = ("media_asset_id", "storage_backend", "storage_key",
              "original_filename", "mime_type", "file_size", "checksum",
              "origin_kind", "origin_label", "origin_locator",
              "provenance_note", "role")
    result=[]
    for row in rows:
        try: video_id=parse_youtube_url(row["storage_key"])
        except InvalidYouTubeURL: continue
        item={field:row[field] for field in fields}
        item.update(video_id=video_id,watch_url=youtube_watch_url(video_id),embed_url=youtube_embed_url(video_id))
        result.append(item)
    return result


def project_nomenclature_history(connection, alternative_id):
    return [dict(row) for row in connection.execute("""
        SELECT e.created_at,e.origin,e.reason,e.created_by,
               c.old_working_label,c.new_working_label
        FROM renumber_change c JOIN renumber_event e USING(renumber_event_id)
        WHERE c.alternative_id=? ORDER BY e.created_at DESC,e.renumber_event_id DESC
    """, (alternative_id,))]


def build_catalog_projection(connection):
    """Return only current canonical lexical state, using JSON-safe values."""
    all_alternatives = [dict(row) for row in connection.execute("""
        SELECT a.alternative_id, a.concept_id, a.original_code, a.working_label,
               a.created_at, a.retired_at, c.preferred_label,
               c.semantic_field_1,c.semantic_field_2,
               c.knowledge_area_1,c.knowledge_area_2,c.created_at AS concept_created_at
        FROM alternative AS a JOIN concept AS c USING(concept_id)
    """)]
    names = {
        row["alternative_id"]: _alternative_name(
            row["preferred_label"], row["working_label"]
        ) for row in all_alternatives
    }
    alternatives = [
        row for row in all_alternatives
        if row["retired_at"] is None and (row["working_label"] or "").strip()
    ]
    active_ids = {row["alternative_id"] for row in alternatives}
    by_concept = {}
    for row in alternatives:
        by_concept.setdefault(row["concept_id"], {
            "concept_id": row["concept_id"],
            "preferred_label": row["preferred_label"],
            "semantic_fields": [value for value in (row["semantic_field_1"], row["semantic_field_2"]) if value],
            "knowledge_areas": [value for value in (row["knowledge_area_1"], row["knowledge_area_2"]) if value],
            "alternatives": [], "relations": [],
        })
    for row in alternatives:
        ids = connection.execute(
            "SELECT occurrence_id FROM assignment "
            "WHERE alternative_id = ? AND is_current = 1", (row["alternative_id"],)
        )
        occurrences = [project_occurrence(connection, item[0]) for item in ids]
        occurrences.sort(key=lambda item: (_key(item["original_gloss"]), item["occurrence_id"]))
        by_concept[row["concept_id"]]["alternatives"].append({
            "alternative_id": row["alternative_id"],
            "legacy_alternative_id": row["original_code"],
            "working_label": row["working_label"],
            "name": names[row["alternative_id"]],
            "media": project_alternative_media(connection, row["alternative_id"]),
            "occurrences": occurrences,
            "morphology": project_morphology(connection, row["alternative_id"], names),
            "relation_ids": [],
            "nomenclature_history": project_nomenclature_history(connection, row["alternative_id"]),
        })
    concept_by_alternative = {
        row["alternative_id"]: row["concept_id"] for row in alternatives
    }
    relations = connection.execute("""
        SELECT alternative_relation_id, alternative_low_id,
               alternative_high_id, phonological_parameter
        FROM alternative_relation WHERE is_current = 1
        ORDER BY alternative_low_id, alternative_high_id,
                 phonological_parameter, alternative_relation_id
    """)
    for row in relations:
        low_id, high_id = row["alternative_low_id"], row["alternative_high_id"]
        if low_id not in active_ids or high_id not in active_ids:
            continue
        relation = {
            "alternative_relation_id": row["alternative_relation_id"],
            "alternative_low_id": low_id,
            "alternative_low_name": names[low_id],
            "alternative_high_id": high_id,
            "alternative_high_name": names[high_id],
            "phonological_parameter": row["phonological_parameter"],
        }
        concept_ids = {concept_by_alternative[low_id], concept_by_alternative[high_id]}
        for concept_id in concept_ids:
            by_concept[concept_id]["relations"].append(relation.copy())
    concepts = list(by_concept.values())
    for concept in concepts:
        concept["alternatives"].sort(
            key=lambda item: (_key(item["working_label"]), item["alternative_id"])
        )
        for relation in concept["relations"]:
            for alternative in concept["alternatives"]:
                if alternative["alternative_id"] in (
                    relation["alternative_low_id"], relation["alternative_high_id"]
                ):
                    alternative["relation_ids"].append(relation["alternative_relation_id"])
        concept["relations"].sort(key=lambda item: (
            item["alternative_low_id"], item["alternative_high_id"],
            _key(item["phonological_parameter"]), item["alternative_relation_id"],
        ))
        concept["alternative_count"] = len(concept["alternatives"])
        concept["occurrence_count"] = sum(
            len(item["occurrences"]) for item in concept["alternatives"]
        )
    concepts.sort(key=lambda item: (_key(item["preferred_label"]), item["concept_id"]))
    return {"concepts": concepts}
