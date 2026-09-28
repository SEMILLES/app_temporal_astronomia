"""Explicit local-copy normalization; never starts the app or installs a schema.

python -B normalize_academic_membership.py --database COPY.db --mode dry-run
python -B normalize_academic_membership.py --database COPY.db --mode apply --reason TEXT
Use --details to include every planned Concept operation in the JSON output.
APPLY is intentionally restricted to copies inside this checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata

from activity import record_activity
from concept_classification import apply_metadata

ROOT = Path(__file__).resolve().parent
ABSENT = {'', '-', '---', 'n/a', 'no reporta'}


def key(value):
    return unicodedata.normalize('NFC', str(value or '').strip()).casefold()


def checks(db):
    return {'integrity_check': [r[0] for r in db.execute('PRAGMA integrity_check')],
            'foreign_key_check': [list(r) for r in db.execute('PRAGMA foreign_key_check')]}


def plan(db):
    result = dict(memberships_to_create=0, memberships_compatible=0,
                  revisions_to_create=0, revisions_compatible=0,
                  conflicts=[], unmapped_values=[], operations=[])
    def conflict(message, concept_id=None):
        result['conflicts'].append({'concept_id': concept_id, 'reason': message})
    health = checks(db)
    result.update(health)
    if health['integrity_check'] != ['ok'] or health['foreign_key_check']:
        conflict('Database integrity or foreign keys failed')
        return result
    collection = db.execute("SELECT * FROM collection WHERE code='academic-vocabulary'").fetchone()
    system = db.execute("SELECT * FROM classification_system WHERE code='knowledge-areas'").fetchone()
    if not collection or not collection['active']:
        conflict('academic-vocabulary missing or inactive')
    if not system or not system['active'] or not collection or system['collection_id'] != collection['collection_id']:
        conflict('knowledge-areas missing, inactive or incorrectly scoped')
    if result['conflicts']:
        return result
    category = db.execute("SELECT * FROM classification_category WHERE system_id=? AND code='KA-01'", (system['system_id'],)).fetchone()
    if not category or not category['active'] or key(category['name']) != 'astronomía':
        conflict('KA-01/Astronomía missing, inactive or incompatible')
        return result
    cid, sid, aid = collection['collection_id'], system['system_id'], category['category_id']
    result['resolved_codes'] = {'academic-vocabulary': cid, 'knowledge-areas': sid, 'KA-01': aid}
    # Catch impossible open states even outside the legacy target set.
    for table, grouping in (('collection_membership', 'concept_id,collection_id'),
                            ('concept_classification_revision', 'concept_id,system_id')):
        for row in db.execute(f'SELECT concept_id FROM {table} WHERE ended_at IS NULL GROUP BY {grouping} HAVING count(*)>1'):
            conflict('Multiple current states in ' + table, row[0])
    for concept in db.execute('SELECT * FROM concept ORDER BY concept_id'):
        raw = [concept['knowledge_area_1'], concept['knowledge_area_2']]
        values = [key(v) for v in raw if key(v) not in ABSENT]
        if not values:
            continue
        ident = concept['concept_id']
        unknown = [v for v in raw if key(v) not in ABSENT and key(v) != 'astronomía']
        if unknown:
            result['unmapped_values'].append({'concept_id': ident, 'values': unknown})
            conflict('Unmapped legacy knowledge area', ident)
            continue
        members = db.execute('SELECT * FROM collection_membership WHERE concept_id=? AND collection_id=? ORDER BY membership_id', (ident,cid)).fetchall()
        revisions = db.execute('SELECT * FROM concept_classification_revision WHERE concept_id=? AND system_id=? ORDER BY revision_id', (ident,sid)).fetchall()
        active_m = [r for r in members if r['ended_at'] is None]
        active_r = [r for r in revisions if r['ended_at'] is None]
        if len(active_m)>1 or len(active_r)>1:
            continue  # Already reported above.
        m = active_m[0] if active_m else None
        r = active_r[0] if active_r else None
        if (members and not m) or (revisions and not r):
            conflict('Closed history requires explicit review; no automatic reopening', ident)
            continue
        if m and (m['collection_code_snapshot'] != collection['code'] or m['collection_name_snapshot'] != collection['name']):
            conflict('Incompatible current membership snapshot', ident)
            continue
        if r and (not m or r['membership_id'] != m['membership_id'] or
                  r['category_1_id'] != aid or r['category_2_id'] is not None or
                  r['category_1_code'] != category['code'] or r['category_1_name'] != category['name'] or
                  r['system_code_snapshot'] != system['code'] or r['system_name_snapshot'] != system['name']):
            conflict('Current classification differs from Astronomía or its scope/snapshots', ident)
            continue
        result['memberships_compatible' if m else 'memberships_to_create'] += 1
        result['revisions_compatible' if r else 'revisions_to_create'] += 1
        result['operations'].append({'concept_id': ident, 'legacy_areas': raw,
                                     'create_membership': m is None, 'create_revision': r is None})
    return result


def normalize(db, *, apply=False, reason=None):
    """Own the entire transaction; never accept an outer transaction."""
    if db.in_transaction:
        raise ValueError('A dedicated connection without an active transaction is required')
    if apply and not (reason or '').strip():
        raise ValueError('APPLY requires an audit reason')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
    try:
        result = plan(db)
        result.update(mode='apply' if apply else 'dry-run', changed_concepts=0)
        result['status'] = 'blocked' if result['conflicts'] else 'ready'
        if not apply or result['conflicts']:
            db.rollback()
            return result
        ids = result['resolved_codes']
        for operation in result['operations']:
            if not (operation['create_membership'] or operation['create_revision']):
                continue
            payload = {'collections': {ids['academic-vocabulary']: 'join'} if operation['create_membership'] else {},
                       'classifications': {ids['knowledge-areas']: [ids['KA-01']]} if operation['create_revision'] else {}}
            decision = apply_metadata(db, operation['concept_id'], payload, access_role='master')
            record_activity(db, 'academic_membership_normalized', entity_type='concept',
                            entity_id=operation['concept_id'], access_role='master',
                            comment=json.dumps({'tool': 'normalize_academic_membership/v1',
                                'reason': reason, 'editorial_rule': 'legacy knowledge area determines academic membership',
                                'legacy_areas': operation['legacy_areas'], 'decision': decision}, ensure_ascii=False))
            result['changed_concepts'] += 1
        after = plan(db)
        if after['conflicts'] or after['memberships_to_create'] or after['revisions_to_create']:
            raise ValueError('Post-apply validation failed: ' + json.dumps(after, ensure_ascii=True))
        result['postcheck'] = {k: after[k] for k in ('memberships_to_create','revisions_to_create',
                            'memberships_compatible','revisions_compatible','conflicts','integrity_check','foreign_key_check')}
        db.commit()
        result['status'] = 'applied' if result['changed_concepts'] else 'unchanged'
        return result
    except Exception:
        db.rollback()
        raise


def run(path, *, apply=False, reason=None):
    path = Path(path).resolve(strict=True)
    if apply:
        if not path.is_relative_to(ROOT):
            raise ValueError('APPLY only accepts a disposable copy inside this checkout')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
    try:
        if not apply:
            db.execute('PRAGMA query_only=ON')
        report = normalize(db, apply=apply, reason=reason)
    finally:
        db.close()
    report.update(sha256_before=before, sha256_after=hashlib.sha256(path.read_bytes()).hexdigest())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--mode', choices=('dry-run', 'apply'), default='dry-run')
    parser.add_argument('--reason')
    parser.add_argument('--details', action='store_true')
    args = parser.parse_args()
    try:
        report = run(args.database, apply=args.mode == 'apply', reason=args.reason)
        if not args.details:
            report.pop('operations', None)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 2 if report['conflicts'] else 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({'status': 'aborted', 'error': str(error)}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
