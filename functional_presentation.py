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
        not bool(row['current_label']), working_label_key(row['current_label'] or row['proposed_label']),
        row['alternative_id']))


def preview_status(row):
    if not row['current_label']:
        return '+ Nueva'
    if row['current_label'] == row['proposed_label']:
        return '= Sin cambio'
    current = working_label_key(row['current_label'])
    proposed = working_label_key(row['proposed_label'])
    if current[0] == proposed[0] == 0 and current[1] != proposed[1]:
        return '↻ Cambia de grupo'
    return '↻ Cambia'


def relation_target_error(connection, target_id, concept_id):
    """Explain an invalid historical target without resolving it by label."""
    target = connection.execute(
        'SELECT concept_id, retired_at FROM alternative WHERE alternative_id=?',
        (target_id,)).fetchone()
    prefix = f'No se puede aceptar esta relación porque la Alternativa destino ID {target_id}'
    if target is None:
        return prefix + ' no existe.'
    if target['retired_at'] is not None:
        return prefix + ' fue retirada.'
    if target['concept_id'] != concept_id:
        return prefix + ' pertenece a otro concepto.'
    return 'La relación propuesta ya no tiene un destino vigente del mismo concepto.'


def reference_basis_label(value):
    return {'source_single_year': 'año único de la fuente',
            'source_range_start': 'inicio del período de la fuente',
            'occurrence_year': 'año de la ocurrencia'}.get(value, value or '—')


def has_label_changes(rows):
    return any(row['current_label'] != row['proposed_label'] for row in rows)
