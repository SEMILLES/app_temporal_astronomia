import copy
import unittest
from pathlib import Path
from flask import Flask, render_template

from catalog_views import CATALOG_VIEWS, scope_catalog
from routes.catalog import _catalog_counts


class CatalogViewTests(unittest.TestCase):
    def test_shared_themes_and_internal_navigation_templates(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / 'static/catalogo/catalogo-refinements.css').read_text(encoding='utf-8')
        for theme in ('analizada', 'academica'):
            self.assertIn('.catalogo--' + theme, css)
        for variable in ('primary', 'secondary', 'soft', 'surface', 'focus'):
            self.assertIn('--catalog-' + variable, css)
        self.assertIn('--red:', css)  # Network colors remain independent.
        app = Flask(__name__, template_folder=str(root / 'templates'))
        with app.test_request_context('/ana/trabajo'):
            for role in ('analyst', 'reviewer', 'master'):
                html = render_template('_internal_header.html', role=role, root='/ana',
                                       collaborators=[], show_review=role != 'analyst', show_admin=role == 'master')
                self.assertIn('href="/ana/catalogo-interno"', html)
                self.assertIn('href="/ana/catalogo-interno/colecciones/academica"', html)
        workspace = (root / 'templates/trabajo.html').read_text(encoding='utf-8')
        self.assertIn('/catalogo-interno/colecciones/academica', workspace)
        self.assertIn('Colección Analizada', workspace)
        template = (root / 'templates/catalogo_lesico.html').read_text(encoding='utf-8')
        self.assertNotIn('catalogo-academico.css', template)
        self.assertNotIn('catalogo-analizado.css', template)

    def test_membership_only_scope_preserves_order_content_and_original(self):
        projection = {'concepts': [
            {'concept_id': 9, 'collections': [{'code': 'academic-vocabulary'}],
             'alternatives': [{'occurrences': [1, 2]}]},
            {'concept_id': 4, 'collections': [{'code': 'other'}], 'alternatives': []},
            {'concept_id': 3, 'knowledge_areas': ['Biología'], 'alternatives': []},
            {'concept_id': 1, 'collections': [{'code': 'academic-vocabulary'}],
             'alternatives': [{'occurrences': [3]}, {'occurrences': []}]},
        ]}
        before = copy.deepcopy(projection)
        analyzed = scope_catalog(projection, CATALOG_VIEWS['analizada'])
        academic = scope_catalog(projection, CATALOG_VIEWS['academica'])
        self.assertEqual(analyzed, projection)
        self.assertIsNot(analyzed['concepts'], projection['concepts'])
        self.assertEqual([c['concept_id'] for c in academic['concepts']], [9, 1])
        self.assertEqual(academic['concepts'], [before['concepts'][0], before['concepts'][3]])
        self.assertEqual(_catalog_counts(academic), {'concepts': 2, 'alternatives': 3, 'occurrences': 3})
        self.assertEqual(projection, before)

    def test_empty_and_old_snapshots(self):
        for projection in ({}, {'concepts': []}, {'concepts': [{'concept_id': 1}]}):
            self.assertEqual(scope_catalog(projection, CATALOG_VIEWS['academica'])['concepts'], [])
