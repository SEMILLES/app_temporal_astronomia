"""Ordinary review UI, using disposable synthetic databases only."""
import unittest

from tests import test_alternative_routes as fixtures
from routes.submissions import _alternative_review_context, _rows
from submission_concept_resolution import save_resolution


class LexicalUITests(unittest.TestCase):
    def setUp(self):
        self.connections = []
        fixtures.AlternativeRouteTests.setUp(self)
        self.context = self.client.application.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        for db in self.connections:
            db.close()
        fixtures.AlternativeRouteTests.tearDown(self)

    def connect(self):
        db = fixtures.AlternativeRouteTests.connect(self)
        self.connections.append(db)
        return db

    def create(self, kind='NEW', resolved=True, groups=True):
        form = {'proposal_kind': kind, 'analysis_note': 'NOTA ORIGINAL'}
        if kind == 'NEW':
            form.update(phonological_relation_answer='YES' if groups else 'NO',
                        morphology_component_count='N/A')
            if groups:
                form.update(relation_target_type='alternative', relation_target_id='1',
                            relation_parameter='CM_1', relation_uncertain='0')
        elif kind == 'EXISTING':
            form['proposed_existing_alternative_id'] = '1'
        response = self.client.post('/ocurrencias/2/clasificar', data=form)
        self.assertEqual(302, response.status_code, response.get_data(as_text=True))
        db = self.connect()
        sid = db.execute('SELECT max(submission_id) FROM submission').fetchone()[0]
        if resolved:
            save_resolution(db, sid, 'CONFIRM_REFERENCE', access_role='reviewer')
        db.close()
        return sid

    def dump(self):
        db = self.connect()
        result = '\n'.join(db.iterdump())
        db.close()
        return result

    def page(self, sid):
        response = self.client.get(f'/aportes/{sid}')
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def post(self, sid, **form):
        return self.client.post(f'/aportes/{sid}/decidir', data=form)

    def test_unresolved_concept_blocks_closing_and_keeps_concept_editor(self):
        sid = self.create(resolved=False)
        page = self.page(sid)
        self.assertIn('<fieldset disabled><legend>Resolución del resto del análisis', page)
        self.assertIn('Guardar resolución del concepto', page)
        before = self.dump()
        self.assertEqual(400, self.post(sid, decision='new', relations_resolution='ACCEPTED', morphology_resolution='ACCEPTED').status_code)
        self.assertEqual(before, self.dump())

    def test_resolved_controls_proposal_and_no_ambiguous_defaults(self):
        sid = self.create()
        page = self.page(sid)
        self.assertIn('<fieldset><legend>Resolución del resto del análisis', page)
        self.assertIn('Modificar resolución', page)
        for field in ('relations_resolution', 'morphology_resolution'):
            for value in ('ACCEPTED', 'REJECTED', 'pending'):
                self.assertIn(f'name="{field}" value="{value}"', page)
            self.assertNotIn(f'value="ACCEPTED" checked', page)
            self.assertNotIn(f'value="REJECTED" checked', page)
        for text in ('NOTA ORIGINAL', 'CM_1', 'Con duda', 'Tipo original: NEW', 'no se incorporará', 'Nota de revisión'):
            self.assertIn(text, page)
        self.assertNotIn('value="union"', page)
        self.assertNotIn('name="nomenclature_reason"', page)

    def test_pending_or_omitted_groups_and_leave_pending_have_zero_effects(self):
        sid = self.create()
        before = self.dump()
        for relation, morphology in [('pending', 'ACCEPTED'), ('ACCEPTED', 'pending'), (None, 'ACCEPTED'), ('ACCEPTED', None)]:
            form = dict(decision='new', review_note='Nota')
            if relation is not None: form['relations_resolution'] = relation
            if morphology is not None: form['morphology_resolution'] = morphology
            with self.subTest(form=form):
                self.assertEqual(400, self.post(sid, **form).status_code)
                self.assertEqual(before, self.dump())
        self.assertEqual(302, self.post(sid, decision='pending', relations_resolution='ACCEPTED', morphology_resolution='ACCEPTED', nomenclature_mode='manual', label_new='999').status_code)
        self.assertEqual(before, self.dump())

    def test_existing_requires_rejection_and_does_not_materialize_groups(self):
        sid = self.create()
        before = self.dump()
        for group in ('ACCEPTED', 'pending', None):
            form = dict(decision='existing', alternative_id='1', review_note='Conservar destino')
            if group: form.update(relations_resolution=group, morphology_resolution=group)
            self.assertEqual(400, self.post(sid, **form).status_code)
            self.assertEqual(before, self.dump())
        self.assertEqual(302, self.post(sid, decision='existing', alternative_id='1', relations_resolution='REJECTED', morphology_resolution='REJECTED', review_note='Conservar destino').status_code)
        db = self.connect()
        self.assertEqual(0, db.execute('SELECT count(*) FROM alternative_relation').fetchone()[0])
        self.assertEqual(0, db.execute('SELECT count(*) FROM alternative_morphology').fetchone()[0])
        db.close()

    def test_reject_rest_needs_only_main_decision_and_note(self):
        sid = self.create()
        self.assertEqual(400, self.post(sid, decision='rejected').status_code)
        self.assertEqual(302, self.post(sid, decision='rejected', review_note='Resto rechazado').status_code)
        page = self.page(sid)
        for text in ('Rechazó el resto del análisis', 'Relaciones: rechazadas', 'Morfología: rechazada', 'La clasificación permaneció sin cambios.', 'NOTA ORIGINAL'):
            self.assertIn(text, page)

    def test_preview_variants_match_final_nomenclature(self):
        sid = self.create()
        db = self.connect()
        ctx = _alternative_review_context(db, _rows(db))[sid]
        previews = ctx['nomenclature_previews']
        self.assertEqual({'ACCEPTED', 'REJECTED'}, set(previews))
        self.assertNotEqual(previews['ACCEPTED']['suggestions'], previews['REJECTED']['suggestions'])
        db.close()
        self.assertEqual(302, self.post(sid, decision='new', relations_resolution='REJECTED', morphology_resolution='ACCEPTED', review_note='Sin relación').status_code)
        db = self.connect()
        decision = db.execute('SELECT * FROM submission_lexical_decision').fetchone()
        self.assertEqual(previews['REJECTED']['suggestions']['new'], decision['alternative_label_snapshot'])
        db.close()

    def test_accepted_preview_matches_materialized_result_and_original_is_preserved(self):
        sid = self.create()
        db = self.connect()
        preview = _alternative_review_context(db, _rows(db))[sid]['nomenclature_previews']['ACCEPTED']
        original = tuple(db.execute('SELECT proposal_kind,analysis_note,phonological_relation_answer FROM alternative_submission').fetchone())
        relations = [tuple(r) for r in db.execute('SELECT * FROM alternative_submission_relation')]
        db.close()
        self.assertEqual(302, self.post(sid, decision='new', relations_resolution='ACCEPTED', morphology_resolution='ACCEPTED').status_code)
        db = self.connect()
        self.assertEqual(preview['suggestions']['new'], db.execute('SELECT alternative_label_snapshot FROM submission_lexical_decision').fetchone()[0])
        self.assertEqual(original, tuple(db.execute('SELECT proposal_kind,analysis_note,phonological_relation_answer FROM alternative_submission').fetchone()))
        self.assertEqual(relations, [tuple(r) for r in db.execute('SELECT * FROM alternative_submission_relation')])
        db.close()
        page = self.page(sid)
        for text in ('Creó una Alternative nueva', 'Relaciones: aceptadas', 'Morfología: aceptada', 'Morfología resultante:', 'NOTA ORIGINAL', 'Con duda'):
            self.assertIn(text, page)

    def test_selector_only_local_concept_and_active_alternatives(self):
        sid = self.create('EXISTING')
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('LOCAL')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'LOCAL-ACTIVE')")
        db.execute("INSERT INTO alternative(concept_id,working_label,retired_at) VALUES(2,'LOCAL-RETIRED',CURRENT_TIMESTAMP)")
        db.commit()
        save_resolution(db, sid, 'USE_EXISTING', concept_id=2, note='Cambio conceptual', access_role='reviewer')
        ctx = _alternative_review_context(db, _rows(db))[sid]
        self.assertEqual(['LOCAL-ACTIVE'], [a['working_label'] for a in ctx['alternatives']])
        self.assertFalse(ctx['proposed_alternative_matches'])
        db.close()
        self.assertEqual(400, self.post(sid, decision='existing', alternative_id='1', review_note='Destino ajeno').status_code)

    def test_historical_snapshots_survive_rename_move_and_retirement(self):
        sid = self.create('EXISTING')
        self.assertEqual(302, self.post(sid, decision='existing_proposed').status_code)
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('CONCEPTO ACTUAL')")
        db.execute("UPDATE alternative SET working_label='RENOMBRADA',concept_id=2,retired_at=CURRENT_TIMESTAMP WHERE alternative_id=1")
        db.commit(); db.close()
        page = self.page(sid)
        history = page.split('<section class="lexical-history">')[1].split('</section>')[0]
        current = page.split('<section class="lexical-current">')[1].split('</section>')[0]
        for text in ('Usó una Alternative existente', 'Concepto snapshot: TEST', 'Alternative snapshot: TEST-1', 'Se creó la clasificación', 'Relaciones: no propuestas', 'Morfología: no propuesta'):
            self.assertIn(text, history)
        self.assertNotIn('RENOMBRADA', history)
        self.assertNotIn('CONCEPTO ACTUAL', history)
        for text in ('RENOMBRADA', 'CONCEPTO ACTUAL', 'Retirada', 'CLASIFICACIÓN VIGENTE ACTUAL'):
            self.assertIn(text, current)

    def test_legacy_closed_without_decision_does_not_infer_history(self):
        sid = self.create('EXISTING', resolved=False)
        db = self.connect()
        db.execute("UPDATE submission SET status='resolved',resolution='accepted',review_note='NOTA HISTÓRICA' WHERE submission_id=?", (sid,))
        db.execute('UPDATE alternative_submission SET resolved_alternative_id=1 WHERE submission_id=?', (sid,))
        db.commit(); db.close()
        page = self.page(sid)
        self.assertIn('Decisión léxica explícita no registrada.', page)
        self.assertIn('NOTA HISTÓRICA', page)
        self.assertIn('NOTA ORIGINAL', page)
        self.assertNotIn('Alternative snapshot:', page)
        self.assertNotIn('Usó una Alternative existente', page)

    def test_existing_to_new_without_morphology_and_note_validation(self):
        sid = self.create('EXISTING')
        self.assertIn('Sin morfología propuesta.', self.page(sid))
        self.assertNotIn('name="morphology_resolution"', self.page(sid))
        before = self.dump()
        self.assertEqual(400, self.post(sid, decision='new').status_code)
        self.assertEqual(before, self.dump())
        self.assertEqual(302, self.post(sid, decision='new', review_note='Forma distinta').status_code)

    def test_free_component_label_is_visible_after_closure(self):
        sid = self.create(groups=False)
        db = self.connect()
        db.execute("INSERT INTO alternative_submission_component(submission_id,position,component_label,note) VALUES(?,1,'LABEL ORIGINAL','NOTA COMPONENTE')", (sid,))
        db.commit(); db.close()
        self.assertEqual(302, self.post(sid, decision='new', morphology_resolution='REJECTED', review_note='No incorporar componentes').status_code)
        page = self.page(sid)
        self.assertIn('LABEL ORIGINAL', page)
        self.assertIn('NOTA COMPONENTE', page)
