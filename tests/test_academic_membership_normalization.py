import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from database import crear_esquema
from concept_classification import apply_metadata
from normalize_academic_membership import ROOT, normalize, run, load_manifest, validate_manifest


class AcademicNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        crear_esquema(self.db)
        for name in ('UNO', 'DOS'):
            self.db.execute("INSERT INTO concept(preferred_label,knowledge_area_1,semantic_field_1) VALUES(?,'ASTRONOMÍA','Animales')", (name,))
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('NO ACADEMICO')")
        self.db.commit()
        self.addCleanup(self.db.close)
        self.manifest = {'manifest_version': 1, 'collection_code': 'academic-vocabulary',
                         'classification_system_code': 'knowledge-areas', 'concept_count': 2,
                         'concepts': [{'concept_id': i, 'preferred_label': name, 'areas': ['Astronomía']}
                                      for i, name in ((1, 'UNO'), (2, 'DOS'))]}
        self.cid = self.db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        self.sid = self.db.execute("SELECT system_id FROM classification_system WHERE code='knowledge-areas'").fetchone()[0]
        self.aid = self.db.execute("SELECT category_id FROM classification_category WHERE system_id=? AND code='KA-01'", (self.sid,)).fetchone()[0]

    def normalize(self, db=None, **kwargs):
        return normalize(self.db if db is None else db, self.manifest, **kwargs)

    def run_file(self, path, **kwargs):
        fd, name = tempfile.mkstemp(suffix='.json', dir=ROOT)
        os.close(fd)
        manifest = Path(name)
        try:
            manifest.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8')
            report = run(path, manifest=manifest, **kwargs)
            self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), report['manifest_sha256'])
            return report
        finally:
            manifest.unlink()

    def remove_admin(self):
        trigger = self.db.execute("SELECT sql FROM sqlite_master WHERE name='no_delete_classification_category'").fetchone()[0]
        self.db.execute('DROP TRIGGER no_delete_classification_category')
        self.db.execute("DELETE FROM classification_category WHERE code='KA-24' AND system_id=?", (self.sid,))
        self.db.execute(trigger)
        self.db.commit()

    def dump(self):
        return '\n'.join(self.db.iterdump())

    def test_manifest_validation(self):
        validate_manifest(self.manifest)
        self.assertEqual(2, self.normalize()['manifest_concepts'])
        invalid = []
        for field, value in [('manifest_version', 2), ('manifest_version', True),
                             ('collection_code', 'wrong'), ('classification_system_code', 'wrong'),
                             ('concept_count', 386), ('concept_count', True)]:
            item = copy.deepcopy(self.manifest); item[field] = value; invalid.append(item)
        for field, value in [('concept_id', 1), ('preferred_label', 'UNO')]:
            item = copy.deepcopy(self.manifest); item['concepts'][1][field] = value; invalid.append(item)
        for areas in ([], [''], ['Astronomía', 'Astronomía'], ['Astronomía', 'Física', 'Química'],
                      ['Geografía'], ['Administración Pública'], ['Investigación']):
            item = copy.deepcopy(self.manifest); item['concepts'][0]['areas'] = areas; invalid.append(item)
        before = self.dump()
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                normalize(self.db, item, apply=True, reason='Invalid manifest')
            self.assertEqual(before, self.dump())

    def test_migration_022_accepts_original_23_areas_without_writes(self):
        import importlib
        migration = importlib.import_module('migrations.022_concept_classification')
        self.remove_admin()
        reference = migration.reference_schema()
        try:
            before = self.dump()
            migration.check_seeds(self.db, reference, initial=False)
            self.assertEqual(before, self.dump())
            with self.assertRaises(migration.MigrationError):
                migration.check_seeds(self.db, reference, initial=True)
        finally: reference.close()

    def test_manifest_order_changes_are_conflicts(self):
        self.manifest['concepts'][0]['areas'] = ['Astronomía', 'Física']
        self.normalize(apply=True, reason='Original order')
        self.manifest['concepts'][0]['areas'].reverse()
        before = self.dump()
        self.assertTrue(self.normalize(apply=True, reason='Reordered')['conflicts'])
        self.assertEqual(before, self.dump())

    def test_administrativo_name_collision(self):
        self.remove_admin()
        self.db.execute("INSERT INTO classification_category(system_id,code,name,name_key) VALUES(?,'CUSTOM','Administrativo','administrativo')", (self.sid,))
        self.db.commit()
        before = self.dump()
        self.assertTrue(self.normalize(apply=True, reason='Collision')['conflicts'])
        self.assertEqual(before, self.dump())

    def test_missing_concept_and_label_mismatch(self):
        for field, value in [('concept_id', 999), ('preferred_label', 'Mismatch')]:
            with self.subTest(field=field):
                document = copy.deepcopy(self.manifest)
                document['concepts'][0][field] = value
                before = self.dump()
                self.assertTrue(normalize(self.db, document, apply=True, reason='Mismatch')['conflicts'])
                self.assertEqual(before, self.dump())

    def test_administrativo_created_with_audit_and_two_areas(self):
        self.remove_admin()
        self.manifest['concepts'][0]['areas'] = ['Administrativo', 'Economía y Administración']
        before = self.dump()
        preview = self.normalize()
        self.assertEqual((1, 23), (preview['categories_to_create'], preview['categories_compatible']))
        self.assertEqual(before, self.dump())
        self.normalize(apply=True, reason='Editorial audit')
        category = self.db.execute("SELECT * FROM classification_category WHERE system_id=? AND code='KA-24'", (self.sid,)).fetchone()
        self.assertEqual(('Administrativo', 24, 1), (category['name'], category['display_order'], category['active']))
        row = self.db.execute('SELECT * FROM concept_classification_revision WHERE concept_id=1').fetchone()
        self.assertEqual(('KA-24', 'KA-14'), (row['category_1_code'], row['category_2_code']))
        event = self.db.execute("SELECT comment FROM activity_event WHERE event_type='classification_catalog_changed'").fetchone()
        audit = json.loads(event[0])
        self.assertIsNone(audit['before'])
        self.assertEqual(24, audit['after']['display_order'])
        self.assertIn('manifest_sha256', audit)
        before = self.dump()
        self.assertEqual('unchanged', self.normalize(apply=True, reason='Again')['status'])
        self.assertEqual(before, self.dump())

    def test_seed_administrativo_preexisting_compatible(self):
        row = self.db.execute("SELECT name,display_order FROM classification_category WHERE system_id=? AND code='KA-24'", (self.sid,)).fetchone()
        self.assertEqual(('Administrativo', 24), tuple(row))
        report = self.normalize(apply=True, reason='Already seeded')
        self.assertEqual((0, 24), (report['categories_to_create'], report['categories_compatible']))
        self.assertEqual(0, self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='classification_catalog_changed'").fetchone()[0])
        self.assertEqual(0, self.db.execute("SELECT count(*) FROM classification_category WHERE name IN ('Geografía','Administración Pública','Investigación')").fetchone()[0])

    def test_incompatible_administrativo(self):
        for column, value in [('name', 'Wrong'), ('name_key', 'wrong'), ('display_order', 0), ('active', 0)]:
            with self.subTest(column=column):
                before_row = dict(self.db.execute("SELECT * FROM classification_category WHERE system_id=? AND code='KA-24'", (self.sid,)).fetchone())
                self.db.execute(f"UPDATE classification_category SET {column}=? WHERE category_id=?", (value, before_row['category_id']))
                self.db.commit()
                before = self.dump()
                self.assertTrue(self.normalize(apply=True, reason='Incompatible')['conflicts'])
                self.assertEqual(before, self.dump())
                self.db.execute(f'UPDATE classification_category SET {column}=? WHERE category_id=?', (before_row[column], before_row['category_id']))
                self.db.commit()

    def test_extra_membership_aborts(self):
        apply_metadata(self.db, 3, {'collections': {self.cid: 'join'}}, access_role='master')
        before = self.dump()
        report = self.normalize(apply=True, reason='Extra membership')
        self.assertEqual([3], report['unexpected_academic_memberships'])
        self.assertTrue(report['conflicts'])
        self.assertEqual(before, self.dump())

    def test_manifest_authority_does_not_depend_on_legacy(self):
        self.db.execute('UPDATE concept SET knowledge_area_1=NULL')
        self.db.commit()
        self.assertEqual(2, self.normalize(apply=True, reason='Manifest authority')['changed_concepts'])
        self.assertEqual(0, self.db.execute('SELECT count(*) FROM concept WHERE knowledge_area_1 IS NOT NULL').fetchone()[0])

    def test_load_manifest_rejects_duplicate_json_keys_and_unapproved_final(self):
        fd, name = tempfile.mkstemp(suffix='.json', dir=ROOT); os.close(fd)
        path = Path(name)
        try:
            path.write_text('{"manifest_version":1,"manifest_version":1}', encoding='utf-8')
            with self.assertRaises(ValueError): load_manifest(path)
            path.write_text(json.dumps(self.manifest), encoding='utf-8')
            from unittest.mock import patch
            with patch('normalize_academic_membership.FINAL_MANIFEST_NAME', path.name):
                with self.assertRaises(ValueError): load_manifest(path)
        finally: path.unlink()

    def test_dry_run_query_only_and_no_changes(self):
        before = self.dump()
        self.db.execute('PRAGMA query_only=ON')
        report = self.normalize()
        self.assertEqual((2,2,[]), (report['memberships_to_create'], report['revisions_to_create'], report['conflicts']))
        self.assertEqual(before, self.dump())

    def test_apply_idempotency_preservation_and_audit(self):
        legacy = [tuple(r) for r in self.db.execute('SELECT * FROM concept')]
        report = self.normalize( apply=True, reason='Synthetic validation')
        self.assertEqual(2, report['changed_concepts'])
        self.assertEqual(legacy, [tuple(r) for r in self.db.execute('SELECT * FROM concept')])
        self.assertEqual(0, self.db.execute('SELECT count(*) FROM submission').fetchone()[0])
        rows = self.db.execute('SELECT * FROM concept_classification_revision').fetchall()
        self.assertEqual(2,len(rows))
        for r in rows:
            self.assertEqual((self.sid,self.aid,'KA-01','master',None,None),
                (r['system_id'],r['category_1_id'],r['category_1_code'],r['access_role'],r['resolution_id'],r['collaborator_id']))
            self.assertIsNotNone(r['started_at'])
        events = self.db.execute("SELECT comment FROM activity_event WHERE event_type='academic_membership_normalized'").fetchall()
        self.assertEqual(2,len(events))
        self.assertEqual(['ASTRONOMÍA',None],json.loads(events[0][0])['legacy_areas'])
        before = self.dump()
        again = self.normalize( apply=True, reason='Second execution')
        self.assertEqual((0,0,0,[]),(again['memberships_to_create'],again['revisions_to_create'],again['changed_concepts'],again['conflicts']))
        self.assertEqual(before,self.dump())
        self.assertEqual(2,self.db.execute('SELECT count(*) FROM collection_membership').fetchone()[0])

    def test_conflicting_revision_aborts_entire_plan(self):
        other = self.db.execute("SELECT category_id FROM classification_category WHERE system_id=? AND code='KA-02'",(self.sid,)).fetchone()[0]
        apply_metadata(self.db,2,{'collections':{self.cid:'join'},'classifications':{self.sid:[other]}},access_role='master')
        before = self.dump()
        report = self.normalize(apply=True,reason='Must abort')
        self.assertTrue(report['conflicts'])
        self.assertEqual(0,report['changed_concepts'])
        self.assertEqual(before,self.dump())

    def test_rollback_after_mid_apply_failure(self):
        self.remove_admin()
        self.manifest['concepts'][0]['areas'] = ['Administrativo', 'Astronomía']
        self.db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON concept_classification_revision WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.db.commit()
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            self.normalize(apply=True,reason='Rollback validation')
        self.assertEqual(before,self.dump())

    def test_unknown_area_never_becomes_other(self):
        self.manifest['concepts'][1]['areas'] = ['Unknown area']
        before = self.dump()
        report = self.normalize(apply=True,reason='Unknown area')
        self.assertEqual([{'concept_id':2,'values':['Unknown area']}],report['unmapped_values'])
        self.assertEqual(before,self.dump())

    def test_inactive_definitions(self):
        for table, column, value in (('collection','collection_id',self.cid),('classification_system','system_id',self.sid),('classification_category','category_id',self.aid)):
            with self.subTest(table=table):
                self.db.execute(f'UPDATE {table} SET active=0 WHERE {column}=?',(value,));self.db.commit()
                before=self.dump()
                self.assertTrue(self.normalize(apply=True,reason='Inactive')['conflicts'])
                self.assertEqual(before,self.dump())
                self.db.execute(f'UPDATE {table} SET active=1 WHERE {column}=?',(value,));self.db.commit()

    def test_missing_definitions(self):
        for table in ('collection','classification_system','classification_category'):
            with self.subTest(table=table):
                other=sqlite3.connect(':memory:');other.row_factory=sqlite3.Row
                self.db.backup(other)
                try:
                    other.execute('DROP TRIGGER no_delete_'+table)
                    other.execute('DELETE FROM '+table);other.commit()
                    self.assertTrue(self.normalize(other,apply=True,reason='Missing')['conflicts'])
                finally: other.close()

    def test_duplicate_current_memberships_detected(self):
        self.normalize(apply=True,reason='Initial')
        self.db.execute('DROP INDEX one_open_collection_membership')
        self.db.execute("INSERT INTO collection_membership(concept_id,collection_id,collection_name_snapshot,collection_code_snapshot,access_role) VALUES(1,?,'Vocabulario Académico','academic-vocabulary','master')",(self.cid,))
        self.db.commit()
        before=self.dump()
        self.assertTrue(self.normalize(apply=True,reason='Duplicate')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_closed_history_not_reopened(self):
        self.normalize(apply=True,reason='Initial')
        apply_metadata(self.db,1,{'collections':{self.cid:'leave'}},access_role='master')
        before=self.dump()
        self.assertTrue(self.normalize(apply=True,reason='Closed history')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_incompatible_membership_snapshot(self):
        self.db.execute("INSERT INTO collection_membership(concept_id,collection_id,collection_name_snapshot,collection_code_snapshot,access_role) VALUES(1,?,'Wrong','wrong','master')",(self.cid,))
        self.db.commit()
        before=self.dump()
        self.assertTrue(self.normalize(apply=True,reason='Wrong membership')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_wrong_system_scope_and_category_label(self):
        self.db.execute("UPDATE classification_category SET name='Wrong' WHERE category_id=?",(self.aid,))
        self.db.commit()
        self.assertTrue(self.normalize()['conflicts'])
        self.db.execute("UPDATE classification_category SET name='Astronomía' WHERE category_id=?",(self.aid,))
        self.db.execute("INSERT INTO collection(code,name,name_key) VALUES('other','Other','other')")
        self.db.execute('DROP TRIGGER stable_scope_classification_system')
        other=self.db.execute("SELECT collection_id FROM collection WHERE code='other'").fetchone()[0]
        self.db.execute('UPDATE classification_system SET collection_id=? WHERE system_id=?',(other,self.sid))
        self.db.commit()
        before=self.dump()
        self.assertTrue(self.normalize(apply=True,reason='Wrong scope')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_duplicate_current_revisions_detected(self):
        self.normalize(apply=True,reason='Initial')
        self.db.execute('DROP INDEX one_open_concept_classification')
        cols=[r[1] for r in self.db.execute('PRAGMA table_info(concept_classification_revision)') if r[1]!='revision_id']
        columns=','.join(cols)
        self.db.execute(f'INSERT INTO concept_classification_revision({columns}) SELECT {columns} FROM concept_classification_revision WHERE concept_id=1')
        self.db.commit()
        before=self.dump()
        self.assertTrue(self.normalize(apply=True,reason='Duplicate revision')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_membership_only_reuses_existing_identity(self):
        apply_metadata(self.db,1,{'collections':{self.cid:'join'}},access_role='master')
        report=self.normalize(apply=True,reason='Partial state')
        self.assertEqual((1,1,2),(report['memberships_to_create'],report['memberships_compatible'],report['revisions_to_create']))

    def test_file_dry_run_hash_and_checkout_guard(self):
        fd,name=tempfile.mkstemp(suffix='.db',dir=ROOT);os.close(fd)
        path=Path(name)
        try:
            filedb=sqlite3.connect(path);self.db.backup(filedb);filedb.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            report=self.run_file(path)
            self.assertEqual(before,report['sha256_after'])
            report=self.run_file(path,apply=True,reason='Synthetic copy inside checkout')
            self.assertEqual('applied',report['status'])
            self.assertEqual(2,report['changed_concepts'])
        finally:path.unlink()
        fd,name=tempfile.mkstemp(suffix='.db',dir=ROOT.parent);os.close(fd)
        outside=Path(name)
        try:
            self.assertFalse(outside.resolve(strict=True).is_relative_to(ROOT))
            filedb=sqlite3.connect(outside);self.db.backup(filedb);filedb.close()
            before=hashlib.sha256(outside.read_bytes()).hexdigest()
            self.assertEqual(before,self.run_file(outside)['sha256_after'])
            with self.assertRaises(ValueError):
                self.run_file(outside,apply=True,reason='Synthetic copy outside checkout')
            self.assertEqual(before,hashlib.sha256(outside.read_bytes()).hexdigest())
        finally:outside.unlink()


if __name__ == '__main__':
    unittest.main()
