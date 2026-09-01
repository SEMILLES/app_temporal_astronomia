import sqlite3
import unittest
from database import crear_esquema
from assignments import create_or_replace_assignment
from assignments import AlternativeNotFoundError

class ConflictHookTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:");self.db.row_factory=sqlite3.Row;self.db.execute("PRAGMA foreign_keys=ON");crear_esquema(self.db)
        self.db.execute("INSERT INTO source(source_name) VALUES('S')");self.db.execute("INSERT INTO concept(preferred_label) VALUES('C')");self.db.execute("INSERT INTO occurrence(source_id) VALUES(1)");self.db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(1,'1',CURRENT_TIMESTAMP)");self.db.commit()
    def tearDown(self):self.db.close()
    def test_assignment_to_retired_alternative_is_rejected_atomically(self):
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AlternativeNotFoundError):create_or_replace_assignment(self.db,1,1)
        self.db.rollback()
        self.assertEqual(0,self.db.execute("SELECT count(*) FROM assignment").fetchone()[0]);self.assertEqual(0,self.db.execute("SELECT count(*) FROM conflict").fetchone()[0])
