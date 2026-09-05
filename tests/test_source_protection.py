from tests.form_client import FormClient
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

import database
from tests import test_collaboration_activity as roles

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration018", ROOT / "migrations/018_source_protection.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def test_existing_sources_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "migration.db"
            db = sqlite3.connect(path)
            database.crear_esquema(db)
            for table in ("source", "source_revision"):
                db.execute(f"ALTER TABLE {table} DROP COLUMN analyst_protected")
            db.executemany("INSERT INTO source(source_name) VALUES(?)", [(f"S{i}",) for i in range(44)])
            db.commit(); db.close()
            self.assertTrue(migration.migrate(path, None))
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT sum(analyst_protected) FROM source").fetchone()[0], 44)
            db.execute("UPDATE source SET analyst_protected=0 WHERE source_id=1")
            db.commit(); db.close()
            self.assertFalse(migration.migrate(path, None))
            db = sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT sum(analyst_protected) FROM source").fetchone()[0], 43)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.close()


class SourceProtectionTests(unittest.TestCase):
    def setUp(self):
        roles.RoleAccessTests.setUp(self)
        self.client = FormClient(self.client)
    tearDown = roles.RoleAccessTests.tearDown

    def post(self, role, path, data):
        return self.client.post(f"/test-{role}{path}", data=data)

    def row(self, sql):
        db = sqlite3.connect(self.path)
        try: return db.execute(sql).fetchone()
        finally: db.close()

    def create(self, role):
        self.assertEqual(self.post(role, "/fuentes/nueva", {"source_name": role, "source_type": "OTRO", "collaborator_id": "1", "analyst_protected": "0"}).status_code, 302)
        return self.row("SELECT max(source_id) FROM source")[0]

    def test_role_defaults_and_creation_activity(self):
        for role in ("analyst", "reviewer", "master"):
            with self.subTest(role=role):
                sid = self.create(role)
                self.assertEqual(self.row(f"SELECT analyst_protected FROM source WHERE source_id={sid}")[0], int(role != "analyst"))
                self.assertEqual(self.row(f"SELECT event_type,access_role FROM activity_event WHERE entity_id={sid}"), ("source_created", role))

    def test_protection_permissions_confirmation_and_history(self):
        sid = self.create("analyst")
        path = f"/fuentes/{sid}/proteccion"
        for value in ("0", "1"):
            self.assertEqual(self.post("analyst", path, {"protected": value, "confirm": "1"}).status_code, 404)
        self.assertEqual(self.client.get(f"/test-analyst{path}").status_code, 404)
        for role in ("reviewer", "master"):
            self.assertEqual(self.post(role, path, {"protected": "1"}).status_code, 302)
            self.assertEqual(self.row("SELECT analyst_protected FROM source")[0], 1)
            html = self.client.get(f"/test-{role}{path}").get_data(as_text=True)
            for text in ("Cancelar", "Desproteger", "Los analistas podrán editar esta fuente", 'name="confirm"'):
                self.assertIn(text, html)
            self.assertEqual(self.post(role, path, {"protected": "0"}).status_code, 400)
            self.assertEqual(self.row("SELECT analyst_protected FROM source")[0], 1)
            self.assertEqual(self.post(role, path, {"protected": "0", "confirm": "1"}).status_code, 302)
            self.assertEqual(self.row("SELECT analyst_protected FROM source")[0], 0)
        self.assertEqual(self.row("SELECT count(*) FROM activity_event WHERE event_type IN ('source_protected','source_unprotected')")[0], 4)
        self.assertEqual(self.row("SELECT count(*),sum(analyst_protected) FROM source_revision"), (4, 2))

    def test_edit_matrix_and_toggle_persistence(self):
        for creator in ("analyst", "reviewer", "master"):
            sid = self.create(creator)
            for enabled in (0, 1):
                self.post("master", "/configuracion/creacion-fuentes", {"enabled": "1"} if enabled else {})
                self.assertEqual(self.row("SELECT setting_value FROM application_setting")[0], str(enabled))
                for protected in (0, 1):
                    self.post("reviewer", f"/fuentes/{sid}/proteccion", {"protected": str(protected), "confirm": "1"})
                    for role in ("analyst", "reviewer", "master"):
                        with self.subTest(creator=creator, enabled=enabled, protected=protected, role=role):
                            allowed = role != "analyst" or (enabled and not protected)
                            base = f"/test-{role}/fuentes/{sid}"
                            self.assertEqual(self.client.get(base + "/editar").status_code, 200 if allowed else 404)
                            self.assertEqual(self.client.post(base + "/actualizar", data={"source_name": creator, "source_type": "OTRO", "characterization": f"{enabled}-{protected}-{role}", "collaborator_id": "999", "analyst_protected": "0"}).status_code, 302 if allowed else 404)
                            self.assertEqual(self.row(f"SELECT analyst_protected FROM source WHERE source_id={sid}")[0], protected)
                    html = self.client.get("/test-analyst/fuentes").get_data(as_text=True)
                    self.assertEqual(f"/fuentes/{sid}/editar" in html, bool(enabled and not protected))
                    self.assertEqual("Guardar fuente" in html, bool(enabled))
                    self.assertNotIn("Protegida", html)
                    self.assertNotIn("Desproteger", html)
        self.assertGreater(self.row("SELECT count(*) FROM source_revision")[0], 0)
        self.assertGreater(self.row("SELECT count(*) FROM activity_event WHERE event_type='source_updated' AND access_role='analyst'")[0], 0)

    def test_used_source_type_change_preserves_details(self):
        sid = self.create("analyst")
        db = sqlite3.connect(self.path)
        db.execute("INSERT INTO occurrence(source_id,source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status) VALUES(?,'title','02:20','VALUE','VALUE')", (sid,))
        db.commit(); db.close()
        self.assertEqual(self.row("SELECT analyst_protected FROM source")[0], 0)
        html = self.client.get(f"/test-analyst/fuentes/{sid}/editar").get_data(as_text=True)
        self.assertIn("Advertencia", html)
        self.assertEqual(self.post("analyst", f"/fuentes/{sid}/actualizar", {"source_name": "changed", "source_type": "MATERIAL_IMPRESO"}).status_code, 302)
        self.assertEqual(self.row("SELECT source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status FROM occurrence"), ("title", "02:20", "VALUE", "VALUE"))
        self.assertEqual(self.row("SELECT source_type,analyst_protected FROM source_revision"), ("OTRO", 0))
        self.assertEqual(self.row("SELECT count(*) FROM submission")[0], 0)
