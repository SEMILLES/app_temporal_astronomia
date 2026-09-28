import copy
import unittest

from catalog_views import CATALOG_VIEWS, scope_catalog
from routes.catalog import _catalog_counts


class CatalogViewTests(unittest.TestCase):
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
