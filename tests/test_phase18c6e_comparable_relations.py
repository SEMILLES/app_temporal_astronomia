"""Comparable destinations, resolution and visual evidence without schema changes."""
import unittest
from tests import test_alternative_workflow as workflow_fixtures
from tests import test_phase18c6d_analysis as ui_fixtures
from alternative_workflow import (AlternativeWorkflowError, _relation_targets,
    _materialize_relations, review_as_new, review_as_existing,
    reject_alternative_submission, create_alternative_submission)
from alternative_video_service import add_video


class ComparableWorkflowTests(unittest.TestCase):
    setUp = workflow_fixtures.AlternativeWorkflowTests.setUp
    tearDown = workflow_fixtures.AlternativeWorkflowTests.tearDown
    create = workflow_fixtures.AlternativeWorkflowTests.create

    def pair(self):
        target = self.create(occurrence=1, phonological_relation_answer='NO')
        origin = self.create(phonological_relation_answer='YES', relations=[
            {'target_submission_id':target, 'phonological_parameter':'N_MANOS'}])
        return target, origin

    def accept(self, sid, resolution='ACCEPTED'):
        return review_as_new(self.db, sid, relations_resolution=resolution,
            morphology_resolution='ACCEPTED', access_role='reviewer', review_note='Revisión explícita')

    def test_duplicate_alternative_any_parameter(self):
        for parameter in ('CM_1', 'N_MANOS'):
            with self.assertRaisesRegex(AlternativeWorkflowError, 'destino está repetido'):
                self.create(phonological_relation_answer='YES', relations=[
                    {'target_alternative_id':1,'phonological_parameter':p} for p in ('CM_1',parameter)])

    def test_pending_blocks_acceptance_atomically_and_rejection_is_allowed(self):
        _, sid = self.pair()
        before = '\n'.join(self.db.iterdump())
        with self.assertRaisesRegex(AlternativeWorkflowError, 'todavía no ha sido resuelta'):
            self.accept(sid)
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        self.accept(sid, 'REJECTED')
        self.assertEqual(0, self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])

    def test_target_resolves_new(self):
        target, sid = self.pair()
        destination = review_as_new(self.db, target, morphology_resolution='ACCEPTED', access_role='reviewer')
        self.assertEqual([(destination,'N_MANOS')], _relation_targets(self.db,sid))
        self.accept(sid)
        self.assertEqual(1, self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])

    def test_target_resolves_existing(self):
        target, sid = self.pair()
        review_as_existing(self.db,target,2,morphology_resolution='REJECTED',access_role='reviewer',review_note='Usar existente')
        self.assertEqual([(2,'N_MANOS')], _relation_targets(self.db,sid))
        self.accept(sid)

    def test_rejected_target_has_no_destination(self):
        target, sid = self.pair()
        reject_alternative_submission(self.db,target,access_role='reviewer',review_note='Rechazo')
        with self.assertRaisesRegex(AlternativeWorkflowError,'sin alternativa resultante'):
            self.accept(sid)
        self.accept(sid,'REJECTED')

    def test_collapsed_targets_block_even_same_parameter(self):
        x = self.create(occurrence=1,phonological_relation_answer='NO')
        y = self.create(occurrence=2,phonological_relation_answer='NO')
        sid = self.create(phonological_relation_answer='YES',relations=[
            {'target_submission_id':x,'phonological_parameter':'CM_1'},
            {'target_submission_id':y,'phonological_parameter':'N_MANOS'}])
        for target in (x,y):
            review_as_existing(self.db,target,3,morphology_resolution='REJECTED',access_role='reviewer',review_note='Destino común')
        for parameter in ('N_MANOS','CM_1'):
            self.db.execute('UPDATE alternative_submission_relation SET phonological_parameter=? WHERE target_submission_id=?',(parameter,y))
            with self.assertRaisesRegex(AlternativeWorkflowError,'misma alternativa'):
                self.accept(sid)
        self.assertEqual(0,self.db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])

    def test_self_relation_is_explicit(self):
        target, sid = self.pair()
        review_as_existing(self.db,target,2,morphology_resolution='REJECTED',access_role='reviewer',review_note='Destino')
        with self.assertRaisesRegex(AlternativeWorkflowError,'autorrelación'):
            _materialize_relations(self.db,2,sid)

    def test_no_eligible_destination(self):
        self.db.execute('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP')
        with self.assertRaisesRegex(AlternativeWorkflowError,'No existen otras alternativas'):
            self.create(phonological_relation_answer='YES')


