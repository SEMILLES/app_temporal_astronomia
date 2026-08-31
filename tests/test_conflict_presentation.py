import sqlite3
import unittest

from conflict_presentation import format_subject, local_timestamp, ui_label
from database import crear_esquema

class ConflictPresentationTests(unittest.TestCase):
    def test_translations_and_bogota_timestamp(self):
        self.assertEqual("Abierto",ui_label("open"));self.assertEqual("Resuelto",ui_label("resolved"))
        self.assertEqual("Fallido",ui_label("failed"));self.assertEqual("Exitoso",ui_label("succeeded"))
        self.assertEqual("Automático",ui_label("automatic"));self.assertEqual("Manual",ui_label("manual"))
        self.assertEqual("Bloquea publicación",ui_label("blocking"));self.assertEqual("NO bloquea publicación",ui_label("non_blocking"))
        self.assertEqual("Flujo de trabajo",ui_label("workflow"));self.assertEqual("Validación global",ui_label("global_validation"))
        self.assertEqual("2026-08-31 16:05",local_timestamp("2026-08-31 21:05:18"))

    def test_human_readable_occurrence_concept_alternative_submission_relation(self):
        db=sqlite3.connect(":memory:");crear_esquema(db);db.execute("INSERT INTO source(source_name) VALUES('S')");db.execute("INSERT INTO concept(preferred_label) VALUES('ASTRONOMÍA-PREHISPÁNICA')");db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'TEST')");db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')");db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1b')");db.execute("INSERT INTO submission(occurrence_id,submission_type,status) VALUES(1,'ALTERNATIVE','pending')");db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'CM_1')")
        self.assertEqual("Ocurrencia 1 — TEST",format_subject(db,"occurrence",1));self.assertEqual("ASTRONOMÍA-PREHISPÁNICA",format_subject(db,"concept",1));self.assertEqual("ASTRONOMÍA-PREHISPÁNICA-1b",format_subject(db,"alternative",2));self.assertEqual("Aporte #1 — Análisis de alternativa",format_subject(db,"submission",1));self.assertIn("1a ↔ ASTRONOMÍA-PREHISPÁNICA-1b — CM_1",format_subject(db,"alternative_relation",1));db.close()
