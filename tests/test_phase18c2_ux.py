import unittest

from playwright.sync_api import sync_playwright
from werkzeug.datastructures import MultiDict

from routes.concepts import concepts_bp
from routes.occurrences import _component_rows
from tests import test_alternative_routes as alternatives
from tests import test_occurrence_grammar_routes as grammar
from tests import test_catalog_legacy_ui as catalog


class GrammarReviewUXTests(unittest.TestCase):
    setUp = grammar.GrammarWorkflowRouteTests.setUp
    tearDown = grammar.GrammarWorkflowRouteTests.tearDown
    connect = grammar.GrammarWorkflowRouteTests.connect

    def test_review_context_note_and_field_uncertainty(self):
        db = self.connect()
        db.execute('INSERT INTO assignment(occurrence_id,alternative_id) VALUES(1,1)')
        db.commit()
        db.close()
        response = self.client.post('/ocurrencias/1/gramatica', data={
            'gender': 'SIN-MARCA', 'gender_uncertain': 'on',
            'plural': 'SEÑA-DIFERENTES', 'agentive': 'K (P-ASL)',
            'agentive_uncertain': 'on', 'note': 'Help <script>note</script>',
        })
        self.assertEqual(response.status_code, 302)
        for path in ('/aportes/pendientes', '/aportes/1'):
            html = self.client.get(path).get_data(as_text=True)
            for text in ('Ocurrencia ID:</strong> 1', 'EVIDENCE', 'Synthetic source',
                         'Concepto contextual:</strong> ASTRONOMIA',
                         'Asignación vigente:</strong> ASTRONOMIA-1a',
                         'Nota del analista:</strong> Help &lt;script&gt;note&lt;/script&gt;',
                         'SIN-MARCA <strong>(con duda)</strong>',
                         'K (P-ASL) <strong>(con duda)</strong>',
                         '<td>SEÑA-DIFERENTES</td>', '<td>Sin analizar</td>'):
                self.assertIn(text, html)
            self.assertEqual(html.count('(con duda)'), 2)