class ComparableUITests(unittest.TestCase):
    role = "reviewer"
    setUp = ui_fixtures.AnalysisFlowTests.setUp
    tearDown = ui_fixtures.AnalysisFlowTests.tearDown
    connect = ui_fixtures.AnalysisFlowTests.connect
    database = ui_fixtures.AnalysisFlowTests.database
    payload = ui_fixtures.AnalysisFlowTests.payload
    base = ui_fixtures.AnalysisFlowTests.base

    def pending(self):
        with self.database() as db:
            oid = db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(1,'DESTINO',2002)").lastrowid
            db.execute('INSERT INTO occurrence_concept_reference(occurrence_id,concept_id) VALUES(?,1)',(oid,))
            sid = create_alternative_submission(db,oid,'NEW',phonological_relation_answer='NO',morphology={'component_count_not_applicable':True})
        return oid,sid

    def test_pending_selector_occ_id_and_immediate_block(self):
        oid,target = self.pending()
        html = self.client.get('/ocurrencias/1/clasificar').text
        self.assertIn(f'value="submission:{target}"',html)
        self.assertIn(f'OCC-{oid:06d} · DESTINO',html)
        self.assertNotIn(f'[PENDIENTE #{target}]',html)
        data = self.payload(relation_target_type=['submission'],relation_target_id=[str(target)],relation_parameter=['N_MANOS'])
        self.role = 'reviewer'
        for action in ('preview','confirmar'):
            response = self.client.post(self.base+action,data=data)
            self.assertEqual(400,response.status_code)
            self.assertIn('todavía no ha sido resuelta',response.text)
        # The actual compact selector also submits correctly without JavaScript conversion.
        data.pop('relation_target_type');data.pop('relation_target_id')
        data.update(relation_target_type_0='alternative',relation_alternative_id=f'submission:{target}')
        self.assertEqual(302,self.client.post('/ocurrencias/1/clasificar',data=data).status_code)
        with self.database() as db:
            sid=db.execute('SELECT submission_id FROM submission WHERE occurrence_id=1').fetchone()[0]
            from submission_concept_resolution import save_resolution
            save_resolution(db,sid,'CONFIRM_REFERENCE',access_role='reviewer')
        detail=self.client.get(f'/aportes/{sid}').text
        self.assertIn(f'OCC-{oid:06d} · DESTINO',detail)
        self.assertIn('data-blocked="true"',detail)
        self.assertNotIn('data-resolution="ACCEPTED"',detail)
        self.assertIn('data-resolution="REJECTED"',detail)
        self.assertEqual(1,detail.count('name="relations_resolution" value="REJECTED"'))
        self.assertEqual(302,self.client.post(f'/aportes/{sid}/decidir',data={'decision':'pending'}).status_code)
        from submission_concept_resolution import save_resolution
        with self.database() as db:
            save_resolution(db,target,'CONFIRM_REFERENCE',access_role='reviewer')
            review_as_existing(db,target,1,morphology_resolution='REJECTED',access_role='reviewer',review_note='Existente')
        detail=self.client.get(f'/aportes/{sid}').text
        self.assertIn('Resuelta como:',detail)
        self.assertNotIn('todavía no ha sido resuelta',detail)
        self.assertIn('data-resolution="ACCEPTED"',detail)

    def test_all_occ_ids_and_optional_video(self):
        oid,_=self.pending()
        with self.database() as db:
            db.executemany('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(?,1)',[(1,),(oid,)])
        html=self.client.get('/ocurrencias/1/clasificar').text
        for value in (1,oid):self.assertIn(f'OCC-{value:06d}',html)
        self.assertNotIn('Ver video canónico',html)
        with self.database() as db:
            add_video(db,1,'https://www.youtube.com/watch?v=dQw4w9WgXcQ',{'access_role':'reviewer'})
        html=self.client.get('/ocurrencias/1/clasificar').text
        self.assertIn('Ver video canónico',html)
        self.assertIn('https://www.youtube.com/watch?v=dQw4w9WgXcQ',html)
        for value in (1,oid):self.assertIn(f'OCC-{value:06d}',html)

    def test_no_targets_disables_yes_and_server_rejects(self):
        with self.database() as db:db.execute('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP')
        html=self.client.get('/ocurrencias/1/clasificar').text
        self.assertIn('<option value="YES" disabled',html)
        self.assertIn('No existen otras alternativas',html)
        response=self.client.post('/ocurrencias/1/clasificar',data=self.payload())
        self.assertEqual(400,response.status_code)
        self.assertIn('No existen otras alternativas',response.text)

    def test_collapsed_targets_disable_accepted_preview_and_control(self):
        from submission_concept_resolution import save_resolution
        _, x = self.pending()
        _, y = self.pending()
        data = self.payload(relation_target_type=['submission','submission'],
            relation_target_id=[str(x),str(y)])
        self.assertEqual(302,self.client.post('/ocurrencias/1/clasificar',data=data).status_code)
        with self.database() as db:
            sid=db.execute('SELECT submission_id FROM submission WHERE occurrence_id=1').fetchone()[0]
            for target in (sid,x,y):
                save_resolution(db,target,'CONFIRM_REFERENCE',access_role='reviewer')
            for target in (x,y):
                review_as_existing(db,target,1,morphology_resolution='REJECTED',access_role='reviewer',review_note='Mismo destino')
        detail=self.client.get(f'/aportes/{sid}').text
        self.assertIn('misma alternativa',detail)
        self.assertIn('data-blocked="true"',detail)
        self.assertNotIn('data-resolution="ACCEPTED"',detail)
        self.assertIn('data-resolution="REJECTED"',detail)
