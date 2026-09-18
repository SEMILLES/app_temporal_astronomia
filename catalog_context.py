"""Live catalog enrichment, deliberately separate from immutable publications."""
from concept_classification import ClassificationError
from usage_profile import normalize


def semantic_fields(db, concept_id, legacy):
    rows = db.execute('''SELECT r.category_1_name,r.category_2_name
        FROM concept_classification_revision r JOIN classification_system s USING(system_id)
        WHERE r.concept_id=? AND s.code='semantic-fields' AND r.ended_at IS NULL''', (concept_id,)).fetchall()
    if len(rows) > 1:
        raise ClassificationError('Más de una clasificación semántica vigente')
    # An explicit empty revision means empty, not permission to revive legacy.
    values = tuple(rows[0]) if rows else legacy
    return list(dict.fromkeys(v for raw in values if (v := normalize(raw))))[:2]


def enrich_catalog(db, projection):
    for concept in projection['concepts']:
        concept['semantic_fields'] = semantic_fields(db, concept['concept_id'], concept['semantic_fields'])
        concept['knowledge_areas'] = [value for raw in concept['knowledge_areas'] if (value := normalize(raw))]
    return projection
