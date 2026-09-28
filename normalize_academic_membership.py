"""Explicit local-copy normalization; never starts the app or installs a schema.

python -B normalize_academic_membership.py --database COPY.db --manifest AUDIT.json --mode dry-run
python -B normalize_academic_membership.py --database COPY.db --manifest AUDIT.json --mode apply --reason TEXT
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
from classification_schema import KNOWLEDGE_AREAS

ROOT = Path(__file__).resolve().parent
FUTURE_AREAS = {'geografía', 'administración pública', 'investigación'}
FINAL_MANIFEST_NAME = 'academic_collection_audit_2026-09-28.json'
FINAL_MANIFEST_SHA256 = 'cee762fdd7e9c34aead0947b5ec4929a122f1b258ebd72e5e644631d43d9c580'


def key(value):
    return unicodedata.normalize('NFC', str(value or '').strip()).casefold()


def validate_manifest(document):
    """Validate editorial inputs without consulting or writing a database."""
    if not isinstance(document, dict):
        raise ValueError('Manifest must be an object')
    if type(document.get('manifest_version')) is not int or document['manifest_version'] != 1:
        raise ValueError('Unsupported manifest_version')
    if document.get('collection_code') != 'academic-vocabulary' or document.get('classification_system_code') != 'knowledge-areas':
        raise ValueError('Manifest collection/system mismatch')
    concepts = document.get('concepts')
    if not isinstance(concepts, list) or not concepts or type(document.get('concept_count')) is not int or document['concept_count'] != len(concepts):
        raise ValueError('Invalid concept_count')
    ids, labels = set(), set()
    for concept in concepts:
        if not isinstance(concept, dict):
            raise ValueError('Concept must be an object')
        ident, label, areas = concept.get('concept_id'), concept.get('preferred_label'), concept.get('areas')
        if type(ident) is not int or ident <= 0 or ident in ids:
            raise ValueError('Invalid or duplicate concept_id')
        if not isinstance(label, str) or not key(label) or key(label) in labels:
            raise ValueError('Empty or duplicate preferred_label')
        if not isinstance(areas, list) or not 1 <= len(areas) <= 2 or any(not isinstance(a, str) or not key(a) for a in areas):
            raise ValueError('Each Concept requires one or two nonempty areas')
        if len({key(a) for a in areas}) != len(areas):
            raise ValueError('Duplicate area within Concept')
        if any(key(a) in FUTURE_AREAS for a in areas):
            raise ValueError('Future area proposal is prohibited')
        ids.add(ident)
        labels.add(key(label))


def load_manifest(path):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate JSON key: ' + name)
            result[name] = value
        return result
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object)
    validate_manifest(document)
    if path.name == FINAL_MANIFEST_NAME and (digest != FINAL_MANIFEST_SHA256 or len(document['concepts']) != 386):
        raise ValueError('Final 2026-09-28 audit must match its approved hash and 386 Concepts')
    return document, digest, str(path)


def checks(db):
    return {'integrity_check': [r[0] for r in db.execute('PRAGMA integrity_check')],
            'foreign_key_check': [list(r) for r in db.execute('PRAGMA foreign_key_check')]}


def plan(db, manifest):
    result = dict(memberships_to_create=0, memberships_compatible=0,
                  revisions_to_create=0, revisions_compatible=0,
                  categories_to_create=0, categories_compatible=0,
                  conflicts=[], unmapped_values=[], unexpected_academic_memberships=[], operations=[])
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
    cid, sid = collection['collection_id'], system['system_id']
    categories = [dict(r) for r in db.execute('SELECT * FROM classification_category WHERE system_id=?', (sid,))]
    resolved = {}
    # Verify the stable controlled vocabulary, including code/name collisions.
    for order, name in enumerate(KNOWLEDGE_AREAS, 1):
        code = f'KA-{order:02}'
        matches = [r for r in categories if key(r['code']) == key(code) or key(r['name']) == key(name) or key(r['name_key']) == key(name)]
        if not matches and name == 'Administrativo':
            if any(r['display_order'] == 24 for r in categories):
                conflict('Display order 24 is occupied by another category')
            result['categories_to_create'] = 1
            resolved[key(name)] = dict(category_id=None, code=code, name=name)
        elif (len(matches) != 1 or matches[0]['code'] != code or matches[0]['name'] != name or
              matches[0]['name_key'] != key(name) or not matches[0]['active'] or
              (name == 'Administrativo' and matches[0]['display_order'] != 24)):
            conflict('Missing, ambiguous, inactive or incompatible category: ' + code + '/' + name)
        else:
            resolved[key(name)] = matches[0]
            result['categories_compatible'] += 1
    result['resolved_codes'] = {'academic-vocabulary': cid, 'knowledge-areas': sid,
                                'categories': {r['code']: r['category_id'] for r in resolved.values()}}
    target_ids = {c['concept_id'] for c in manifest['concepts']}
    for row in db.execute('SELECT concept_id FROM collection_membership WHERE collection_id=? AND ended_at IS NULL ORDER BY concept_id', (cid,)):
        if row[0] not in target_ids:
            result['unexpected_academic_memberships'].append(row[0])
            conflict('Academic membership outside final manifest', row[0])
    for row in db.execute('SELECT concept_id FROM concept_classification_revision WHERE system_id=? AND ended_at IS NULL', (sid,)):
        if row[0] not in target_ids:
            conflict('Academic classification outside final manifest', row[0])
    # Catch impossible open states even outside the legacy target set.
    for table, grouping in (('collection_membership', 'concept_id,collection_id'),
                            ('concept_classification_revision', 'concept_id,system_id')):
        for row in db.execute(f'SELECT concept_id FROM {table} WHERE ended_at IS NULL GROUP BY {grouping} HAVING count(*)>1'):
            conflict('Multiple current states in ' + table, row[0])
    for target in sorted(manifest['concepts'], key=lambda c: c['concept_id']):
        ident = target['concept_id']
        concept = db.execute('SELECT * FROM concept WHERE concept_id=?', (ident,)).fetchone()
        if not concept or concept['preferred_label'] != target['preferred_label']:
            conflict('Concept missing or preferred_label mismatch', ident)
            continue
        raw = [concept['knowledge_area_1'], concept['knowledge_area_2']]
        unknown = [v for v in target['areas'] if key(v) not in resolved]
        if unknown:
            result['unmapped_values'].append({'concept_id': ident, 'values': unknown})
            conflict('Unmapped or incompatible manifest area', ident)
            continue
        selected = [resolved[key(v)] for v in target['areas']]
        expected_ids = [r['category_id'] for r in selected] + [None] * (2-len(selected))
        expected_names = [r['name'] for r in selected] + [None] * (2-len(selected))
        expected_codes = [r['code'] for r in selected] + [None] * (2-len(selected))
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
                  [r['category_1_id'], r['category_2_id']] != expected_ids or
                  [r['category_1_code'], r['category_2_code']] != expected_codes or
                  [r['category_1_name'], r['category_2_name']] != expected_names or
                  r['system_code_snapshot'] != system['code'] or r['system_name_snapshot'] != system['name']):
            conflict('Current classification differs from manifest or its scope/snapshots', ident)
            continue
        result['memberships_compatible' if m else 'memberships_to_create'] += 1
        result['revisions_compatible' if r else 'revisions_to_create'] += 1
        result['operations'].append({'concept_id': ident, 'legacy_areas': raw,
                                     'target_areas': target['areas'], 'category_codes': [r['code'] for r in selected],
                                     'create_membership': m is None, 'create_revision': r is None})
    return result


def normalize(db, manifest, *, apply=False, reason=None, manifest_sha256=None, manifest_source='<in-memory>'):
    """Own the entire transaction; never accept an outer transaction."""
    validate_manifest(manifest)
    manifest_sha256 = manifest_sha256 or hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if db.in_transaction:
        raise ValueError('A dedicated connection without an active transaction is required')
    if apply and not (reason or '').strip():
        raise ValueError('APPLY requires an audit reason')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
    try:
        result = plan(db, manifest)
        result.update(manifest_sha256=manifest_sha256, manifest_concepts=len(manifest['concepts']))
        result.update(mode='apply' if apply else 'dry-run', changed_concepts=0)
        result['status'] = 'blocked' if result['conflicts'] else 'ready'
        if not apply or result['conflicts']:
            db.rollback()
            return result
        ids = result['resolved_codes']
        provenance = {'tool': 'normalize_academic_membership/v2', 'reason': reason,
                      'manifest': manifest_source, 'manifest_sha256': manifest_sha256,
                      'editorial_rule': 'Authoritative manifest areas determine academic membership'}
        if result['categories_to_create']:
            # administer() cannot set display_order. Record its existing audit shape
            # after this narrow insertion, including the complete final row.
            new_id = db.execute('''INSERT INTO classification_category
                (system_id,code,name,name_key,active,display_order)
                VALUES(?,'KA-24','Administrativo','administrativo',1,24)''', (ids['knowledge-areas'],)).lastrowid
            after_category = dict(db.execute('SELECT * FROM classification_category WHERE category_id=?', (new_id,)).fetchone())
            record_activity(db, 'classification_catalog_changed', entity_type='classification_category',
                            entity_id=new_id, access_role='master',
                            comment=json.dumps({'before': None, 'after': after_category, **provenance}, ensure_ascii=False))
            ids['categories']['KA-24'] = new_id
        for operation in result['operations']:
            if not (operation['create_membership'] or operation['create_revision']):
                continue
            payload = {'collections': {ids['academic-vocabulary']: 'join'} if operation['create_membership'] else {},
                       'classifications': {ids['knowledge-areas']: [ids['categories'][code] for code in operation['category_codes']]} if operation['create_revision'] else {}}
            decision = apply_metadata(db, operation['concept_id'], payload, access_role='master')
            record_activity(db, 'academic_membership_normalized', entity_type='concept',
                            entity_id=operation['concept_id'], access_role='master',
                            comment=json.dumps({**provenance, 'target_areas': operation['target_areas'],
                                'legacy_areas': operation['legacy_areas'], 'decision': decision}, ensure_ascii=False))
            result['changed_concepts'] += 1
        after = plan(db, manifest)
        if (after['conflicts'] or after['memberships_to_create'] or after['revisions_to_create'] or
                after['categories_to_create'] or after['memberships_compatible'] != len(manifest['concepts']) or
                after['revisions_compatible'] != len(manifest['concepts'])):
            raise ValueError('Post-apply validation failed: ' + json.dumps(after, ensure_ascii=True))
        result['postcheck'] = {k: after[k] for k in ('memberships_to_create','revisions_to_create',
                            'memberships_compatible','revisions_compatible','categories_to_create','categories_compatible',
                            'conflicts','integrity_check','foreign_key_check')}
        db.commit()
        result['status'] = 'applied' if result['changed_concepts'] or result['categories_to_create'] else 'unchanged'
        return result
    except Exception:
        db.rollback()
        raise


def run(path, *, manifest, apply=False, reason=None):
    document, digest, source = load_manifest(manifest)
    path = Path(path).resolve(strict=True)
    if apply:
        if not path.is_relative_to(ROOT):
            raise ValueError('APPLY only accepts a disposable copy inside this checkout')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
    try:
        if not apply:
            db.execute('PRAGMA query_only=ON')
        report = normalize(db, document, apply=apply, reason=reason, manifest_sha256=digest, manifest_source=source)
    finally:
        db.close()
    report.update(sha256_before=before, sha256_after=hashlib.sha256(path.read_bytes()).hexdigest())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--mode', choices=('dry-run', 'apply'), default='dry-run')
    parser.add_argument('--reason')
    parser.add_argument('--details', action='store_true')
    args = parser.parse_args()
    try:
        report = run(args.database, manifest=args.manifest, apply=args.mode == 'apply', reason=args.reason)
        if not args.details:
            report.pop('operations', None)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 2 if report['conflicts'] else 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({'status': 'aborted', 'error': str(error)}, ensure_ascii=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