class ComponentAndConceptUXTests(unittest.TestCase):
    setUp = alternatives.AlternativeRouteTests.setUp
    tearDown = alternatives.AlternativeRouteTests.tearDown
    connect = alternatives.AlternativeRouteTests.connect

    def test_review_uses_current_component_label_and_preserves_note(self):
        response = self.client.post('/ocurrencias/2/clasificar', data={
            'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO',
            'morphology_component_count': '2', 'free_permutation': 'NO',
            'record_components': 'yes', 'component_position': ['1', '2'],
            'component_type': ['existing', 'unapproved'],
            'component_alternative_id': ['1', ''], 'component_note': ['', 'DUDA <test>'],
        })
        self.assertEqual(response.status_code, 302)
        db = self.connect()
        db.execute("UPDATE alternative SET working_label='1a' WHERE alternative_id=1")
        db.commit()
        before = list(db.execute('SELECT * FROM alternative_submission_component'))
        db.close()
        for path in ('/aportes/pendientes', '/aportes/1'):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('Alternativa existente: TEST-1a (ID 1)', html)
            self.assertIn('Componente por revisar &mdash; DUDA &lt;test&gt;', html)
            self.assertNotIn('Alternative vigente', html)
        db = self.connect()
        self.assertEqual(before, list(db.execute('SELECT * FROM alternative_submission_component')))
        db.close()

    def test_remove_last_preserves_previous_rows_and_backend_alignment(self):
        html = self.client.get('/ocurrencias/2/clasificar').get_data(as_text=True)
        self.assertIn('Alternativa vigente', html)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.set_content(html)
            page.locator('[name=proposal_kind][value=NEW]').check()
            page.locator('[name=morphology_component_count]').select_option('3')
            page.locator('[name=record_components][value=yes]').check()
            for i in range(3):
                page.locator('#add-component').click()
                page.locator(f'[name=component_{i}_type][value=unapproved]').check()
                page.locator(f'[name=component_{i}_note]').fill(f'Note {i}')
            def values():
                return page.locator('#components').evaluate(
                    "node => Array.from(new FormData(node.closest('form')).entries())")
            before = values()
            page.locator('#add-component').click()
            malformed = MultiDict(values())
            response = self.client.post('/ocurrencias/2/clasificar', data=malformed)
            self.assertEqual(response.status_code, 400)
            self.assertIn('Estructura de componentes incompleta o desalineada.', response.get_data(as_text=True))
            page.locator('#remove-last-component').click()
            self.assertEqual(values(), before)
            self.assertEqual([r['note'] for r in _component_rows(MultiDict(values()))],
                             ['Note 0', 'Note 1', 'Note 2'])
            page.locator('#remove-last-component').click()
            page.locator('#remove-last-component').click()
            self.assertTrue(page.locator('#remove-last-component').is_disabled())
            page.locator('#remove-last-component').evaluate('button => button.onclick()')
            self.assertEqual(page.locator('#components .component').count(), 1)
            page.locator('#add-component').click()
            self.assertEqual(page.locator('[name=component_row_id]').evaluate_all(
                'items => items.map(x => x.value)'), ['0', '1'])
            self.assertEqual(page.locator('[name=component_0_note]').input_value(), 'Note 0')
            browser.close()

    def test_concept_order_default_oldest_az_and_invalid(self):
        app = self.client.application
        app.register_blueprint(concepts_bp)
        app.add_url_rule('/trabajo', endpoint='main.trabajo', view_func=lambda: '')
        app.add_url_rule('/alternativas/<int:concept_id>', endpoint='alternatives.alternativas',
                         view_func=lambda concept_id: '')
        db = self.connect()
        db.executemany('INSERT INTO concept(preferred_label) VALUES(?)', [('ZETA',), ('ALFA',)])
        db.commit()
        db.close()
        import re
        for query, expected in [('', [3, 2, 1]), ('?sort=recent', [3, 2, 1]),
                                ('?sort=oldest', [1, 2, 3]), ('?sort=az', [3, 1, 2]),
                                ('?sort=invalid', [3, 2, 1])]:
            html = self.client.get('/conceptos' + query).get_data(as_text=True)
            self.assertEqual([int(x) for x in re.findall(r'<td>\s*(\d+)\s*</td>', html)], expected)
            for label in ('Más recientes', 'Más antiguos', 'A–Z'):
                self.assertIn(label, html)


class CatalogComponentUXTests(unittest.TestCase):
    setUp = catalog.LegacyCatalogUITests.setUp
    tearDown = catalog.LegacyCatalogUITests.tearDown
    connect = catalog.LegacyCatalogUITests.connect
    digest = catalog.LegacyCatalogUITests.digest

    def test_structured_components_use_related_concept_and_keep_notes(self):
        db = self.connect()
        db.execute("INSERT INTO concept(preferred_label) VALUES('OTHER-CONCEPT')")
        db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        db.execute('UPDATE alternative_component SET component_alternative_id=4,component_label=NULL WHERE position=1')
        db.execute("INSERT INTO alternative_component(alternative_morphology_id,position,note) VALUES(1,3,'DUDA <test>')")
        db.commit()
        db.close()
        before = self.digest()
        html = self.client.get('/ana/catalogo-interno/alternativas/1').get_data(as_text=True)
        for text in ('Componente 1', '<dt>Concepto</dt><dd>OTHER-CONCEPT',
                     '<dt>Alternativa</dt><dd>OTHER-CONCEPT-1a', 'Componente 3',
                     '<dt>Estado</dt><dd>Por revisar / no aprobado',
                     '<dt>Nota</dt><dd>DUDA &lt;test&gt;'):
            self.assertIn(text, html)
        self.assertLess(html.index('Componente 1'), html.index('Componente 3'))
        self.assertEqual(before, self.digest())
