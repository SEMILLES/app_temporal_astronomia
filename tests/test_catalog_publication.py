import json, sqlite3, tempfile, unittest
from pathlib import Path

from catalog_diff import build_catalog_diff
from catalog_publication import (IdenticalPublication, PublicationBlocked,
    PublicationError, publish_catalog, serialize_catalog_projection, verify_publication_hash)
from database import crear_esquema


class CatalogPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"test.db"
        self.db=sqlite3.connect(self.path); self.db.row_factory=sqlite3.Row; self.db.execute("PRAGMA foreign_keys=ON"); crear_esquema(self.db)
    def tearDown(self): self.db.close(); self.temp.cleanup()
    def actor(self): return {"access_role":"master"}
    def add_lexical_data(self):
        self.db.execute("INSERT INTO source(source_name) VALUES('Fuente')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('COSMOS')")
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'ESTRELLA')")
        self.db.execute("INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)")
        self.db.commit()
    def test_versions_hash_comment_identity_activity_and_immutability(self):
        first=publish_catalog(self.db,publication_comment=" Primera publicación ",actor_context=self.actor())
        self.assertEqual(first["version_number"],1); self.assertTrue(verify_publication_hash(first))
        self.assertEqual(first["published_by_name_snapshot"],None)
        with self.assertRaises(IdenticalPublication): publish_catalog(self.db,publication_comment="Otra",actor_context=self.actor())
        with self.assertRaises(PublicationError): publish_catalog(self.db,publication_comment=" ",actor_context=self.actor())
        self.add_lexical_data(); second=publish_catalog(self.db,publication_comment="Datos",actor_context=self.actor())
        self.assertEqual(second["version_number"],2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM activity_event WHERE event_type='catalog_published'").fetchone()[0],2)
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE catalog_publication SET publication_comment='x' WHERE publication_id=1")
    def test_blocking_prevents_and_nonblocking_is_frozen(self):
        self.db.execute("INSERT INTO conflict(origin_kind,rule_code,severity,description,subject_signature,detection_source) VALUES('automatic','X','blocking','Bloqueo','b','workflow')"); self.db.commit()
        with self.assertRaises(PublicationBlocked): publish_catalog(self.db,publication_comment="No",actor_context=self.actor())
        self.assertEqual(self.db.execute("SELECT count(*) FROM catalog_publication").fetchone()[0],0)
        self.db.execute("UPDATE conflict SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE severity='blocking'")
        self.db.execute("INSERT INTO conflict(origin_kind,rule_code,severity,description,subject_signature,detection_source) VALUES('automatic','Y','non_blocking','Aviso original','n','workflow')"); self.db.commit()
        row=publish_catalog(self.db,publication_comment="Sí",actor_context=self.actor())
        snap=self.db.execute("SELECT * FROM publication_open_conflict WHERE publication_id=?",(row["publication_id"],)).fetchone()
        self.assertEqual(snap["description_snapshot"],"Aviso original")
        self.db.execute("UPDATE conflict SET description='Cambiado',status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE conflict_id=?",(snap["conflict_id"],)); self.db.commit()
        self.assertEqual(self.db.execute("SELECT description_snapshot FROM publication_open_conflict").fetchone()[0],"Aviso original")
    def test_deterministic_serialization_and_diff(self):
        self.assertEqual(serialize_catalog_projection({"b":"á","a":1}),'{"a":1,"b":"á"}')
        diff=build_catalog_diff(None,{"concepts":[{"concept_id":2,"preferred_label":"X","alternatives":[],"relations":[]}]})
        self.assertEqual(diff["concepts_added"][0]["concept_id"],2)

if __name__=="__main__": unittest.main()
