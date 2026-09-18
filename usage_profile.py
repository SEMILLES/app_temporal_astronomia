"""Optional, current usage/context of an Alternative; no documentary evidence."""

FIELDS = {
    'frequency_impressionistic': 'Frecuencia reportada',
    'geographic_zone_general': 'Zona geográfica general',
    'geographic_zone_detail': 'Detalle geográfico',
    'age_group': 'Grupo etario',
    'socioeconomic_group': 'Grupo socioeconómico',
    'popular_etymology': 'Etimología popular',
    'iconicity_notes': 'Iconicidad',
    'register_notes': 'Registro',
    'semantic_pragmatic_nuance': 'Matiz semántico-pragmático',
    'spanish_relations': 'Relación con el español',
    'additional_notes': 'Observaciones',
}
ABSENCES = frozenset(('', '-', '---', 'n/a', 'no reporta'))


def normalize(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if value.casefold() in ABSENCES else value


SCHEMA = '''CREATE TABLE IF NOT EXISTS alternative_usage_profile (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alternative_id INTEGER NOT NULL UNIQUE REFERENCES alternative(alternative_id),
    ''' + ',\n    '.join(
        f"{field} TEXT CHECK({field} IS NULL OR (length(trim({field}, char(9)||char(10)||char(13)||' '))>0 "
        f"AND lower(trim({field}, char(9)||char(10)||char(13)||' ')) NOT IN ('-','---','n/a','no reporta')))"
        for field in FIELDS
    ) + ''',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)'''


def install(db):
    db.execute(SCHEMA)


def validate_schema(db):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(':memory:')) as reference:
        install(reference)
        for pragma in ('table_info', 'foreign_key_list', 'index_list'):
            actual = [tuple(r) for r in db.execute(f'PRAGMA {pragma}(alternative_usage_profile)')]
            expected = list(reference.execute(f'PRAGMA {pragma}(alternative_usage_profile)'))
            if actual != expected:
                raise ValueError(f'Esquema de perfil incompatible: {pragma}')
        actual = db.execute("SELECT sql FROM sqlite_master WHERE name='alternative_usage_profile'").fetchone()
        expected = reference.execute("SELECT sql FROM sqlite_master WHERE name='alternative_usage_profile'").fetchone()
        compact = lambda sql: ''.join(sql.replace('IF NOT EXISTS', '').split()).lower()
        if not actual or compact(actual[0]) != compact(expected[0]):
            raise ValueError('Restricciones de perfil incompatibles')


def public_profile(row):
    return {key: value for key in FIELDS if (value := normalize(row[key]))} if row else {}


def display_profile(profile):
    return [(FIELDS[key], value) for key in FIELDS if (value := normalize((profile or {}).get(key)))]
