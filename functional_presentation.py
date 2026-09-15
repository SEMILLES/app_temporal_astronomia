"""Derived operational state and presentation order; no persistent changes."""
from alternative_nomenclature import working_label_key


def concept_options(connection, exclude_id=None):
    return connection.execute("""
        SELECT c.concept_id, c.preferred_label,
               EXISTS(SELECT 1 FROM alternative a WHERE a.concept_id=c.concept_id
                      AND a.retired_at IS NULL) AS has_active_alternatives
        FROM concept c WHERE (? IS NULL OR c.concept_id<>?)
        ORDER BY c.preferred_label,c.concept_id
    """, (exclude_id, exclude_id)).fetchall()


def preview_rows(rows):
    return sorted(rows, key=lambda row: (
        not bool(row['current_label']), working_label_key(row['current_label']),
        row['alternative_id']))


def reference_basis_label(value):
    return {'source_single_year': 'año único de la fuente',
            'source_range_start': 'inicio del período de la fuente',
            'occurrence_year': 'año de la ocurrencia'}.get(value, value or '—')


def has_label_changes(rows):
    return any(row['current_label'] != row['proposed_label'] for row in rows)
