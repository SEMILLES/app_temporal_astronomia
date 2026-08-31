import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask, g
from database import crear_esquema
from routes.conflicts import conflicts_bp

class ConflictRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=__import__('pathlib').Path(self.temp.name)/"routes.db"
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;crear_esquema(db);db.commit();db.close()
        self.app=Flask(__name__,template_folder=str(__import__('pathlib').Path(__file__).resolve().parents[1]/'templates'));self.app.secret_key='test';self.app.register_blueprint(conflicts_bp)
        @self.app.before_request
        def role():g.current_access_role=self.role
    def tearDown(self):self.temp.cleanup()
    def connection(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON");return db
    def test_reviewer_and_master_access_analyst_404_and_get_no_side_effects(self):
        with patch("routes.conflicts.conectar",side_effect=self.connection):
            for role,expected in (("analyst",404),("reviewer",200),("master",200)):
                self.role=role;response=self.app.test_client().get("/conflictos");self.assertEqual(expected,response.status_code)
            db=self.connection();self.assertEqual(0,db.execute("SELECT count(*) FROM conflict").fetchone()[0]);db.close()
