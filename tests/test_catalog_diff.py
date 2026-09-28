import copy
import json
import unittest

from catalog_diff import build_catalog_diff


class CatalogDiffTests(unittest.TestCase):
    def projection(self, **metadata):
        return {'concepts': [{'concept_id': 1, 'preferred_label': 'UNO', **metadata}]}

    def test_membership_entry_exit_change_and_equivalent_order(self):
        a = {'collection_id': 1, 'code': 'a', 'name': 'A'}
        b = {'collection_id': 2, 'code': 'b', 'name': 'B'}
        for before, after in [([], [a]), ([a], []), ([a], [dict(a, name='Nuevo')])]:
            with self.subTest(before=before, after=after):
                diff = build_catalog_diff(self.projection(collections=before), self.projection(collections=after))
                self.assertEqual(diff['collections_changed'], [{'concept_id': 1, 'preferred_label': 'UNO', 'before': before, 'after': after}])
                self.assertEqual(diff['concepts_changed'], [])
                self.assertEqual(json.loads(json.dumps(diff)), diff)
        diff = build_catalog_diff(self.projection(collections=[b, a]), self.projection(collections=[a, b]))
        self.assertEqual(diff['collections_changed'], [])

    def test_classification_changes_preserve_order_and_system_presence(self):
        a, b = {'code': '1', 'name': 'Uno'}, {'code': '2', 'name': 'Dos'}
        for before, after in [({}, {'s': [a]}), ({'s': [a]}, {}),
                              ({'s': [a, b]}, {'s': [b, a]}),
                              ({'s': [a]}, {'s': [dict(a, name='Nuevo')]}),
                              ({}, {'s': []})]:
            with self.subTest(before=before, after=after):
                diff = build_catalog_diff(self.projection(classifications=before), self.projection(classifications=after))
                self.assertEqual(diff['classifications_changed'], [{'concept_id': 1, 'preferred_label': 'UNO',
                    'system_code': 's', 'before': before.get('s'), 'after': after.get('s')}])
                self.assertEqual(diff['concepts_changed'], [])
        old = self.projection(classifications={'z': [b], 'a': [a]})
        new = self.projection(classifications={'a': [a], 'z': [b]})
        self.assertEqual(build_catalog_diff(old, new)['classifications_changed'], [])
        changed = build_catalog_diff(old, self.projection())['classifications_changed']
        self.assertEqual([c['system_code'] for c in changed], ['a', 'z'])

    def test_old_snapshots_defaults_and_no_mutation(self):
        old = self.projection()
        new = self.projection(collections=[], classifications={})
        original = copy.deepcopy((old, new))
        diff = build_catalog_diff(old, new)
        self.assertFalse(any(value for value in diff.values() if isinstance(value, list)))
        self.assertEqual((old, new), original)
