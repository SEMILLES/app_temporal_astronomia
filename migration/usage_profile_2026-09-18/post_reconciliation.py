"""Only the four explicitly confirmed Pendientes BD. No inferred decisions."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from safe_database import cli

ACTOR = 'usage-profile-2026-09-18'
# Complete legacy identity + gloss + audited source, never a numeric prefix alone.
CASES = (
    ('10787-EMPANADA', 'EMPANADA', 'Proyecto Lengua de Señas Colombiana', 'EMPANADA', '1a', '1:20', None, False),
    ('10540-COLOMBIA', 'COLOMBIA', 'INSOR - YouTube - Glosarios Académicos', 'COLOMBIA', '1a', '1:18', '01:16', True),
    ('10787-COLOMBIA', 'COLOMBIA', 'INSOR - YouTube - Glosarios Académicos', 'MAPA-COLOMBIA', '1b', '1:21', None, False),
    ('3155-SUPERIORIDAD EN UNA CUALIDAD', 'SUPERIORIDAD EN UNA CUALIDAD', 'Fenascol - Tomo I (corregido)', 'MÁS/MENOS-QUÉ', '1a', None, None, False),
    ('9567-PREGUNTAR', 'PREGUNTAR', 'Sordos Juan N. Cadavid', 'PREGUNTAR', '1b', None, None, True),
    ('10973-PREGUNTAR', 'PREGUNTAR', 'Convenio Insor Universidad Nacional 2024', 'PREGUNTAR', '1c', None, None, True),
)


def unique(rows, description):
    if len(rows) != 1:
        raise ValueError(f'Identidad ausente o ambigua: {description}')
    return rows[0]


def alternative(db, concept, label, events, *, create=False):
    concepts = db.execute('SELECT concept_id FROM concept WHERE preferred_label=?', (concept,)).fetchall()
    if not concepts and create and concept == 'MÁS/MENOS-QUÉ':
        identifier = db.execute('INSERT INTO concept(preferred_label) VALUES(?)', (concept,)).lastrowid
        events.append({'action': 'create_concept', 'concept_id': identifier, 'name': concept})
    else:
        identifier = unique(concepts, concept)[0]
    rows = db.execute('SELECT * FROM alternative WHERE concept_id=? AND working_label=?', (identifier, label)).fetchall()
    if not rows and create:
        aid = db.execute('INSERT INTO alternative(concept_id,working_label,created_by) VALUES(?,?,?)', (identifier, label, ACTOR)).lastrowid
        events.append({'action': 'create_alternative', 'alternative_id': aid, 'name': f'{concept}-{label}'})
        return aid
    row = unique(rows, f'{concept}-{label}')
    if row['retired_at'] is not None:
        # A retired identity may have history requiring a linguistic decision.
        raise ValueError(f'Alternative retirada inesperada: {concept}-{label}')
    return row['alternative_id']


def corrections(db):
    events, satisfied = [], []
    for legacy, gloss, source, concept, label, locator, old_detail, must_exist in CASES:
        occurrence = unique(db.execute('''SELECT o.* FROM occurrence o JOIN source s USING(source_id)
            WHERE o.legacy_occurrence_id=? AND o.original_gloss=? AND s.source_name=?''', (legacy, gloss, source)).fetchall(), legacy)
        aid = alternative(db, concept, label, events, create=(concept, label) in (('MAPA-COLOMBIA', '1b'), ('MÁS/MENOS-QUÉ', '1a')))
        assignments = db.execute('SELECT * FROM assignment WHERE occurrence_id=? AND is_current=1', (occurrence['occurrence_id'],)).fetchall()
        if len(assignments) > 1 or assignments and assignments[0]['alternative_id'] != aid:
            raise ValueError(f'Assignment actual inesperado: {legacy}')
        if not assignments:
            if must_exist:
                raise ValueError(f'Assignment confirmado ausente: {legacy}')
            assigned = db.execute('INSERT INTO assignment(occurrence_id,alternative_id,created_by) VALUES(?,?,?)', (occurrence['occurrence_id'], aid, ACTOR)).lastrowid
            events.append({'action': 'assign', 'legacy': legacy, 'assignment_id': assigned, 'alternative_id': aid})
        else:
            satisfied.append({'legacy': legacy, 'alternative_id': aid, 'status': 'assignment_already_satisfied'})
        if locator:
            # source_detail_2 is the current documentary time locator. Keep the
            # older source_locator consistent too; never alter the source itself.
            if occurrence['source_locator'] not in (None, '', locator) or occurrence['source_detail_2'] not in (None, '', old_detail, locator):
                raise ValueError(f'Localizador inesperado: {legacy}')
            if occurrence['source_locator'] != locator or occurrence['source_detail_2'] != locator or occurrence['source_detail_2_status'] != 'VALUE':
                db.execute('''UPDATE occurrence SET source_locator=?,source_detail_2=?,source_detail_2_status='VALUE',
                    updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE occurrence_id=?''', (locator, locator, ACTOR, occurrence['occurrence_id']))
                events.append({'action': 'locator', 'legacy': legacy, 'before': [occurrence['source_locator'], occurrence['source_detail_2']], 'after': locator})
        if legacy == '10973-PREGUNTAR' and (occurrence['source_locator'] or occurrence['source_detail_2']):
            raise ValueError('10973-PREGUNTAR tiene localizador inesperado')
    first = alternative(db, 'MAPA-COLOMBIA', '1a', events)
    second = alternative(db, 'MAPA-COLOMBIA', '1b', events)
    low, high = sorted((first, second))
    relations = db.execute('''SELECT * FROM alternative_relation WHERE is_current=1 AND
        (alternative_low_id=? OR alternative_high_id=?)''', (second, second)).fetchall()
    if any((r['alternative_low_id'], r['alternative_high_id'], r['phonological_parameter']) != (low, high, 'N_MANOS') for r in relations):
        raise ValueError('Relaciones inesperadas en MAPA-COLOMBIA-1b')
    if len(relations) > 1:
        raise ValueError('Relación N_MANOS duplicada')
    if not relations:
        db.execute('''INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter,created_by)
            VALUES(?,?,'N_MANOS',?)''', (low, high, ACTOR))
        events.append({'action': 'relation', 'low': low, 'high': high, 'parameter': 'N_MANOS'})
    # All cases rechecked after writes; caller owns commit/rollback and integrity.
    for legacy, gloss, source, concept, label, locator, _, _ in CASES:
        row = unique(db.execute('''SELECT a.alternative_id,o.source_locator,o.source_detail_2 FROM occurrence o JOIN source s USING(source_id)
            JOIN assignment a ON a.occurrence_id=o.occurrence_id AND a.is_current=1
            JOIN alternative x ON x.alternative_id=a.alternative_id JOIN concept c USING(concept_id)
            WHERE o.legacy_occurrence_id=? AND o.original_gloss=? AND s.source_name=? AND c.preferred_label=? AND x.working_label=? AND x.retired_at IS NULL''',
            (legacy, gloss, source, concept, label)).fetchall(), legacy)
        if locator and tuple(row)[1:] != (locator, locator):
            raise ValueError(f'Validación de localizador falló: {legacy}')
    return {'changes': len(events), 'events': events, 'satisfied': satisfied, 'validated_cases': len(CASES)}


if __name__ == '__main__':
    raise SystemExit(cli(corrections, 'Aplicar exclusivamente los Pendientes BD confirmados'))
