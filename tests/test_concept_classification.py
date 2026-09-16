"""Phase 19A: only in-memory databases and freshly created temporary files."""
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from database import crear_esquema
from classification_schema import SEMANTIC_FIELDS, KNOWLEDGE_AREAS, TABLES, install
from concept_classification import (ClassificationError, apply_metadata, administer,
    concept_state, catalog_state, normalize_pair, proposal_payload, editor_context)
from edit_concurrency import StaleEdit, edit_token, fingerprint
from alternative_workflow import create_alternative_submission, review_as_new
from submission_concept_resolution import save_resolution
from integrity_audit import Auditor
from catalog_projection import build_catalog_projection


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        crear_esquema(self.db)
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('UNO')")
        self.db.execute("INSERT INTO concept(preferred_label) VALUES('DOS')")
        self.db.execute("INSERT INTO source(source_name) VALUES('SINTÉTICA')")
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss) VALUES(1,'PRUEBA')")
        self.db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(1,1)')
        self.db.execute("INSERT INTO collaborator(display_name) VALUES('Revisora')")
        self.db.commit()
        self.sf = self.db.execute("SELECT system_id FROM classification_system WHERE code='semantic-fields'").fetchone()[0]
        self.ka = self.db.execute("SELECT system_id FROM classification_system WHERE code='knowledge-areas'").fetchone()[0]
        self.collection = self.db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
        self.fields = [r[0] for r in self.db.execute('SELECT category_id FROM classification_category WHERE system_id=? ORDER BY display_order',(self.sf,))]
        self.areas = [r[0] for r in self.db.execute('SELECT category_id FROM classification_category WHERE system_id=? ORDER BY display_order',(self.ka,))]
        self.addCleanup(self.db.close)

    def apply(self, classifications=None, collections=None, concept=1, **kwargs):
        return apply_metadata(self.db,concept,{'classifications':classifications or {},'collections':collections or {}},
                              access_role='reviewer',collaborator_id=1,**kwargs)

    def current(self, sid=None):
        return self.db.execute('SELECT * FROM concept_classification_revision WHERE concept_id=1 AND system_id=? AND ended_at IS NULL',(sid or self.sf,)).fetchone()

    def dump(self):
        return '\n'.join(self.db.iterdump())

    def propose(self, payload):
        return create_alternative_submission(self.db,1,'NEW',phonological_relation_answer='NO',
                    morphology={'component_count_not_applicable':True},concept_metadata=payload,access_role='analyst')

    def test_exact_seeds_idempotent_no_real_assignments(self):
        for sid,expected in ((self.sf,SEMANTIC_FIELDS),(self.ka,KNOWLEDGE_AREAS)):
            self.assertEqual(tuple(r[0] for r in self.db.execute('SELECT name FROM classification_category WHERE system_id=? ORDER BY display_order',(sid,))),expected)
        before=self.dump();install(self.db);self.assertEqual(before,self.dump())
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM collection_membership').fetchone()[0])
        self.assertEqual(56,self.db.execute('SELECT count(*) FROM classification_category').fetchone()[0])

    def test_empty_second_position_order_and_swap_semantics(self):
        self.apply({self.sf:[]});self.assertIsNone(self.current()['category_1_id'])
        self.apply({self.sf:[None,self.fields[2]]});self.assertEqual(self.fields[2],self.current()['category_1_id'])
        self.apply({self.sf:[self.fields[2],self.fields[0]]})
        self.assertEqual((self.fields[2],self.fields[0]),tuple(self.current()[key] for key in ('category_1_id','category_2_id')))
        self.apply({self.sf:[self.fields[0],self.fields[2]]})
        self.assertEqual(0,self.current()['semantic_changed'])
        self.assertEqual(4,self.db.execute('SELECT count(*) FROM concept_classification_revision').fetchone()[0])

    def test_duplicates_wrong_scope_and_third_value_rollback(self):
        for values in ([self.fields[0]]*2,self.fields[:3],[self.areas[0]],['N/A']):
            before=self.dump()
            with self.assertRaises(ValueError):self.apply({self.sf:values},{self.collection:'join'})
            self.assertEqual(before,self.dump())

    def test_areas_require_membership(self):
        with self.assertRaises(ClassificationError):self.apply({self.ka:self.areas[:2]})
        self.apply({self.ka:list(reversed(self.areas[:2]))},{self.collection:'join'})
        self.assertEqual(self.areas[1],self.current(self.ka)['category_1_id'])

    def test_leave_rejoin_preserves_global_and_never_restores_areas(self):
        self.apply({self.sf:self.fields[:2],self.ka:self.areas[:2]},{self.collection:'join'})
        sf=dict(self.current());old=dict(self.current(self.ka))
        globals_before=[tuple(r) for t in ('concept','alternative','occurrence') for r in self.db.execute('SELECT * FROM '+t)]
        self.apply(collections={self.collection:'leave'})
        self.assertIsNone(self.current(self.ka));self.assertEqual(sf,dict(self.current()))
        self.apply(collections={self.collection:'join'})
        self.assertIsNone(self.current(self.ka))
        memberships=self.db.execute('SELECT * FROM collection_membership ORDER BY membership_id').fetchall()
        self.assertEqual(2,len(memberships));self.assertIsNotNone(memberships[0]['ended_at']);self.assertIsNone(memberships[1]['ended_at'])
        self.apply({self.ka:[self.areas[2]]})
        self.assertNotEqual(old['membership_id'],self.current(self.ka)['membership_id'])
        self.assertIsNone(self.current(self.ka)['supersedes_revision_id'])
        self.assertEqual(globals_before,[tuple(r) for t in ('concept','alternative','occurrence') for r in self.db.execute('SELECT * FROM '+t)])

    def test_multiple_collections_and_systems(self):
        cid=administer(self.db,'collection',code='other',name='Otra',access_role='master')
        sid=administer(self.db,'system',code='topics',name='Temas',parent_id=cid,access_role='master')
        category=administer(self.db,'category',code='T-1',name='Uno',parent_id=sid,access_role='master')
        self.apply({self.ka:self.areas[:1],sid:[category]},{self.collection:'join',cid:'join'})
        self.apply(collections={self.collection:'leave'})
        self.assertIsNotNone(self.current(sid))

    def test_history_names_and_inactive_retention(self):
        self.apply({self.sf:self.fields[:2]})
        original=dict(self.current())
        administer(self.db,'category',identifier=self.fields[0],name='Nombre nuevo',active=0,access_role='master')
        self.assertEqual(original,dict(self.current()))
        self.apply({self.sf:self.fields[:2]})
        self.assertEqual(original,dict(self.current()))
        self.apply({self.sf:[self.fields[1],self.fields[0]]})
        self.assertEqual('Nombre nuevo',self.current()['category_2_name'])
        with self.assertRaises(ClassificationError):self.apply({self.sf:[self.fields[0]]},concept=2)
        self.apply({self.sf:[]})
        with self.assertRaises(ClassificationError):self.apply({self.sf:self.fields[:1]})

    def test_inactive_collection_and_system(self):
        self.apply({self.ka:self.areas[:1]},{self.collection:'join'})
        administer(self.db,'collection',identifier=self.collection,name='Vocabulario Académico',active=0,access_role='master')
        self.apply({self.ka:self.areas[:1]})
        with self.assertRaises(ClassificationError):self.apply({self.ka:self.areas[:2]})
        self.apply(collections={self.collection:'leave'})
        with self.assertRaises(ClassificationError):self.apply(collections={self.collection:'join'})
        administer(self.db,'system',identifier=self.sf,name='Campos Semánticos',active=0,access_role='master')
        with self.assertRaises(ClassificationError):self.apply({self.sf:self.fields[:1]})

    def test_master_only_no_na_duplicate_names_or_scope_changes(self):
        for role in ('analyst','reviewer'):
            with self.assertRaises(ClassificationError):administer(self.db,'category',code='x',name='Otro más',parent_id=self.sf,access_role=role)
        with self.assertRaises(ClassificationError):administer(self.db,'category',code='x',name=' N/A ',parent_id=self.sf,access_role='master')
        with self.assertRaises(sqlite3.IntegrityError):administer(self.db,'category',code='x',name='  OTRO ',parent_id=self.sf,access_role='master')
        for sql in ('DELETE FROM classification_category WHERE category_id=1',
                    'UPDATE classification_category SET system_id=2 WHERE category_id=1',
                    "UPDATE classification_category SET code='changed' WHERE category_id=1"):
            with self.assertRaises(sqlite3.IntegrityError):self.db.execute(sql)
        self.db.rollback()

    def test_history_is_immutable_and_database_checks_scope(self):
        self.apply({self.sf:self.fields[:1],self.ka:self.areas[:1]},{self.collection:'join'})
        for sql in ('DELETE FROM concept_classification_revision',
                    "UPDATE concept_classification_revision SET category_1_name='changed'",
                    'DELETE FROM collection_membership',
                    'UPDATE collection_membership SET ended_at=CURRENT_TIMESTAMP'):
            with self.assertRaises(sqlite3.IntegrityError):self.db.execute(sql)
        self.db.rollback()
        self.apply(collections={self.collection:'leave'})
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('UPDATE collection_membership SET ended_at=NULL')
        self.db.rollback()

    def test_atomic_rollback_on_activity_failure(self):
        before=self.dump()
        with patch('concept_classification.record_activity',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):self.apply({self.sf:self.fields[:2]},{self.collection:'join'})
        self.assertEqual(before,self.dump())

    def test_stale_state_including_change_revert(self):
        state=fingerprint(concept_state(self.db,1))
        self.apply({self.sf:self.fields[:1]});self.apply({self.sf:[]})
        before=self.dump()
        with self.assertRaises(StaleEdit):self.apply({self.sf:self.fields[1:2]},expected_state=state)
        self.assertEqual(before,self.dump())

    def test_propose_correct_empty_and_accept_materializes(self):
        payload={'classifications':{self.sf:list(reversed(self.fields[:2])),self.ka:self.areas[:2]},'collections':{self.collection:'join'}}
        sid=self.propose(payload)
        self.assertIsNone(self.current())
        proposal=proposal_payload(self.db,sid)
        proposal['classifications'][self.sf]=[]
        proposal['classifications'][self.ka]=[self.areas[2],None]
        rid=save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal)
        self.assertIsNone(self.current()['category_1_id'])
        self.assertEqual(self.areas[2],self.current(self.ka)['category_1_id'])
        self.assertEqual(self.fields[1],self.db.execute('SELECT category_1_id FROM submission_classification_proposal WHERE submission_id=? AND system_id=?',(sid,self.sf)).fetchone()[0])
        decision=json.loads(self.db.execute('SELECT classification_decision_json FROM submission_concept_resolution WHERE submission_concept_resolution_id=?',(rid,)).fetchone()[0])
        self.assertEqual('Física',decision['classifications'][1]['names'][0])
        review_as_new(self.db,sid,access_role='reviewer',morphology_resolution='ACCEPTED',review_note='Validado')
        self.assertEqual('resolved',self.db.execute('SELECT status FROM submission WHERE submission_id=?',(sid,)).fetchone()[0])

    def test_create_new_from_proposal(self):
        self.db.execute("INSERT INTO concept_proposal(proposed_label,status) VALUES('NUEVO','pending')")
        self.db.execute('UPDATE occurrence_concept_reference SET concept_id=NULL,concept_proposal_id=1')
        self.db.commit()
        sid=self.propose({'classifications':{self.sf:self.fields[:2]},'collections':{self.collection:'join'}})
        rid=save_resolution(self.db,sid,'ACCEPT_PROPOSAL',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid))
        cid=self.db.execute('SELECT concept_id FROM submission_concept_resolution WHERE submission_concept_resolution_id=?',(rid,)).fetchone()[0]
        self.assertEqual('NUEVO',self.db.execute('SELECT preferred_label FROM concept WHERE concept_id=?',(cid,)).fetchone()[0])
        self.assertEqual(1,len(concept_state(self.db,cid)['memberships']))

    def test_proposal_original_names_and_immutable(self):
        sid=self.propose({'classifications':{self.sf:self.fields[:1]}})
        administer(self.db,'category',identifier=self.fields[0],name='Renombrado',access_role='master')
        original=self.db.execute('SELECT category_1_name FROM submission_classification_proposal WHERE submission_id=?',(sid,)).fetchone()[0]
        self.assertEqual(SEMANTIC_FIELDS[0],original)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute('DELETE FROM submission_classification_proposal')
        self.db.rollback()

    def test_stale_proposal_and_reviewed_destination(self):
        sid=self.propose({'classifications':{self.sf:self.fields[:1]}})
        self.apply({self.sf:self.fields[1:2]})
        with self.assertRaises(StaleEdit):save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid))
        app=Flask(__name__);app.secret_key='test'
        with app.app_context():
            token=edit_token(self.db,'submission_concept',sid)
            save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid),
                            expected_edit_token=token,metadata_reviewed=True,metadata_target=1)
            with self.assertRaises(StaleEdit):save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid),expected_edit_token=token)

    def test_legacy_catalog_keeps_names_order_and_columns(self):
        self.db.execute("UPDATE concept SET semantic_field_1='LEGACY-B',semantic_field_2='LEGACY-A',knowledge_area_1='LEGACY-AREA' WHERE concept_id=1")
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        self.db.commit()
        before=build_catalog_projection(self.db)
        self.apply({self.sf:self.fields[:2],self.ka:self.areas[:2]},{self.collection:'join'})
        self.apply(collections={self.collection:'leave'})
        self.assertEqual(before,build_catalog_projection(self.db))
        self.assertEqual(['LEGACY-B','LEGACY-A'],before['concepts'][0]['semantic_fields'])

    def test_auditor_checks_structural_corruption(self):
        self.apply({self.sf:self.fields[:2],self.ka:self.areas[:2]},{self.collection:'join'})
        audit=Auditor(self.db);audit.classifications()
        self.assertTrue(all(r['status']=='PASS' for r in audit.results),audit.results)
        self.db.execute('DROP TRIGGER immutable_concept_classification_revision')
        self.db.execute('PRAGMA ignore_check_constraints=ON')
        self.db.execute('UPDATE concept_classification_revision SET category_2_id=category_1_id WHERE system_id=?',(self.sf,))
        self.db.execute('UPDATE concept_classification_revision SET membership_id=NULL WHERE system_id=?',(self.ka,))
        audit=Auditor(self.db);audit.classifications()
        failures={r['code'] for r in audit.results if r['status']=='FAIL'}
        self.assertIn('INVALID_CATEGORY_PAIR:concept_classification_revision',failures)
        self.assertIn('INVALID_CLASSIFICATION_SCOPE',failures)

    def test_no_concept_occurrence_is_preserved(self):
        self.db.execute("INSERT INTO occurrence(source_id,original_gloss,source_detail_1) VALUES(1,'SEMILLES','detalle')")
        self.db.commit()
        self.apply({self.sf:self.fields[:1]})
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM assignment WHERE occurrence_id=2').fetchone()[0])
        self.assertEqual('SEMILLES',self.db.execute('SELECT original_gloss FROM occurrence WHERE occurrence_id=2').fetchone()[0])

    def test_pending_inactive_category_requires_correction_and_rollback(self):
        sid=self.propose({'classifications':{self.sf:self.fields[:1]}})
        administer(self.db,'category',identifier=self.fields[0],name=SEMANTIC_FIELDS[0],active=0,access_role='master')
        before=self.dump()
        with self.assertRaises(ClassificationError):
            save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid))
        self.assertEqual(before,self.dump())
        save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata={'classifications':{self.sf:[]}})
        self.assertIsNone(self.current()['category_1_id'])

    def test_omitted_proposal_preserves_current_and_analyst_cannot_apply(self):
        sid=self.propose({'classifications':{self.sf:self.fields[:1]}})
        self.apply({self.sf:self.fields[2:3]})
        save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer')
        self.assertEqual(self.fields[2],self.current()['category_1_id'])
        before=self.dump()
        with self.assertRaises(ClassificationError):
            apply_metadata(self.db,1,{'classifications':{self.sf:[]}},access_role='analyst')
        self.assertEqual(before,self.dump())

    def test_decision_snapshot_name_immutable_after_master_rename(self):
        sid=self.propose({'classifications':{self.sf:self.fields[:1]}})
        save_resolution(self.db,sid,'CONFIRM_REFERENCE',access_role='reviewer',concept_metadata=proposal_payload(self.db,sid))
        original=self.db.execute('SELECT classification_decision_json FROM submission_concept_resolution').fetchone()[0]
        administer(self.db,'category',identifier=self.fields[0],name='Nombre posterior',access_role='master')
        self.assertEqual(original,self.db.execute('SELECT classification_decision_json FROM submission_concept_resolution').fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute("UPDATE submission_concept_resolution SET classification_decision_json='{}'")
        self.db.rollback()
        context=editor_context(self.db,1,sid)
        self.assertEqual('Nombre posterior',context['systems'][0]['current_names'][0])
        self.assertEqual(SEMANTIC_FIELDS[0],context['history'][0]['category_1_name'])

    def test_database_constraints_and_audit_duplicate_membership(self):
        self.apply({self.sf:self.fields[:1]},{self.collection:'join'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('''INSERT INTO concept_classification_revision
                (concept_id,system_id,system_name_snapshot,system_code_snapshot,category_1_id,category_1_name,category_1_code,semantic_changed,access_role)
                VALUES(2,?,'Campos','semantic-fields',?,'Astronomía','KA-01',1,'reviewer')''',(self.sf,self.areas[0]))
        self.db.rollback()
        sql='''INSERT INTO collection_membership(concept_id,collection_id,collection_name_snapshot,collection_code_snapshot,access_role)
               VALUES(1,?,'Vocabulario Académico','academic-vocabulary','reviewer')'''
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute(sql,(self.collection,))
        self.db.rollback()
        self.db.execute('DROP INDEX one_open_collection_membership');self.db.execute(sql,(self.collection,))
        audit=Auditor(self.db);audit.classifications()
        self.assertTrue(any(r['code']=='DUPLICATE_OPEN_MEMBERSHIP' and r['status']=='FAIL' for r in audit.results))

    def test_immediate_lexical_acceptance_metadata_and_stale_preview(self):
        from immediate_acceptance import alternative_operation, preview_operation, confirm_operation
        self.db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(1,'1a')")
        self.db.commit()
        proposal={'proposal_kind':'EXISTING','proposed_existing_alternative_id':1,
                  'concept_metadata':{'classifications':{self.sf:self.fields[:2],self.ka:self.areas[:1]},
                                      'collections':{self.collection:'join'}}}
        operation=alternative_operation(1,proposal,{'decision':'existing','alternative_id':1},actor_context={'access_role':'reviewer'})
        app=Flask(__name__);app.secret_key='test'
        with app.app_context():
            before=self.dump();preview=preview_operation(self.db,operation);self.assertEqual(before,self.dump())
            administer(self.db,'category',identifier=self.fields[0],name='Nombre actualizado',access_role='master')
            with self.assertRaises(StaleEdit):confirm_operation(self.db,operation,expected_preview_token=preview['preview_token'])
            preview=preview_operation(self.db,operation)
            result=confirm_operation(self.db,operation,expected_preview_token=preview['preview_token'])
        self.assertEqual(self.fields[0],self.current()['category_1_id'])
        self.assertEqual(self.areas[0],self.current(self.ka)['category_1_id'])
        self.assertEqual('resolved',self.db.execute('SELECT status FROM submission WHERE submission_id=?',(result['result']['submission_id'],)).fetchone()[0])


class MigrationTests(unittest.TestCase):
    def test_schema_only_idempotent_and_reject_non_test(self):
        migration=importlib.import_module('migrations.022_concept_classification')
        with self.assertRaises(RuntimeError):migration.migrate('not-a-database.db')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'new-test.db'
            db=sqlite3.connect(path)
            with patch('classification_schema.install'):
                crear_esquema(db)
            db.execute("INSERT INTO concept(preferred_label,semantic_field_1) VALUES('LEGACY','Original')")
            db.commit();db.close()
            self.assertTrue(migration.migrate(path,test_only=True))
            self.assertFalse(migration.migrate(path,test_only=True))
            db=sqlite3.connect(path)
            try:
                self.assertEqual('Original',db.execute('SELECT semantic_field_1 FROM concept').fetchone()[0])
                self.assertEqual(0,db.execute('SELECT count(*) FROM concept_classification_revision').fetchone()[0])
                self.assertEqual([],db.execute('PRAGMA foreign_key_check').fetchall())
            finally:db.close()


class ClassificationRouteTests(unittest.TestCase):
    def setUp(self):
        ClassificationTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'route-test.db'
        target=sqlite3.connect(self.path);self.db.backup(target);target.close();self.db.close()
        self.db=self.connect();self.addCleanup(self.db.close)
        from routes.concepts import concepts_bp
        from routes.submissions import submissions_bp
        from routes.occurrences import occurrences_bp
        from routes.alternatives import alternatives_bp
        from routes.main import main_bp
        from concept_labels import human_concept_label, alternative_display_label
        self.app=Flask(__name__,template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        self.app.config.update(TESTING=True,SECRET_KEY='phase19-tests')
        self.app.jinja_env.filters.update(human_concept_label=human_concept_label,alternative_display_label=alternative_display_label)
        for bp in (concepts_bp,submissions_bp,occurrences_bp,alternatives_bp,main_bp):self.app.register_blueprint(bp)
        self.role='reviewer'
        @self.app.before_request
        def actor():g.current_access_role=self.role
        self.client=self.app.test_client()
        for module in ('concepts','submissions','occurrences','alternatives'):
            patcher=patch('routes.'+module+'.conectar',self.connect);patcher.start();self.addCleanup(patcher.stop)

    def connect(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

    def token(self,url,name='edit_token'):
        from tests.form_client import hidden
        response=self.client.get(url)
        self.assertEqual(200,response.status_code,response.get_data(as_text=True))
        return hidden(response.get_data(as_text=True),name)

    def selections(self,fields=None,areas=None,action=None):
        data={}
        for sid,values in ((self.sf,fields),(self.ka,areas)):
            if values is not None:
                values=normalize_pair(values)
                data[f'classification_apply_{sid}']='yes'
                data.update({f'category_{sid}_{i}':str(value or '') for i,value in enumerate(values,1)})
        if action:data[f'collection_action_{self.collection}']=action
        return data

    def test_create_edit_leave_rejoin_and_history_ui(self):
        page=self.client.get('/conceptos');self.assertEqual(200,page.status_code)
        data={'preferred_label':'TRES',**self.selections(list(reversed(self.fields[:2])),self.areas[:2],'join')}
        response=self.client.post('/conceptos/nuevo',data=data);self.assertEqual(302,response.status_code,response.data)
        cid=self.db.execute("SELECT concept_id FROM concept WHERE preferred_label='TRES'").fetchone()[0]
        edit=f'/conceptos/{cid}/editar';update=f'/conceptos/{cid}/actualizar'
        token=self.token(edit)
        data={'preferred_label':'TRES','edit_token':token,**self.selections([],None,'leave')}
        self.assertEqual(302,self.client.post(update,data=data).status_code)
        self.assertEqual(409,self.client.post(update,data=data).status_code)
        data={'preferred_label':'TRES','edit_token':self.token(edit),**self.selections(action='join')}
        self.assertEqual(302,self.client.post(update,data=data).status_code)
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM concept_classification_revision WHERE concept_id=? AND system_id=? AND ended_at IS NULL',(cid,self.ka)).fetchone()[0])
        self.role='analyst'
        page=self.client.get(f'/conceptos/{cid}/clasificaciones')
        self.assertEqual(200,page.status_code);self.assertIn('Astronomía',page.get_data(as_text=True))
        self.assertEqual(404,self.client.get(edit).status_code)
        self.assertEqual(404,self.client.post('/conceptos/nuevo',data={'preferred_label':'DENEGADO'}).status_code)

    def test_master_admin_and_catalog_stale_edit(self):
        url='/administracion/clasificaciones'
        self.assertEqual(404,self.client.get(url).status_code)
        self.role='analyst';self.assertEqual(404,self.client.post(url,data={}).status_code)
        self.role='master';token=self.token(url)
        concept_token=self.token('/conceptos/1/editar')
        data={'edit_token':token,'kind':'category','identifier':self.fields[0],'name':'Nuevo nombre','active':'0'}
        self.assertEqual(302,self.client.post(url,data=data).status_code)
        self.assertEqual(409,self.client.post(url,data=data).status_code)
        self.assertEqual(409,self.client.post('/conceptos/1/actualizar',data={'preferred_label':'UNO','edit_token':concept_token}).status_code)

    def test_analyst_proposal_and_reviewer_correction_http(self):
        self.role='analyst'
        self.assertEqual(200,self.client.get('/ocurrencias/1/clasificar').status_code)
        data={'proposal_kind':'NEW','phonological_relation_answer':'NO','morphology_component_count':'N/A',
              **self.selections(self.fields[:2],self.areas[:2],'join')}
        response=self.client.post('/ocurrencias/1/clasificar',data=data)
        self.assertEqual(302,response.status_code,response.data)
        sid=self.db.execute('SELECT submission_id FROM submission').fetchone()[0]
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM concept_classification_revision').fetchone()[0])
        self.role='reviewer'
        url=f'/aportes/{sid}'
        token=self.token(url,'concept_edit_token')
        data={'concept_edit_token':token,'concept_action':'CONFIRM_REFERENCE',
              'metadata_reviewed':'yes','metadata_target':1,
              **self.selections([],self.areas[2:3],'join')}
        response=self.client.post(url+'/concepto',data=data);self.assertEqual(302,response.status_code,response.data)
        page=self.client.get(url).get_data(as_text=True)
        self.assertIn('Actividades y acciones',page);self.assertIn('Física',page)
        self.assertEqual(409,self.client.post(url+'/concepto',data=data).status_code)

    def test_reviewer_destination_must_match_displayed_state(self):
        sid=create_alternative_submission(self.db,1,'NEW',phonological_relation_answer='NO',morphology={'component_count_not_applicable':True},access_role='analyst')
        url=f'/aportes/{sid}'
        data={'concept_edit_token':self.token(url,'concept_edit_token'),'concept_action':'USE_EXISTING','concept_id':2,
              'concept_note':'Otro Concept','metadata_reviewed':'yes','metadata_target':1,
              **self.selections(self.fields[:1])}
        self.assertEqual(400,self.client.post(url+'/concepto',data=data).status_code)
        data.update(concept_edit_token=self.token(url+'?metadata_target=2','concept_edit_token'),metadata_target=2)
        self.assertEqual(302,self.client.post(url+'/concepto',data=data).status_code)

    def test_invalid_edit_rolls_back_label_and_membership(self):
        data={'preferred_label':'RENOMBRADO','edit_token':self.token('/conceptos/1/editar'),
              **self.selections(self.fields[:1],None,'join')}
        data[f'category_{self.sf}_2']=self.fields[0]
        self.assertEqual(400,self.client.post('/conceptos/1/actualizar',data=data).status_code)
        self.assertEqual('UNO',self.db.execute('SELECT preferred_label FROM concept WHERE concept_id=1').fetchone()[0])
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM collection_membership').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
