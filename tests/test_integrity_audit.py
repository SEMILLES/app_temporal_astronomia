"""Every database in this module is created inside a TemporaryDirectory."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from database import crear_esquema
from integrity_audit import audit_database, open_readonly, readonly_authorizer, AuditAccessError


class IntegrityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'audit # & database.db'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('Synthetic')")
        self.db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('ONE',), ('TWO',)])
        self.add_alternative(1, '1a')
        self.db.commit()

    def add_alternative(self, aid, label, concept=1, evidence=True):
        self.db.execute('INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,?,?)', (aid, concept, label))
        if evidence:
            self.db.execute('INSERT INTO occurrence(occurrence_id,source_id,occurrence_year) VALUES(?,1,?)', (aid, 1900 + aid))
            self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (aid, aid))

    def relation(self, a, b, parameter='CM_1'):
        self.db.execute('INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(?,?,?)', (a, b, parameter))

    def audit(self, **kwargs):
        self.db.commit()
        return audit_database(self.path, **kwargs)

    def assertCode(self, code, status='FAIL'):
        report = self.audit()
        self.assertTrue(any(r['code'] == code and r['status'] == status for r in report['results']), report)
        return report

    def test_consistent_pass(self):
        self.assertEqual(self.audit()['status'], 'PASS')

    def test_cross_concept_and_component(self):
        self.add_alternative(2, '1a', concept=2)
        self.relation(1, 2)
        self.assertCode('CROSS_CONCEPT_RELATION')
        self.assertCode('CROSS_CONCEPT_COMPONENT')

    def test_self_relation(self):
        self.db.execute('PRAGMA ignore_check_constraints=ON')
        self.relation(1, 1)
        self.assertCode('SELF_RELATION')

    def test_duplicate_working_label(self):
        self.add_alternative(2, '1a')
        report = self.assertCode('NOMENCLATURE:1')
        self.assertIn('DUPLICATE_LABEL', str(report))

    def test_duplicate_assignment(self):
        self.db.execute('DROP INDEX one_current_assignment_per_occurrence')
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        self.assertCode('MULTIPLE_CURRENT:assignment')

    def test_orphan_relation(self):
        self.relation(1, 999)
        self.assertCode('ORPHAN_RELATION')

    def test_unknown_parameter(self):
        self.add_alternative(2, '1b')
        self.relation(1, 2, 'unknown')
        self.assertCode('UNKNOWN_RELATION_PARAMETER')

    def test_distinct_parameters_are_compatible(self):
        self.add_alternative(2, '1b')
        self.relation(1, 2, 'CM_1')
        self.relation(1, 2, 'LOC_1')
        self.assertEqual(self.audit()['status'], 'PASS')

    def test_duplicate_relation(self):
        self.add_alternative(2, '1b')
        self.db.execute('DROP INDEX one_current_alternative_relation_per_parameter')
        self.relation(1, 2)
        self.relation(1, 2)
        self.assertCode('DUPLICATE_CURRENT_RELATION')

    def test_capacity(self):
        for aid in range(2, 28):
            self.add_alternative(aid, '1' + chr(97 + (aid - 1) % 26))
            self.relation(aid - 1, aid)
        report = self.assertCode('NOMENCLATURE:1')
        self.assertIn('VARIANT_CAPACITY_EXCEEDED', str(report))

    def test_empty_alternative_warn_only_and_preflight(self):
        self.db.execute('DELETE FROM assignment')
        report = self.assertCode('EMPTY_ACTIVE_ALTERNATIVE', 'WARN')
        self.assertEqual(report['status'], 'WARN')
        report = self.audit(preflight=True)
        self.assertEqual(report['status'], 'PASS')
        self.assertNotIn('EMPTY_ACTIVE_ALTERNATIVE', [r['code'] for r in report['results']])

    def test_different_concept_reference_allowed(self):
        self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,2)')
        self.assertEqual(self.audit()['status'], 'PASS')

    def snapshot(self, snapshot=None, version=1):
        snapshot = snapshot or {'concepts': [{'concept_id': 99, 'alternatives': [
            {'alternative_id': 99, 'working_label': '9z', 'occurrences': []}], 'relations': []}]}
        raw = json.dumps(snapshot)
        self.db.execute('''INSERT INTO catalog_publication(version_number,snapshot_json,snapshot_sha256,
            change_summary_json,publication_comment,published_access_role,concept_count,alternative_count,occurrence_count,relation_count)
            VALUES(?,?,?,'{}','Historic','master',1,1,0,0)''', (version, raw, hashlib.sha256(raw.encode()).hexdigest()))

    def test_historical_snapshot_different_is_valid(self):
        self.snapshot()
        self.assertEqual(self.audit()['status'], 'PASS')

    def test_malformed_snapshot(self):
        self.snapshot({'concepts': [3]})
        self.assertCode('INVALID_PUBLICATION_SNAPSHOT')

    def test_bad_snapshot_hash(self):
        self.snapshot()
        self.db.execute('DROP TRIGGER immutable_catalog_publication_update')
        self.db.execute("UPDATE catalog_publication SET snapshot_sha256=?", ('0' * 64,))
        self.assertCode('INVALID_PUBLICATION_SNAPSHOT')

    def test_immutability_valid_and_invalid(self):
        for invalid in (False, True):
            if invalid:
                self.relation(1, 999)
            self.db.commit()
            before = self.path.read_bytes()
            dump = '\n'.join(self.db.iterdump())
            files = sorted(p.name for p in self.path.parent.iterdir())
            report = self.audit()
            self.assertEqual(report['total_changes'], 0)
            self.assertEqual(hashlib.sha256(before).digest(), hashlib.sha256(self.path.read_bytes()).digest())
            self.assertEqual(dump, '\n'.join(self.db.iterdump()))
            self.assertEqual(files, sorted(p.name for p in self.path.parent.iterdir()))
            self.assertEqual(report['status'], 'FAIL' if invalid else 'PASS')

    def test_authorizer_independently_rejects_writes(self):
        # Writable memory connection proves denial comes from the authorizer itself.
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE sample(id)')
        connection.set_authorizer(readonly_authorizer)
        for sql in ('INSERT INTO sample VALUES(1)', 'UPDATE sample SET id=2', 'DELETE FROM sample',
                    'CREATE TABLE forbidden(id)', 'DROP TABLE sample', 'ALTER TABLE sample ADD x',
                    "ATTACH ':memory:' AS other", 'PRAGMA query_only=OFF', 'PRAGMA journal_mode=WAL'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                connection.execute(sql)
        self.assertEqual(connection.total_changes, 0)

    def test_readonly_connection_rejects_writes_without_authorizer(self):
        self.db.commit()
        with open_readonly(self.path) as connection:
            connection.set_authorizer(None)
            connection.execute('PRAGMA query_only=OFF')
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM alternative')

    def cli(self, *args):
        self.db.commit()
        return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'integrity_audit.py'),
                               *map(str, args)], capture_output=True, text=True, check=False)

    def test_json_exit_codes(self):
        result = self.cli(self.path, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'PASS')
        self.relation(1, 999)
        result = self.cli(self.path, '--json', '--preflight')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'FAIL')
        missing = self.path.parent / 'does-not-exist.db'
        result = self.cli(missing, '--json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'ERROR')
        self.assertFalse(missing.exists())
        self.assertEqual(self.cli().returncode, 2)

    def test_warn_exit_zero(self):
        self.db.execute('DELETE FROM assignment')
        result = self.cli(self.path, '--json')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'WARN')

    def test_invalid_file_unchanged(self):
        bad = self.path.parent / 'not-sqlite.db'
        bad.write_bytes(b'not a database')
        self.assertEqual(self.cli(bad).returncode, 2)
        self.assertEqual(bad.read_bytes(), b'not a database')

    def test_wal_refused_without_modification(self):
        self.db.commit()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute("UPDATE source SET source_name='WAL fixture'")
        self.db.commit()
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        with self.assertRaises(AuditAccessError):
            audit_database(self.path)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.iterdir()})

    def test_missing_schema_is_fail(self):
        self.db.execute('DROP TABLE submission_lexical_decision')
        self.assertCode('SCHEMA_COVERAGE')

    def test_orphan_media_and_morphology(self):
        self.db.execute('INSERT INTO occurrence_media(occurrence_id,media_asset_id) VALUES(1,999)')
        self.db.execute('INSERT INTO alternative_morphology(alternative_id) VALUES(999)')
        self.assertCode('REFERENCE:occurrence_media.media_asset_id')
        self.assertCode('REFERENCE:alternative_morphology.alternative_id')

    def test_orphan_alternative_media(self):
        self.db.execute('INSERT INTO alternative_media(alternative_id,media_asset_id) VALUES(1,999)')
        self.assertCode('REFERENCE:alternative_media.media_asset_id')

    def add_video(self, alternative_id=1, storage_key='https://youtu.be/video'):
        self.db.execute("INSERT INTO media_asset(storage_key,mime_type,origin_kind) VALUES(?,?,?)",
                         (storage_key, 'video/youtube', 'external_reference'))
        media_id = self.db.execute('SELECT last_insert_rowid()').fetchone()[0]
        self.db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id,role,created_access_role) VALUES(?,?,?,?)",
                         (alternative_id, media_id, 'catalog_video', 'reviewer'))

    def test_invalid_canonical_video(self):
        self.add_video(storage_key='not-a-youtube-url')
        self.assertCode('INVALID_CANONICAL_VIDEO')

    def test_multiple_current_canonical_videos(self):
        self.db.execute('DROP INDEX one_current_catalog_video_per_alternative')
        self.add_video(storage_key='https://youtu.be/one')
        self.add_video(storage_key='https://youtu.be/two')
        self.assertCode('MULTIPLE_CURRENT:alternative_media')

    def test_invalid_period(self):
        self.db.execute('UPDATE source SET start_year=2000,end_year=1900')
        self.assertCode('IMPOSSIBLE_SOURCE_PERIOD')

    def test_occurrence_outside_source_period(self):
        self.db.execute('UPDATE source SET start_year=2000,end_year=2010')
        self.db.execute('UPDATE occurrence SET occurrence_year=1999 WHERE occurrence_id=1')
        self.assertCode('OCCURRENCE_OUTSIDE_SOURCE_PERIOD')

    def test_retired_target(self):
        self.db.execute("UPDATE alternative SET retired_at='2020-01-01'")
        self.assertCode('RETIRED_ASSIGNMENT_TARGET')

    def test_allowed_letter_gap_warn(self):
        self.add_alternative(2, '1c')
        self.relation(1, 2)
        report = self.assertCode('NONCANONICAL_ALLOWED_LABELS:1', 'WARN')
        self.assertEqual(report['status'], 'WARN')

    def test_number_gap_fails_a3(self):
        self.db.execute("UPDATE alternative SET working_label='2a'")
        self.assertCode('NOMENCLATURE:1')

    def test_pending_materialized_result(self):
        self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'ALTERNATIVE','pending')")
        self.db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id,resolved_alternative_id) VALUES(1,'UNSURE',1,1)")
        self.assertCode('LEXICAL_SUBMISSION_RESULT')

    def test_accepted_submission_to_retired_alternative(self):
        self.db.execute("INSERT INTO submission(occurrence_id,submission_type,status,resolution) VALUES(1,'ALTERNATIVE','resolved','accepted')")
        self.db.execute("INSERT INTO alternative_submission(submission_id,proposal_kind,reference_concept_id,resolved_alternative_id) VALUES(1,'UNSURE',1,1)")
        self.db.execute("UPDATE alternative SET retired_at='2026-01-01'")
        self.assertCode('LEXICAL_SUBMISSION_RESULT')

    def test_renumber_change_must_match_event_concept(self):
        self.db.execute("INSERT INTO renumber_event(concept_id,origin) VALUES(2,'automatic_assisted')")
        event_id = self.db.execute('SELECT last_insert_rowid()').fetchone()[0]
        self.db.execute("INSERT INTO renumber_change(renumber_event_id,alternative_id,new_working_label) VALUES(?,?,?)",
                         (event_id, 1, '1a'))
        self.assertCode('REN_NUMBER_CONCEPT_MISMATCH')

    def test_known_activity_entity_orphan_fails(self):
        self.db.execute("INSERT INTO activity_event(event_type,entity_type,entity_id,access_role) VALUES('changed','alternative',999,'analyst')")
        self.assertCode('ACTIVITY_ENTITY_REFERENCE')

    def test_no_app_import_in_cli(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; import integrity_audit; assert 'app' not in sys.modules; assert 'database' not in sys.modules"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
