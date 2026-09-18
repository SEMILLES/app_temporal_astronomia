import copy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from access_control import install_access_context
from catalog_context import enrich_catalog, semantic_fields
from catalog_presentation import variation_type
from catalog_projection import build_catalog_projection
from catalog_publication import publish_catalog, verify_publication_hash
from catalog_diff import build_catalog_diff
from concept_classification import apply_metadata
from database import crear_esquema
from routes.catalog import catalog_bp
from usage_profile import FIELDS, normalize, validate_schema

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'migration/usage_profile_2026-09-18'
sys.path.insert(0, str(SCRIPTS))
from import_profiles import read_sheet, import_rows, HEADERS
from post_reconciliation import corrections, CASES
from safe_database import run, check_integrity
spec = importlib.util.spec_from_file_location('usage_migration', ROOT / 'migrations/023_alternative_usage_profile.py')
migration_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration_module)


def connect(path=':memory:'):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def seed_pending(db):
    for legacy, gloss, source, concept, label, locator, old_detail, must_exist in CASES:
        existing = db.execute('SELECT source_id FROM source WHERE source_name=?', (source,)).fetchone()
        sid = existing[0] if existing else db.execute('INSERT INTO source(source_name) VALUES(?)', (source,)).lastrowid
        oid = db.execute('INSERT INTO occurrence(legacy_occurrence_id,original_gloss,source_id,source_detail_2) VALUES(?,?,?,?)', (legacy, gloss, sid, old_detail)).lastrowid
        if concept == 'MÁS/MENOS-QUÉ':
            continue
        cid = db.execute('SELECT concept_id FROM concept WHERE preferred_label=?', (concept,)).fetchone()
        cid = cid[0] if cid else db.execute('INSERT INTO concept(preferred_label) VALUES(?)', (concept,)).lastrowid
        aid = db.execute('INSERT INTO alternative(concept_id,working_label) VALUES(?,?)', (cid, '1a' if concept == 'MAPA-COLOMBIA' else label)).lastrowid
        if must_exist:
            db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,?)', (oid, aid))
    db.commit()


class UsageSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(); crear_esquema(self.db)
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('UNO')")
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")

    def test_optional_fields_unique_foreign_key_index_and_delete(self):
        validate_schema(self.db)
        self.db.execute('INSERT INTO alternative_usage_profile(alternative_id) VALUES(1)')
        row = self.db.execute('SELECT * FROM alternative_usage_profile').fetchone()
        self.assertTrue(all(row[f] is None for f in FIELDS))
        for sql in ('INSERT INTO alternative_usage_profile(alternative_id) VALUES(1)',
                    'INSERT INTO alternative_usage_profile(alternative_id) VALUES(999)',
                    'DELETE FROM alternative WHERE alternative_id=1'):
            with self.assertRaises(sqlite3.IntegrityError): self.db.execute(sql)
        indexes = self.db.execute('PRAGMA index_list(alternative_usage_profile)').fetchall()
        self.assertTrue(any(r['unique'] for r in indexes))

    def test_absences_and_constraints(self):
        for value in (None, '', ' ', '\t\n', '-', '---', ' N/A ', 'No Reporta', 'no reporta'):
            self.assertIsNone(normalize(value))
        self.assertEqual(normalize(' Córdoba '), 'Córdoba')
        for field in FIELDS:
            for value in ('-', '---', 'N/A', 'No Reporta', '', ' \t\n'):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.db.execute(f'INSERT INTO alternative_usage_profile(alternative_id,{field}) VALUES(1,?)', (value,))

    def test_migration_dry_run_backup_idempotence_and_partial_schema(self):
        self.db.execute('DROP TABLE alternative_usage_profile');self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as target: self.db.backup(target)
            original = path.read_bytes()
            self.assertEqual(run(path, migration_module.migration)['changes'], 1)
            self.assertEqual(path.read_bytes(), original)
            first = run(path, migration_module.migration, apply=True)
            self.assertEqual(Path(first['backup']).read_bytes(), original)
            second = run(path, migration_module.migration, apply=True)
            self.assertEqual(second['changes'], 0);self.assertIsNone(second['backup'])
        self.db.execute('CREATE TABLE alternative_usage_profile(profile_id INTEGER PRIMARY KEY)')
        with self.assertRaises(ValueError): migration_module.migration(self.db)

    def test_failure_after_backup_rolls_back_and_keeps_backup(self):
        self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as target:self.db.backup(target)
            before=path.read_bytes();calls=[]
            def operation(db):
                calls.append(True)
                db.execute('INSERT INTO alternative_usage_profile(alternative_id) VALUES(1)')
                if len(calls)==2:raise ValueError('Fallo simulado después del backup')
                return {'changes':1}
            with self.assertRaisesRegex(ValueError,'Fallo simulado'):run(path,operation,apply=True)
            self.assertEqual(path.read_bytes(),before)
            backups=list(Path(directory).glob('*.backup.db'))
            self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),before)

    def test_remote_environment_guard_refuses_other_environments(self):
        self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as target:self.db.backup(target)
            with patch.dict('os.environ',{'RAILWAY_ENVIRONMENT_NAME':'production'}):
                with self.assertRaisesRegex(ValueError,'únicamente'):run(path,migration_module.migration,apply=True)


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.db = connect();crear_esquema(self.db);seed_pending(self.db)
        self.addCleanup(self.db.close)

    def test_four_cases_identity_locator_relation_and_second_run(self):
        preguntar_before = [tuple(r) for r in self.db.execute("SELECT * FROM occurrence WHERE original_gloss='PREGUNTAR'")]
        result = corrections(self.db)
        self.assertEqual(result['validated_cases'], 6)
        for legacy, gloss, source, concept, label, locator, _, _ in CASES:
            row = self.db.execute('''SELECT c.preferred_label,x.working_label,o.source_detail_2,o.source_locator
                FROM occurrence o JOIN assignment a USING(occurrence_id) JOIN alternative x USING(alternative_id)
                JOIN concept c USING(concept_id) WHERE o.legacy_occurrence_id=? AND a.is_current=1''', (legacy,)).fetchone()
            self.assertEqual(tuple(row[:2]), (concept, label))
            if locator: self.assertEqual(tuple(row[2:]), (locator, locator))
        relation = self.db.execute('SELECT phonological_parameter FROM alternative_relation').fetchone()
        self.assertEqual(relation[0], 'N_MANOS')
        self.assertEqual(preguntar_before, [tuple(r) for r in self.db.execute("SELECT * FROM occurrence WHERE original_gloss='PREGUNTAR'")])
        self.assertEqual(corrections(self.db)['changes'], 0)
        check_integrity(self.db)

    def test_numeric_legacy_collision_is_not_used(self):
        self.db.execute("INSERT INTO occurrence(legacy_occurrence_id,original_gloss,source_id) VALUES('10787-OTRO','OTRO',1)")
        corrections(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM assignment a JOIN occurrence o USING(occurrence_id) WHERE o.original_gloss='OTRO'").fetchone()[0], 0)

    def test_unexpected_assignment_or_duplicate_aborts_without_writes(self):
        self.db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,2)');self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as target:self.db.backup(target)
            before = path.read_bytes()
            with self.assertRaises(ValueError):run(path, corrections, apply=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(Path(directory).glob('*.backup.db'))),0)

    def test_preguntar_missing_assignment_and_unexpected_locator_rejected(self):
        self.db.execute("UPDATE occurrence SET source_locator='1:23' WHERE legacy_occurrence_id='10973-PREGUNTAR'")
        with self.assertRaisesRegex(ValueError, 'localizador inesperado'):corrections(self.db)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.db = connect();crear_esquema(self.db);self.addCleanup(self.db.close)
        self.rows = read_sheet(SCRIPTS/'perfil_uso_contexto_consolidado.xlsx', 'Perfil consolidado')
        # Use workbook IDs verbatim; only the two authorized blanks are resolved.
        for row in self.rows:
            concept, label = row['Alternative actual'].rsplit('-', 1)
            cid = self.db.execute('SELECT concept_id FROM concept WHERE preferred_label=?', (concept,)).fetchone()
            cid = cid[0] if cid else self.db.execute('INSERT INTO concept(preferred_label) VALUES(?)',(concept,)).lastrowid
            aid = int(row['ID Alternative actual']) if row['ID Alternative actual'] else (10001 if concept=='MAPA-COLOMBIA' else 10002)
            self.db.execute('INSERT INTO alternative(alternative_id,concept_id,working_label) VALUES(?,?,?)',(aid,cid,label))
        self.db.commit()

    def test_real_262_profiles_second_run_nulls_and_resolved_ids(self):
        result = import_rows(self.db, self.rows)
        self.assertEqual(result['created'], 262);self.assertEqual(result['null_fields'],2491)
        self.assertEqual(set(result['resolved_ids'].values()), {10001,10002})
        self.assertEqual(import_rows(self.db,self.rows)['changes'],0)
        self.assertEqual(import_rows(self.db,self.rows)['matching'],262)
        self.assertEqual(self.db.execute('SELECT count(*) FROM alternative_usage_profile').fetchone()[0],262)
        self.assertEqual(self.db.execute('SELECT count(*) FROM alternative_usage_profile WHERE geographic_zone_general IS NULL').fetchone()[0],221)
        check_integrity(self.db)

    def test_no_guesses_for_missing_ids_names_duplicate_or_retired(self):
        for field,value in (('ID Alternative actual',''),('Alternative actual','INCORRECTO-1a')):
            rows=copy.deepcopy(self.rows);rows[0][field]=value
            with self.assertRaises(ValueError):import_rows(self.db,rows)
        rows=copy.deepcopy(self.rows);rows[1]=rows[0]
        with self.assertRaises(ValueError):import_rows(self.db,rows)
        self.db.rollback()
        self.db.execute('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=?',(int(self.rows[0]['ID Alternative actual']),))
        with self.assertRaises(ValueError):import_rows(self.db,self.rows)

    def test_differences_require_explicit_update(self):
        import_rows(self.db,self.rows)
        changed=copy.deepcopy(self.rows);changed[0]['Registro']='Nuevo registro'
        with self.assertRaisesRegex(ValueError,'diferente'):import_rows(self.db,changed)
        self.assertEqual(import_rows(self.db,changed,allow_update=True)['updated'],1)

    def test_import_normalizes_absences_and_rejects_ambiguous_exception(self):
        rows=copy.deepcopy(self.rows)
        absent=(' - ','---','N/A','No Reporta',' \t ')
        keys=list(FIELDS)[:5]
        for key,value in zip(keys,absent):rows[0][HEADERS[key]]=value
        import_rows(self.db,rows)
        row=self.db.execute('SELECT * FROM alternative_usage_profile WHERE alternative_id=?',(int(rows[0]['ID Alternative actual']),)).fetchone()
        self.assertTrue(all(row[key] is None for key in keys))
        self.db.rollback()
        cid=self.db.execute("SELECT concept_id FROM concept WHERE preferred_label='MAPA-COLOMBIA'").fetchone()[0]
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(?,'1b')",(cid,))
        with self.assertRaisesRegex(ValueError,'unívoca'):import_rows(self.db,self.rows)

    def test_import_atomic_failure_and_missing_file(self):
        rows=copy.deepcopy(self.rows);rows[-1]['Alternative actual']='ERROR'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db.sqlite'
            with closing(sqlite3.connect(path)) as target:self.db.backup(target)
            before=path.read_bytes()
            with self.assertRaises(ValueError):run(path,lambda db:import_rows(db,rows),apply=True)
            self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(FileNotFoundError):read_sheet(SCRIPTS/'absent.xlsx','Perfil consolidado')


class CatalogContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'ui.sqlite'
        db=connect(self.path);crear_esquema(db)
        db.execute("INSERT INTO concept(preferred_label,semantic_field_1,semantic_field_2,knowledge_area_1) VALUES('MORADO','Colores','N/A','Artes')")
        db.executemany('INSERT INTO alternative(concept_id,working_label) VALUES(1,?)', [('1a',),('1b',),('2a',)])
        db.execute("INSERT INTO alternative_relation(alternative_low_id,alternative_high_id,phonological_parameter) VALUES(1,2,'N_MANOS')")
        db.execute("INSERT INTO alternative_usage_profile(alternative_id,geographic_zone_general,geographic_zone_detail,semantic_pragmatic_nuance) VALUES(1,'Caribe','Córdoba','Significa moretón.')")
        db.execute('INSERT INTO alternative_usage_profile(alternative_id) VALUES(2)')
        db.execute("INSERT INTO media_asset(storage_backend,storage_key,mime_type,origin_kind) VALUES('external','https://youtu.be/abcdefghijk','video/youtube','external_reference')")
        db.execute("INSERT INTO alternative_media(alternative_id,media_asset_id,role,created_access_role) VALUES(1,1,'catalog_video','reviewer')")
        db.commit();db.close()
        app=Flask(__name__,template_folder=str(ROOT/'templates'),static_folder=str(ROOT/'static'));app.testing=True
        app.register_blueprint(catalog_bp);install_access_context(app);app.wsgi_app.routes={'ana':'analyst','rev':'reviewer','mas':'master'}
        for target in ('routes.catalog.conectar','access_control.conectar'):
            patcher=patch(target,side_effect=lambda:connect(self.path));patcher.start();self.addCleanup(patcher.stop)
        self.client=app.test_client()

    def test_conditional_tab_fields_escaping_video_and_existing_tabs(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        for role in ('ana','rev','mas'):
            html=self.client.get(f'/{role}/catalogo-interno/conceptos/1?video=1').get_data(as_text=True)
            for text in ('Caribe','Córdoba','Significa moretón.','Uso y contexto','Ocurrencias y fuentes','Morfología','Relaciones fonológicas','N_MANOS','Colores','Artes','Solo conceptos con video','Campo semántico','Limpiar filtros'):
                self.assertIn(text,html)
            self.assertEqual(html.count('>Uso y contexto</button>'),1)
            self.assertNotIn('data-panel="uso-2"',html);self.assertNotIn('data-panel="uso-3"',html)
            self.assertNotIn('>N/A<',html)
            self.assertEqual(html.count('<iframe'),1)
            self.assertLess(html.index('<iframe'),html.index('class="pestanas"'))
            for aid in (1,2,3):self.assertIn(f'data-alternative-select="{aid}"',html)
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        db=connect(self.path);db.execute("UPDATE alternative_usage_profile SET additional_notes='<script>bad()</script>' WHERE alternative_id=1");db.commit();db.close()
        html=self.client.get('/ana/catalogo-interno/conceptos/1').get_data(as_text=True)
        self.assertIn('&lt;script&gt;bad()',html);self.assertNotIn('<script>bad()',html)

    def test_current_semantic_classification_preferred_even_when_empty(self):
        db=connect(self.path)
        try:
            self.assertEqual(semantic_fields(db,1,['Colores','N/A']),['Colores'])
            system=db.execute("SELECT system_id FROM classification_system WHERE code='semantic-fields'").fetchone()[0]
            category=db.execute("SELECT category_id FROM classification_category WHERE name='Clima'").fetchone()[0]
            apply_metadata(db,1,{'classifications':{system:[category]}},access_role='master')
            self.assertEqual(semantic_fields(db,1,['Colores']),['Clima'])
            apply_metadata(db,1,{'classifications':{system:[]}},access_role='master')
            self.assertEqual(semantic_fields(db,1,['Colores']),[])
        finally:db.close()

    def test_profiles_freeze_in_new_publications_without_changing_history(self):
        db=connect(self.path)
        try:
            first=publish_catalog(db,publication_comment='Perfil inicial',actor_context={'access_role':'master'})
            frozen=json.loads(first['snapshot_json'])
            db.execute("UPDATE alternative_usage_profile SET register_notes='Registro nuevo' WHERE alternative_id=1");db.commit()
            live=build_catalog_projection(db)
            self.assertTrue(build_catalog_diff(frozen,live)['alternatives_changed'])
            self.assertTrue(verify_publication_hash(first))
            second=publish_catalog(db,publication_comment='Perfil actualizado',actor_context={'access_role':'master'})
            self.assertEqual(second['version_number'],2)
            self.assertNotIn('Registro nuevo',first['snapshot_json'])
        finally:db.close()
        historic=self.client.get('/catalogo/v1/alternativas/1').get_data(as_text=True)
        self.assertIn('Uso y contexto',historic);self.assertNotIn('Registro nuevo',historic)
        current=self.client.get('/catalogo/alternativas/1').get_data(as_text=True)
        self.assertIn('Registro nuevo',current)

    def test_four_variation_combinations_and_media_retirement(self):
        db=connect(self.path)
        try:
            concept=build_catalog_projection(db)['concepts'][0]
            self.assertEqual(variation_type(concept),'both')
            self.assertEqual(variation_type(dict(concept,alternatives=concept['alternatives'][:2])),'phonological')
            self.assertEqual(variation_type(dict(concept,relations=[])),'lexical')
            self.assertEqual(variation_type(dict(concept,alternatives=concept['alternatives'][:1],relations=[])),'none')
            db.execute('UPDATE alternative_media SET is_current=0,retired_at=CURRENT_TIMESTAMP');db.commit()
            self.assertFalse(any(a['media'] for a in build_catalog_projection(db)['concepts'][0]['alternatives']))
        finally:db.close()
        html=self.client.get('/ana/catalogo-interno/conceptos/1').get_data(as_text=True)
        self.assertNotIn('<iframe',html);self.assertNotIn('class="media-alternativa"',html)


if __name__ == '__main__':unittest.main()
