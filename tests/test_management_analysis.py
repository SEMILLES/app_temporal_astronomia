"""Management UI contracts, using disposable synthetic databases only."""
import json
import re
import sqlite3
import unittest
from contextlib import closing
from html import unescape
from werkzeug.datastructures import MultiDict

from tests import test_alternative_admin as fixtures
from tests.form_client import hidden
from phonological_parameters import PHONOLOGICAL_PARAMETERS


class ManagementAnalysisTests(unittest.TestCase):
    setUp = fixtures.AlternativeAdminRouteTests.setUp
    tearDown = fixtures.AlternativeAdminRouteTests.tearDown
    path_url = '/alternativas/1/gestionar'

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            result = db.execute(sql, args).fetchall()
            db.commit()
            return result

    def save(self, count, **values):
        data = MultiDict({'action': 'morphology', 'confirm': 'yes',
                          'component_count': count, 'record_components': 'no'})
        for key, value in values.items():
            data.setlist(key, value if isinstance(value, list) else [value])
        return self.client.post(self.path_url, data=data)

    def component(self, index=0, kind='unapproved', note='Por revisar', target=''):
        return {f'component_{index}_position': str(index + 1),
                f'component_{index}_type': kind, f'component_{index}_note': note,
                f'component_{index}_alternative_id': target}

    def current(self):
        return self.query('SELECT * FROM alternative_morphology WHERE alternative_id=1 AND is_current=1')[0]

    def components(self):
        return self.query('SELECT * FROM alternative_component WHERE alternative_morphology_id=?',
                          (self.current()['alternative_morphology_id'],))

    def test_unanalysed_get_and_empty_post_do_not_create_na(self):
        self.query('DELETE FROM alternative_morphology')
        page = self.client.get(self.path_url).text
        self.assertIn('Estado: Sin analizar', page)
        self.assertNotIn('value="N/A" selected', page)
        self.assertEqual(self.save('').status_code, 400)
        self.assertEqual(self.query('SELECT * FROM alternative_morphology'), [])

    def test_one_ignores_even_malformed_hidden_components_and_permutation(self):
        result = self.save('1', record_components='yes', free_permutation='NO',
                           component_row_id='bad', component_bad_position='invalid',
                           morphology_note='Observación preservada')
        self.assertEqual(result.status_code, 200)
        row = self.current()
        self.assertEqual((row['component_count'], row['component_count_not_applicable'], row['free_permutation']), (1, 0, 'N/A'))
        self.assertEqual(row['note'], 'Observación preservada')
        self.assertEqual(self.components(), [])
        self.assertIn('id="permutation-field" hidden', result.text)
        self.assertIn('id="component-registration-field" hidden', result.text)
        self.assertEqual(len(self.query('SELECT * FROM alternative')), 2)

    def test_multiple_defaults_to_valid_unknown_and_rejects_na_permutation(self):
        result = self.save('2')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.current()['free_permutation'], 'SIN INFORMACIÓN')
        self.assertNotIn('id="permutation-field" hidden', result.text)
        self.assertNotIn('id="component-registration-field" hidden', result.text)
        self.assertEqual(self.save('2', free_permutation='N/A').status_code, 400)

    def test_na_can_save_many_components_without_count_limit(self):
        values = {'record_components': 'yes', 'component_row_id': [str(i) for i in range(11)]}
        for i in range(11):
            values.update(self.component(i))
        result = self.save('N/A', **values)
        self.assertEqual(result.status_code, 200)
        row = self.current()
        self.assertEqual((row['component_count'], row['component_count_not_applicable'], row['free_permutation']), (None, 1, 'N/A'))
        self.assertEqual(len(self.components()), 11)
        self.assertIn('id="permutation-field" hidden', result.text)
        self.assertNotIn('id="component-registration-field" hidden', result.text)

    def test_partial_components_preload_note_and_noop_preserve_history(self):
        values = dict(record_components='yes', component_row_id='0', free_permutation='SÍ',
                      morphology_note='Observación', **self.component(kind='existing', target='2', note='Nota'))
        response = self.save('4', **values)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.components()), 1)
        page = self.client.get(self.path_url).text
        self.assertIn('value="4" selected', page)
        self.assertIn('value="SÍ" selected', page)
        self.assertIn('value="yes" checked', page)
        data = json.loads(re.search(r'id="management-components">(.*?)</script>', page, re.S)[1])
        self.assertEqual(data[0], dict(position=1, type='existing', alternative_id='2', note='Nota', label=''))
        self.assertIn('>Observación</textarea>', page)
        versions = len(self.query('SELECT * FROM alternative_morphology'))
        self.assertEqual(self.save('4', **values).status_code, 200)
        self.assertEqual(len(self.query('SELECT * FROM alternative_morphology')), versions)
        self.assertEqual(self.save('1').status_code, 200)
        self.assertEqual(len(self.query('SELECT * FROM alternative_morphology')), versions + 1)
        self.assertEqual(len(self.query('SELECT * FROM alternative_component')), 1)
        self.assertIn('Historial de morfología', self.client.get(self.path_url).text)
        self.assertEqual(self.query('SELECT * FROM submission'), [])

    def test_too_many_components_rejected_and_error_keeps_inputs(self):
        values = dict(record_components='yes', component_row_id=['0','1','2'], morphology_note='No perder')
        for i in range(3):
            values.update(self.component(i))
        response = self.save('2', **values)
        self.assertEqual(response.status_code, 400)
        self.assertIn('no pueden superar', response.text)
        self.assertIn('No perder', response.text)
        self.assertEqual(len(self.query('SELECT * FROM alternative_morphology')), 1)
        data = json.loads(re.search(r'id="management-components">(.*?)</script>', response.text, re.S)[1])
        self.assertEqual(len(data), 3)

    def test_component_validation_and_no_answer_discards_stale_rows(self):
        for values in (self.component(note=''), self.component(kind='existing'),
                       self.component(kind='existing', target='999'),
                       self.component(kind='unknown')):
            self.assertEqual(self.save('N/A', record_components='yes', component_row_id='0', **values).status_code, 400)
        self.assertEqual(self.save('N/A', record_components='no', component_row_id='0', **self.component()).status_code, 200)
        self.assertEqual(self.components(), [])
        self.query('UPDATE alternative SET retired_at=CURRENT_TIMESTAMP WHERE alternative_id=2')
        self.assertEqual(self.save('2', record_components='yes', component_row_id='0', **self.component(kind='existing', target='2')).status_code, 400)

    def test_stale_token_not_refreshed_on_error_and_permissions_unchanged(self):
        old = hidden(self.client.get(self.path_url).text, 'edit_token')
        self.assertEqual(self.save('2').status_code, 200)
        response = self.save('1', edit_token=old)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(hidden(response.text, 'edit_token'), old)
        self.role = 'analyst'
        self.assertEqual(self.client.get(self.path_url).status_code, 404)
        self.assertEqual(self.save('1').status_code, 404)

    def relation(self, source, target, parameter='CM_1'):
        path = f'/alternativas/{source}/gestionar'
        result = self.client.post(path, data={'action':'preview_add_relation', 'target_id':target, 'parameter':parameter})
        if result.status_code != 200:
            return result
        return self.confirm_relation(path, result)

    def confirm_relation(self, path, preview):
        form = preview.text.split('value="confirm_relation"', 1)[1].split('</form>', 1)[0]
        data = {name: unescape(value) for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"', form)}
        data.update(action='confirm_relation', mode='automatic')
        return self.client.post(path, data=data)

    def test_relation_selectors_exclude_self_and_keep_controlled_parameters(self):
        page = self.client.get(self.path_url).text
        form = page.split('id="management-relation"',1)[1].split('</form>',1)[0]
        targets = form.split('name="target_id"',1)[1].split('</select>',1)[0]
        self.assertNotIn('value="1"', targets)
        self.assertIn('TEST-2a', targets)
        params = form.split('name="parameter"',1)[1].split('</select>',1)[0]
        self.assertEqual(re.findall(r'<option value="([^"]*)"', params), ['', *PHONOLOGICAL_PARAMETERS])
        for target, parameter in [('1','CM_1'), ('2',''), ('2','inventado')]:
            response = self.client.post(self.path_url, data={'action':'preview_add_relation','target_id':target,'parameter':parameter})
            self.assertEqual(response.status_code, 400)
        self.query("INSERT INTO concept(preferred_label) VALUES('OTHER')")
        self.query("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
        self.assertEqual(self.relation(1,3).status_code, 400)

    def test_multiple_relations_duplicate_inverse_and_retirement_history(self):
        self.query("INSERT INTO alternative(concept_id,working_label) VALUES(1,'3a')")
        self.assertEqual(self.relation(1,2).status_code, 200)
        self.assertEqual(self.relation(1,3,'CM_2').status_code, 200)
        self.assertEqual(len(self.query('SELECT * FROM alternative_relation WHERE is_current=1')), 2)
        self.assertEqual(self.relation(1,2).status_code, 400)
        self.assertEqual(self.relation(2,1).status_code, 400)
        page = self.client.get(self.path_url).text
        self.assertIn('CM_1', page)
        self.assertIn('CM_2', page)
        relation_id = self.query('SELECT alternative_relation_id FROM alternative_relation')[0][0]
        preview = self.client.post(self.path_url, data={'action':'preview_retire_relation','relation_id':relation_id})
        self.assertEqual(preview.status_code, 200)
        result = self.confirm_relation(self.path_url, preview)
        self.assertEqual(result.status_code, 200)
        self.assertIn('Historial de relaciones', result.text)
        self.assertEqual(len(self.query('SELECT * FROM alternative_relation WHERE is_current=1')), 1)
        self.assertEqual(len(self.query('SELECT * FROM alternative_relation')), 2)


if __name__ == '__main__':
    unittest.main()
