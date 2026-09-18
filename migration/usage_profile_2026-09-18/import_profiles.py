"""Import the audited workbook by authoritative IDs, never by legacy guesses."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from usage_profile import FIELDS, normalize, validate_schema
from safe_database import run

HEADERS = dict(zip(FIELDS, (
    'Frecuencia reportada', 'Región general', 'Región detalle', 'Grupo etario',
    'Grupo socioeconómico', 'Etimología popular', 'Iconicidad', 'Registro',
    'Matiz semántico-pragmático', 'Relaciones con español', 'Observaciones adicionales',
)))
EXCEPTIONS = {'MAPA-COLOMBIA-1b': ('MAPA-COLOMBIA', '1b'),
              'MÁS/MENOS-QUÉ-1a': ('MÁS/MENOS-QUÉ', '1a')}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_sheet(path, name):
    """Small read-only OOXML reader; rejects formulas rather than stale caches."""
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        matches = [n for n in workbook.findall('s:sheets/s:sheet', NS) if n.attrib['name'] == name]
        if len(matches) != 1:
            raise ValueError(f'Hoja ausente o ambigua: {name}')
        rid = matches[0].attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next(n.attrib['Target'] for n in relationships if n.attrib['Id'] == rid)
        member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        result = []
        for row in ET.fromstring(archive.read(member)).findall('s:sheetData/s:row', NS):
            values = {}
            for cell in row.findall('s:c', NS):
                if cell.find('s:f', NS) is not None:
                    raise ValueError(f'Fórmula inesperada en {name}: {cell.attrib["r"]}')
                value = cell.findtext('s:v', default='', namespaces=NS)
                if cell.attrib.get('t') == 's':
                    value = strings[int(value)]
                elif cell.attrib.get('t') == 'inlineStr':
                    value = ''.join(n.text or '' for n in cell.findall('s:is//s:t', NS))
                column = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                values[column] = value
            if any(normalize(v) for v in values.values()):
                result.append(values)
        if not result:
            raise ValueError(f'Hoja vacía: {name}')
        header = result.pop(0)
        if len(set(header.values())) != len(header):
            raise ValueError('Columnas duplicadas')
        return [{label: values.get(column) for column, label in header.items()} for values in result]


def import_rows(db, rows, *, allow_update=False, expected_count=262):
    validate_schema(db)
    if len(rows) != expected_count:
        raise ValueError(f'Se esperaban {expected_count} filas; recibidas {len(rows)}')
    report = {'rows_read': len(rows), 'created': 0, 'matching': 0, 'updated': 0,
              'null_fields': 0, 'resolved_ids': {}, 'changes': 0, 'errors': []}
    seen = set()
    for number, row in enumerate(rows, 2):
        required = {*HEADERS.values(), 'Alternative actual', 'ID Alternative actual'}
        if not required <= row.keys():
            raise ValueError(f'Columnas ausentes: {sorted(required - row.keys())}')
        name = normalize(row['Alternative actual'])
        identifier = normalize(row['ID Alternative actual'])
        if identifier is None:
            if name not in EXCEPTIONS:
                raise ValueError(f'Fila {number}: ID vacío no autorizado ({name})')
            found = db.execute('''SELECT a.alternative_id FROM alternative a JOIN concept c USING(concept_id)
                WHERE c.preferred_label=? AND a.working_label=? AND a.retired_at IS NULL''', EXCEPTIONS[name]).fetchall()
            if len(found) != 1:
                raise ValueError(f'Fila {number}: resolución no unívoca de {name}')
            identifier = found[0][0]
            report['resolved_ids'][name] = identifier
        else:
            try:
                identifier = int(identifier)
            except ValueError:
                raise ValueError(f'Fila {number}: ID no entero') from None
        actual = db.execute('''SELECT c.preferred_label || '-' || a.working_label FROM alternative a
            JOIN concept c USING(concept_id) WHERE a.alternative_id=? AND a.retired_at IS NULL''', (identifier,)).fetchone()
        if not actual or actual[0] != name:
            raise ValueError(f'Fila {number}: identidad canónica incompatible: {identifier}, {name}')
        if identifier in seen:
            raise ValueError(f'Alternative duplicada: {identifier}')
        seen.add(identifier)
        values = tuple(normalize(row[HEADERS[field]]) for field in FIELDS)
        if not any(values):
            raise ValueError(f'Fila {number}: perfil sin contenido sustantivo')
        report['null_fields'] += values.count(None)
        current = db.execute(f'SELECT {",".join(FIELDS)} FROM alternative_usage_profile WHERE alternative_id=?', (identifier,)).fetchone()
        if current is None:
            db.execute(f'INSERT INTO alternative_usage_profile(alternative_id,{",".join(FIELDS)}) VALUES({",".join("?" for _ in range(len(FIELDS)+1))})', (identifier, *values))
            report['created'] += 1
        elif tuple(current) == values:
            report['matching'] += 1
        elif allow_update:
            db.execute(f'UPDATE alternative_usage_profile SET {",".join(f"{field}=?" for field in FIELDS)},updated_at=CURRENT_TIMESTAMP WHERE alternative_id=?', (*values, identifier))
            report['updated'] += 1
        else:
            raise ValueError(f'Perfil diferente: {name}; requiere --allow-update explícito')
    report['changes'] = report['created'] + report['updated']
    report['total_profiles'] = db.execute('SELECT count(*) FROM alternative_usage_profile').fetchone()[0]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--workbook', type=Path, default=Path(__file__).with_name('perfil_uso_contexto_consolidado.xlsx'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--allow-update', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--report')
    args = parser.parse_args()
    try:
        rows = read_sheet(args.workbook, 'Perfil consolidado')
        report = run(args.database, lambda db: import_rows(db, rows, allow_update=args.allow_update), apply=args.apply, backup=args.backup)
        report['workbook_sha256'] = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    except Exception as error:
        report = {'error': str(error)}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output + '\n', encoding='utf-8')
    print(output)
    return int('error' in report)


if __name__ == '__main__':
    raise SystemExit(main())
