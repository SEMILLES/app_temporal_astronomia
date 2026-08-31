import sqlite3
import unittest
from database import crear_esquema
from conflict_rules import ConflictSubject, detect_missing_working_label
from conflicts import *

class ConflictServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");crear_esquema(self.db)
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Revisora')");self.db.execute("INSERT INTO concept(preferred_label) VALUES('C')");self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,NULL)")
        self.actor={"collaborator_id":1,"access_role":"reviewer"}
    def tearDown(self):self.db.close()
    def test_dedup_failed_success_no_auto_resolve_and_recurrence(self):
        finding=detect_missing_working_label(self.db)[0]
        cid,new=create_or_get_automatic_conflict(self.db,finding,actor_context=self.actor);self.assertTrue(new)
        self.assertEqual((cid,False),create_or_get_automatic_conflict(self.db,finding,actor_context=self.actor))
        ok,_=attempt_conflict_resolution(self.db,cid,comment="Revisé",actor_context=self.actor);self.assertFalse(ok)
        self.db.execute("UPDATE alternative SET working_label='1' WHERE alternative_id=1")
        self.assertEqual("open",self.db.execute("SELECT status FROM conflict").fetchone()[0])
        self.assertTrue(attempt_conflict_resolution(self.db,cid,comment="Corregido",actor_context=self.actor)[0])
        self.db.execute("UPDATE alternative SET working_label=NULL WHERE alternative_id=1")
        second,is_new=create_or_get_automatic_conflict(self.db,detect_missing_working_label(self.db)[0]);self.assertTrue(is_new);self.assertNotEqual(cid,second)
        self.assertEqual(["failed","succeeded"],[r[0] for r in self.db.execute("SELECT outcome FROM conflict_resolution_attempt WHERE conflict_id=? ORDER BY conflict_resolution_attempt_id",(cid,))])
    def test_manual_requires_confirmation_and_global_without_actor_has_no_activity(self):
        cid=create_manual_conflict(self.db,description="Revisar",subjects=[ConflictSubject("alternative",1,"subject")],severity="non_blocking",justification="No bloquea catálogo",resolution_criteria="Verificación humana",actor_context=self.actor)
        with self.assertRaisesRegex(ConflictError,"confirmar"):attempt_conflict_resolution(self.db,cid,comment="Hecho",actor_context=self.actor)
        self.assertTrue(attempt_conflict_resolution(self.db,cid,comment="Hecho",manual_confirmed=True,actor_context=self.actor)[0])
        before=self.db.execute("SELECT count(*) FROM activity_event").fetchone()[0]
        result=run_global_conflict_validation(self.db);self.assertTrue(result["created_conflict_ids"])
        self.assertEqual(before,self.db.execute("SELECT count(*) FROM activity_event").fetchone()[0])
        self.assertTrue(has_blocking_open_conflicts(self.db))
