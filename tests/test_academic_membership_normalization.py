import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from database import crear_esquema
from concept_classification import apply_metadata
from normalize_academic_membership import ROOT, normalize, run


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
        self.cid = self.db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        self.sid = self.db.execute("SELECT system_id FROM classification_system WHERE code='knowledge-areas'").fetchone()[0]
        self.aid = self.db.execute("SELECT category_id FROM classification_category WHERE system_id=? AND code='KA-01'", (self.sid,)).fetchone()[0]

    def dump(self):
        return '\n'.join(self.db.iterdump())

    def test_dry_run_query_only_and_no_changes(self):
        before = self.dump()
        self.db.execute('PRAGMA query_only=ON')
        report = normalize(self.db)
        self.assertEqual((2,2,[]), (report['memberships_to_create'], report['revisions_to_create'], report['conflicts']))
        self.assertEqual(before, self.dump())

    def test_apply_idempotency_preservation_and_audit(self):
        legacy = [tuple(r) for r in self.db.execute('SELECT * FROM concept')]
        report = normalize(self.db, apply=True, reason='Synthetic validation')
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
        again = normalize(self.db, apply=True, reason='Second execution')
        self.assertEqual((0,0,0,[]),(again['memberships_to_create'],again['revisions_to_create'],again['changed_concepts'],again['conflicts']))
        self.assertEqual(before,self.dump())
        self.assertEqual(2,self.db.execute('SELECT count(*) FROM collection_membership').fetchone()[0])

    def test_conflicting_revision_aborts_entire_plan(self):
        other = self.db.execute("SELECT category_id FROM classification_category WHERE system_id=? AND code='KA-02'",(self.sid,)).fetchone()[0]
        apply_metadata(self.db,2,{'collections':{self.cid:'join'},'classifications':{self.sid:[other]}},access_role='master')
        before = self.dump()
        report = normalize(self.db,apply=True,reason='Must abort')
        self.assertTrue(report['conflicts'])
        self.assertEqual(0,report['changed_concepts'])
        self.assertEqual(before,self.dump())

    def test_rollback_after_mid_apply_failure(self):
        self.db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON concept_classification_revision WHEN NEW.concept_id=2 BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.db.commit()
        before = self.dump()
        with self.assertRaises(sqlite3.IntegrityError):
            normalize(self.db,apply=True,reason='Rollback validation')
        self.assertEqual(before,self.dump())

    def test_unknown_area_never_becomes_other(self):
        self.db.execute("UPDATE concept SET knowledge_area_2='Administrativo' WHERE concept_id=2")
        self.db.commit()
        before = self.dump()
        report = normalize(self.db,apply=True,reason='Unknown area')
        self.assertEqual([{'concept_id':2,'values':['Administrativo']}],report['unmapped_values'])
        self.assertEqual(before,self.dump())

    def test_inactive_definitions(self):
        for table, column, value in (('collection','collection_id',self.cid),('classification_system','system_id',self.sid),('classification_category','category_id',self.aid)):
            with self.subTest(table=table):
                self.db.execute(f'UPDATE {table} SET active=0 WHERE {column}=?',(value,));self.db.commit()
                before=self.dump()
                self.assertTrue(normalize(self.db,apply=True,reason='Inactive')['conflicts'])
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
                    self.assertTrue(normalize(other,apply=True,reason='Missing')['conflicts'])
                finally: other.close()

    def test_duplicate_current_memberships_detected(self):
        normalize(self.db,apply=True,reason='Initial')
        self.db.execute('DROP INDEX one_open_collection_membership')
        self.db.execute("INSERT INTO collection_membership(concept_id,collection_id,collection_name_snapshot,collection_code_snapshot,access_role) VALUES(1,?,'Vocabulario Académico','academic-vocabulary','master')",(self.cid,))
        self.db.commit()
        before=self.dump()
        self.assertTrue(normalize(self.db,apply=True,reason='Duplicate')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_closed_history_not_reopened(self):
        normalize(self.db,apply=True,reason='Initial')
        apply_metadata(self.db,1,{'collections':{self.cid:'leave'}},access_role='master')
        before=self.dump()
        self.assertTrue(normalize(self.db,apply=True,reason='Closed history')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_incompatible_membership_snapshot(self):
        self.db.execute("INSERT INTO collection_membership(concept_id,collection_id,collection_name_snapshot,collection_code_snapshot,access_role) VALUES(1,?,'Wrong','wrong','master')",(self.cid,))
        self.db.commit()
        before=self.dump()
        self.assertTrue(normalize(self.db,apply=True,reason='Wrong membership')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_wrong_system_scope_and_category_label(self):
        self.db.execute("UPDATE classification_category SET name='Wrong' WHERE category_id=?",(self.aid,))
        self.db.commit()
        self.assertTrue(normalize(self.db)['conflicts'])
        self.db.execute("UPDATE classification_category SET name='Astronomía' WHERE category_id=?",(self.aid,))
        self.db.execute("INSERT INTO collection(code,name,name_key) VALUES('other','Other','other')")
        self.db.execute('DROP TRIGGER stable_scope_classification_system')
        other=self.db.execute("SELECT collection_id FROM collection WHERE code='other'").fetchone()[0]
        self.db.execute('UPDATE classification_system SET collection_id=? WHERE system_id=?',(other,self.sid))
        self.db.commit()
        before=self.dump()
        self.assertTrue(normalize(self.db,apply=True,reason='Wrong scope')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_duplicate_current_revisions_detected(self):
        normalize(self.db,apply=True,reason='Initial')
        self.db.execute('DROP INDEX one_open_concept_classification')
        cols=[r[1] for r in self.db.execute('PRAGMA table_info(concept_classification_revision)') if r[1]!='revision_id']
        columns=','.join(cols)
        self.db.execute(f'INSERT INTO concept_classification_revision({columns}) SELECT {columns} FROM concept_classification_revision WHERE concept_id=1')
        self.db.commit()
        before=self.dump()
        self.assertTrue(normalize(self.db,apply=True,reason='Duplicate revision')['conflicts'])
        self.assertEqual(before,self.dump())

    def test_membership_only_reuses_existing_identity(self):
        apply_metadata(self.db,1,{'collections':{self.cid:'join'}},access_role='master')
        report=normalize(self.db,apply=True,reason='Partial state')
        self.assertEqual((1,1,2),(report['memberships_to_create'],report['memberships_compatible'],report['revisions_to_create']))

    def test_file_dry_run_hash_and_checkout_guard(self):
        fd,name=tempfile.mkstemp(suffix='.db',dir=ROOT);os.close(fd)
        path=Path(name)
        try:
            filedb=sqlite3.connect(path);self.db.backup(filedb);filedb.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            report=run(path)
            self.assertEqual(before,report['sha256_after'])
            report=run(path,apply=True,reason='Synthetic copy inside checkout')
            self.assertEqual('applied',report['status'])
            self.assertEqual(2,report['changed_concepts'])
        finally:path.unlink()
        fd,name=tempfile.mkstemp(suffix='.db',dir=ROOT.parent);os.close(fd)
        outside=Path(name)
        try:
            self.assertFalse(outside.resolve(strict=True).is_relative_to(ROOT))
            filedb=sqlite3.connect(outside);self.db.backup(filedb);filedb.close()
            before=hashlib.sha256(outside.read_bytes()).hexdigest()
            self.assertEqual(before,run(outside)['sha256_after'])
            with self.assertRaises(ValueError):
                run(outside,apply=True,reason='Synthetic copy outside checkout')
            self.assertEqual(before,hashlib.sha256(outside.read_bytes()).hexdigest())
        finally:outside.unlink()


if __name__ == '__main__':
    unittest.main()
