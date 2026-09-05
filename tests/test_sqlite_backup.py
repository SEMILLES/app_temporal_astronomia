from contextlib import closing, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database
import sqlite_backup as backup


class SQLiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "active.db"
        with closing(sqlite3.connect(self.source)) as connection:
            database.crear_esquema(connection)
            connection.execute("INSERT INTO concept(preferred_label) VALUES('test')")
            connection.commit()

    def create(self):
        return backup.create_backup(self.source, self.root / "backups", "test reason")

    def rewrite_manifest(self, path, **changes):
        manifest = json.loads(backup.manifest_path(path).read_text())
        manifest.update(changes)
        backup.manifest_path(path).write_text(json.dumps(manifest))

    def test_active_database_backup_restore_and_source_unchanged(self):
        before = backup.sha256(self.source)
        with closing(sqlite3.connect(self.source)) as active:
            active.execute("BEGIN IMMEDIATE")
            active.execute("INSERT INTO concept(preferred_label) VALUES('uncommitted')")
            path = self.create()
            active.rollback()
        manifest = backup.verify_backup(path)
        self.assertEqual(manifest["sha256"], backup.sha256(path))
        self.assertEqual(manifest["integrity_check"], "ok")
        self.assertEqual(manifest["foreign_key_check"], 0)
        self.assertNotIn(str(self.root), json.dumps(manifest))
        restored = backup.restore_backup(path, self.root / "restored.db")
        self.assertEqual(backup.sha256(restored), manifest["sha256"])
        with closing(backup.read_connection(restored)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM concept").fetchone()[0], 1)
        self.assertEqual(backup.inspect_database(restored)["foreign_key_check"], 0)
        self.assertEqual(before, backup.sha256(self.source))
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(path, restored)
        self.assertEqual(backup.sha256(restored), manifest["sha256"])

    def test_no_source_fallback_or_missing_file_creation(self):
        for source in (None, "", self.root / "missing.db"):
            with self.assertRaises(backup.BackupError):
                backup.create_backup(source, self.root / "backups")
        self.assertFalse((self.root / "missing.db").exists())

    def test_missing_and_incorrect_manifest(self):
        path = self.create()
        self.rewrite_manifest(path, filename="other.db")
        with self.assertRaisesRegex(backup.BackupError, "filename"):
            backup.verify_backup(path)
        backup.manifest_path(path).unlink()
        with self.assertRaisesRegex(backup.BackupError, "Manifest"):
            backup.verify_backup(path)

    def test_altered_backup_fails_checksum(self):
        path = self.create()
        with path.open("ab") as stream:
            stream.write(b"altered")
        with self.assertRaisesRegex(backup.BackupError, "Checksum"):
            backup.verify_backup(path)

    def test_corrupt_backup_even_with_matching_checksum(self):
        path = self.create()
        path.write_bytes(b"corrupt")
        self.rewrite_manifest(path, sha256=backup.sha256(path), size_bytes=path.stat().st_size)
        with self.assertRaises(sqlite3.DatabaseError):
            backup.verify_backup(path)

    def test_fk_failure_does_not_publish_backup(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("INSERT INTO occurrence(source_id) VALUES(999)")
            connection.commit()
        with self.assertRaisesRegex(backup.BackupError, "foreign_key_check"):
            self.create()
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_schema_failure_does_not_publish_backup(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("DROP TABLE concept")
        with self.assertRaisesRegex(backup.BackupError, "Schema"):
            self.create()
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_permission_and_space_errors(self):
        with patch.object(backup, "temporary_file", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.create()
        usage = type("Usage", (), {"free": 0})()
        with patch.object(backup.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(backup.BackupError, "Espacio"):
                self.create()

    def test_interruption_cleans_partial_files(self):
        with self.assertRaisesRegex(backup.BackupError, "interrumpido"):
            backup.create_backup(self.source, self.root / "backups", timeout=-1)
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_integrity_failure_does_not_publish_backup(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("INSERT INTO collaborator(display_name,active) VALUES('invalid',9)")
            connection.commit()
        with self.assertRaisesRegex(backup.BackupError, "integrity_check"):
            self.create()
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_publication_failure_cleans_files(self):
        real_link = os.link

        def link(source, destination):
            if str(destination).endswith(".manifest.json"):
                raise OSError("disk full")
            return real_link(source, destination)

        with patch.object(backup.os, "link", side_effect=link):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_restore_publication_race_does_not_overwrite(self):
        path = self.create()
        destination = self.root / "restored.db"
        real_link = os.link

        def link(source, target):
            Path(target).write_bytes(b"existing")
            return real_link(source, target)

        with patch.object(backup.os, "link", side_effect=link):
            with self.assertRaises(FileExistsError):
                backup.restore_backup(path, destination)
        self.assertEqual(destination.read_bytes(), b"existing")

    def test_repeated_names_do_not_collide(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first, second)
        backup.verify_backup(first)
        backup.verify_backup(second)

    def test_cli_environment_and_exit_codes(self):
        with patch.dict(os.environ, {"LESICO_DATABASE_PATH": str(self.source)}), redirect_stdout(StringIO()):
            self.assertEqual(backup.main(["backup", "--directory", str(self.root / "backups")]), 0)
            path = next((self.root / "backups").glob("*.db"))
            self.assertEqual(backup.main(["verify", str(path)]), 0)
            self.assertEqual(backup.main(["restore", str(path), "--destination", str(self.source)]), 1)

    def test_verification_is_read_only(self):
        path = self.create()
        before = path.read_bytes(), backup.manifest_path(path).read_bytes()
        backup.verify_backup(path)
        self.assertEqual(before, (path.read_bytes(), backup.manifest_path(path).read_bytes()))


if __name__ == "__main__":
    unittest.main()
