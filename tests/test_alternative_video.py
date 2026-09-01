import importlib.util, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from access_control import install_access_context
from alternative_video_service import (CurrentVideoExists, NoCurrentVideo,
    VideoAlreadyCurrent, add_video, get_current_video, get_video_history,
    replace_video, retire_video)
from catalog_projection import build_catalog_projection
from database import crear_esquema
from routes.alternatives import alternatives_bp
from youtube_media import (InvalidYouTubeURL, normalize_youtube_url,
                           parse_youtube_url, youtube_embed_url)

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("migration015",ROOT/"migrations/015_alternative_video_history.py")
MIGRATION=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MIGRATION)


class YouTubeParserTests(unittest.TestCase):
    def test_valid_formats_normalize(self):
        expected="abcdefghijk"
        for url in ("https://youtube.com/watch?v=abcdefghijk","https://www.youtube.com/watch?v=abcdefghijk","https://youtu.be/abcdefghijk","https://youtube.com/shorts/abcdefghijk","https://www.youtube.com/embed/abcdefghijk"):
            self.assertEqual(parse_youtube_url(url),expected)
            self.assertEqual(normalize_youtube_url(url),"https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(youtube_embed_url(expected),"https://www.youtube.com/embed/abcdefghijk")
    def test_rejects_untrusted_or_malformed_urls(self):
        for url in ("","http://youtube.com/watch?v=abcdefghijk","javascript:alert(1)","https://example.com/abcdefghijk","https://youtube.com.evil.example/watch?v=abcdefghijk","//youtu.be/abcdefghijk","https://youtube.com/watch","https://youtu.be/short"):
            with self.subTest(url=url),self.assertRaises(InvalidYouTubeURL): parse_youtube_url(url)


class VideoServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:"); self.db.row_factory=sqlite3.Row; self.db.execute("PRAGMA foreign_keys=ON"); crear_esquema(self.db)
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')"); self.db.executemany("INSERT INTO alternative(concept_id,working_label) VALUES(1,?)",(("1a",),("1b",)))
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Revisora')"); self.db.commit(); self.actor={"access_role":"reviewer","collaborator_id":1}
    def tearDown(self): self.db.close()
    def test_add_replace_retire_history_activity_and_reuse(self):
        first=add_video(self.db,1,"https://youtu.be/abcdefghijk",self.actor)
        self.assertEqual(get_current_video(self.db,1)["video_id"],"abcdefghijk")
        with self.assertRaises(CurrentVideoExists): add_video(self.db,1,"https://youtu.be/lmnopqrstuv",self.actor)
        with self.assertRaises(VideoAlreadyCurrent): replace_video(self.db,1,"https://youtube.com/watch?v=abcdefghijk",self.actor)
        second=replace_video(self.db,1,"https://youtube.com/shorts/lmnopqrstuv",self.actor)
        self.assertNotEqual(first,second); self.assertEqual(len(get_video_history(self.db,1)),2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM alternative_media WHERE role='catalog_video' AND is_current=1").fetchone()[0],1)
        retire_video(self.db,1,self.actor,"No vigente"); self.assertIsNone(get_current_video(self.db,1)); self.assertEqual(len(get_video_history(self.db,1)),2)
        with self.assertRaises(NoCurrentVideo): retire_video(self.db,1,self.actor)
        add_video(self.db,2,"https://youtu.be/abcdefghijk",{"access_role":"master"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM media_asset WHERE mime_type='video/youtube'").fetchone()[0],2)
        self.assertEqual([r[0] for r in self.db.execute("SELECT event_type FROM activity_event ORDER BY activity_event_id")],["alternative_video_added","alternative_video_replaced","alternative_video_retired","alternative_video_added"])
    def test_role_validation_constraint_and_projection_exclusions(self):
        with self.assertRaisesRegex(ValueError,"rol"): add_video(self.db,1,"https://youtu.be/abcdefghijk",{"access_role":"analyst"})
        self.db.execute("INSERT INTO media_asset(storage_backend,storage_key,mime_type,origin_kind) VALUES('external','https://youtu.be/abcdefghijk','video/youtube','external_reference')")
        self.db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id) VALUES(1,1)")
        self.db.commit(); alternative=build_catalog_projection(self.db)["concepts"][0]["alternatives"][0]; self.assertFalse(alternative["media"])
        add_video(self.db,1,"https://youtu.be/lmnopqrstuv",self.actor); alternative=build_catalog_projection(self.db)["concepts"][0]["alternatives"][0]
        self.assertEqual(alternative["media"][0]["embed_url"],"https://www.youtube.com/embed/lmnopqrstuv")
    def test_database_uniqueness_and_transaction_rollback(self):
        add_video(self.db,1,"https://youtu.be/abcdefghijk",self.actor)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id,role,created_access_role) VALUES(1,1,'catalog_video','master')")
        self.db.rollback()
        with patch("alternative_video_service.record_activity",side_effect=RuntimeError("audit failed")),self.assertRaises(RuntimeError):
            replace_video(self.db,1,"https://youtu.be/lmnopqrstuv",self.actor)
        self.assertEqual(get_current_video(self.db,1)["video_id"],"abcdefghijk"); self.assertEqual(len(get_video_history(self.db,1)),1)


class Migration015Tests(unittest.TestCase):
    def test_legacy_is_preserved_without_backfill(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/"014.db"; db=sqlite3.connect(path); db.executescript("""PRAGMA foreign_keys=ON; CREATE TABLE alternative(alternative_id INTEGER PRIMARY KEY); CREATE TABLE media_asset(media_asset_id INTEGER PRIMARY KEY); CREATE TABLE collaborator(collaborator_id INTEGER PRIMARY KEY); CREATE TABLE catalog_publication(publication_id INTEGER PRIMARY KEY); CREATE TABLE alternative_media(alternative_id INTEGER NOT NULL,media_asset_id INTEGER NOT NULL,role TEXT NOT NULL DEFAULT 'internal_reference' CHECK(role='internal_reference'),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,created_by TEXT,PRIMARY KEY(alternative_id,media_asset_id),FOREIGN KEY(alternative_id) REFERENCES alternative,FOREIGN KEY(media_asset_id) REFERENCES media_asset); INSERT INTO alternative VALUES(1); INSERT INTO media_asset VALUES(1); INSERT INTO alternative_media(alternative_id,media_asset_id,created_by) VALUES(1,1,'legacy');"""); db.commit(); db.close()
            self.assertTrue(MIGRATION.migrate(path,None)); self.assertFalse(MIGRATION.migrate(path,None)); db=sqlite3.connect(path)
            self.assertEqual(db.execute("SELECT role,is_current,created_by FROM alternative_media").fetchone(),("internal_reference",1,"legacy")); self.assertEqual(db.execute("SELECT count(*) FROM alternative_media WHERE role='catalog_video'").fetchone()[0],0); self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall()); db.close()


class VideoRouteRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"routes.db"; db=self.connect(); crear_esquema(db); db.execute("INSERT INTO concept(preferred_label) VALUES('TEST')"); db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')"); db.commit(); db.close()
        app=Flask(__name__,template_folder=str(ROOT/"templates")); app.testing=True; app.jinja_env.filters["alternative_display_label"]=lambda c,a:f"{c}-{a}"; app.register_blueprint(alternatives_bp); install_access_context(app); app.wsgi_app.routes={"ana":"analyst","rev":"reviewer","mas":"master"}
        self.patches=[patch("routes.alternatives.conectar",side_effect=self.connect),patch("access_control.conectar",side_effect=self.connect)]
        for item in self.patches:item.start()
        self.client=app.test_client()
    def connect(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
    def tearDown(self):
        for item in self.patches:item.stop()
        self.temp.cleanup()
    def test_analyst_denied_reviewer_and_master_manage(self):
        self.assertEqual(self.client.get("/ana/alternativas/1/video").status_code,404); self.assertEqual(self.client.post("/ana/alternativas/1/video",data={"action":"add","youtube_url":"https://youtu.be/abcdefghijk"}).status_code,404)
        self.assertEqual(self.client.get("/rev/alternativas/1/video").status_code,200)
        response=self.client.post("/rev/alternativas/1/video",data={"action":"add","youtube_url":"https://youtu.be/abcdefghijk"}); self.assertEqual(response.status_code,302)
        self.assertIn("abcdefghijk",self.client.get("/mas/alternativas/1/video").get_data(as_text=True))


if __name__=="__main__": unittest.main()
