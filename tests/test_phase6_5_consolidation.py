from tests.form_client import FormClient
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

import database
from concept_labels import alternative_display_label, human_concept_label
from source_period import format_source_period, validate_occurrence_year
from routes.alternatives import alternatives_bp
from routes.concepts import concepts_bp
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp

ROOT=Path(__file__).resolve().parents[1]


class MigrationCliSafetyTests(unittest.TestCase):
    def test_cli_requires_explicit_database_and_does_not_touch_prototype(self):
        prototype=ROOT/"lesico_prototipo.db"
        before=prototype.stat().st_mtime_ns
        environment=os.environ.copy();environment.pop("LESICO_DATABASE_PATH",None)
        result=subprocess.run(
            [sys.executable,str(ROOT/"migrations"/"012_collaboration_activity.py")],
            cwd=ROOT,env=environment,capture_output=True,text=True,
        )
        self.assertNotEqual(result.returncode,0)
        self.assertIn("LESICO_DATABASE_PATH",result.stderr)
        self.assertEqual(prototype.stat().st_mtime_ns,before)

    def test_cli_respects_environment_and_names_backup_for_selected_database(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"selected.db"
            db=sqlite3.connect(path);database.crear_esquema(db)
            db.execute("DROP TABLE activity_event");db.execute("DROP TABLE collaborator");db.commit();db.close()
            environment=os.environ.copy();environment["LESICO_DATABASE_PATH"]=str(path)
            result=subprocess.run(
                [sys.executable,str(ROOT/"migrations"/"012_collaboration_activity.py")],
                cwd=ROOT,env=environment,capture_output=True,text=True,
            )
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(Path(raw,"selected.pre_migration_012.db").exists())
            db=sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT count(*) FROM activity_event").fetchone()[0],0)
            db.close()


class PeriodAndInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"ui.db"
        self.old=database.BASE_DATOS;database.BASE_DATOS=self.path
        db=sqlite3.connect(self.path);database.crear_esquema(db)
        db.executemany("INSERT INTO source(source_name,start_year,end_year,end_year_status) VALUES(?,?,?,'known')",[("SINGLE",2006,2006),("RANGE",2007,2009)])
        db.execute("INSERT INTO concept(preferred_label) VALUES('TEST-CONCEPT')")
        db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'KNOWN',2006)")
        db.execute("INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        db.commit();db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates"));app.testing=True
        app.jinja_env.filters.update(human_concept_label=human_concept_label,alternative_display_label=alternative_display_label)
        for blueprint in (concepts_bp,alternatives_bp,occurrences_bp,submissions_bp):app.register_blueprint(blueprint)
        self.client=FormClient(app.test_client())

    def tearDown(self):database.BASE_DATOS=self.old;self.tmp.cleanup()

    def test_period_display_and_year_validation(self):
        self.assertEqual(format_source_period(2006,2006),"2006")
        self.assertEqual(format_source_period(2007,2009),"2007–2009")
        page=self.client.get("/aportes/nuevo").get_data(as_text=True)
        self.assertIn("SINGLE (2006)",page);self.assertNotIn("2006–2006",page)
        db=sqlite3.connect(self.path)
        self.assertIsNone(validate_occurrence_year(db,1,""))
        self.assertEqual(validate_occurrence_year(db,2,"2008"),2008)
        for source,year in ((1,"2005"),(1,"2007"),(2,"2006"),(2,"2010")):
            with self.assertRaises(ValueError):validate_occurrence_year(db,source,year)
        db.close()

    def test_source_change_with_incompatible_year_fails(self):
        response=self.client.post("/ocurrencias/1/actualizar",data={"source_id":"2","occurrence_year":"2006"})
        self.assertEqual(response.status_code,400)
        db=sqlite3.connect(self.path);self.assertEqual(db.execute("SELECT source_id,occurrence_year FROM occurrence WHERE occurrence_id=1").fetchone(),(1,2006));db.close()

    def test_canonical_ui_navigation_morphology_and_legacy_bypasses(self):
        page=self.client.get("/ocurrencias/1/clasificar").get_data(as_text=True)
        self.assertIn("TEST-CONCEPT-1a",page)
        self.assertIn("¿Esta nueva alternativa parece estar relacionada fonológicamente con otra alternativa?",page)
        self.assertIn("Con duda",page);self.assertNotIn("Sospechada",page)
        self.assertIn('name="morphology_component_count" required',page)
        self.assertNotIn("¿Desea registrar información morfológica?",page)
        self.assertEqual(self.client.post("/conceptos/1/alternativas/nueva").status_code,404)
        self.assertEqual(self.client.get("/alternativas/1/editar").status_code,404)
        alternatives=self.client.get("/conceptos/1/alternativas").get_data(as_text=True)
        self.assertNotIn("Crear alternativa</button>",alternatives)
        self.assertNotIn("Editar alternativa</a>",alternatives)


if __name__=="__main__":unittest.main()
