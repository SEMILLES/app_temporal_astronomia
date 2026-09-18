"""Read-only acceptance checks for this specific reconciled DB upgrade."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from safe_database import check_integrity, ledger_digest
from import_profiles import import_rows, read_sheet
from post_reconciliation import corrections


def digest_table(db, table):
    digest=hashlib.sha256()
    for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        digest.update(repr(tuple(row)).encode('utf-8'))
    return digest.hexdigest()


def verify(path, baseline=None):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        check_integrity(db)
        counts={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in (
            'occurrence','concept','alternative','assignment','alternative_relation','alternative_usage_profile',
            'reconciliation_run','reconciliation_operation','alternative_media','occurrence_media')}
        if counts['alternative_usage_profile'] != 262:
            raise ValueError('Se requieren exactamente 262 perfiles')
        # These functions perform no writes when all postconditions match.
        pending=corrections(db)
        profiles=import_rows(db,read_sheet(Path(__file__).with_name('perfil_uso_contexto_consolidado.xlsx'),'Perfil consolidado'))
        if pending['changes'] or profiles['changes']:
            raise ValueError('La segunda ejecución no es idempotente')
        unaffected=[]
        if baseline:
            with closing(sqlite3.connect(Path(baseline).resolve().as_uri()+'?mode=ro',uri=True)) as before:
                expected_delta={'concept':1,'alternative':2,'assignment':3,'alternative_relation':1,'occurrence':0}
                for table,delta in expected_delta.items():
                    if counts[table]-before.execute(f'SELECT count(*) FROM {table}').fetchone()[0] != delta:
                        raise ValueError(f'Conteo inesperado: {table}')
                for (table,) in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    if table not in expected_delta:
                        if digest_table(db,table)!=digest_table(before,table):
                            raise ValueError(f'Tabla ajena modificada: {table}')
                        unaffected.append(table)
                # Only the three authorized documentary locator rows may change.
                old_occ={r[0]:tuple(r) for r in before.execute('SELECT * FROM occurrence')}
                changed=[r['legacy_occurrence_id'] for r in db.execute('SELECT * FROM occurrence') if tuple(r)!=old_occ[r['occurrence_id']]]
                if set(changed)!={'10787-EMPANADA','10540-COLOMBIA','10787-COLOMBIA'}:
                    raise ValueError(f'Occurrences inesperadas modificadas: {changed}')
        return {'counts':counts,'integrity_check':'ok','foreign_key_check':0,'ledger':ledger_digest(db),
                'unchanged_tables':unaffected,'corrections_second_changes':pending['changes'],
                'import_second_changes':profiles['changes'],'resolved_ids':profiles['resolved_ids']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--baseline')
    parser.add_argument('--report')
    args=parser.parse_args()
    result=verify(args.database,args.baseline)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.report:Path(args.report).write_text(output+'\n',encoding='utf-8')
    print(output)
