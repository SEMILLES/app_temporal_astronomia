from tests.form_client import FormClient

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from database import crear_esquema
from routes.occurrences import occurrences_bp
from routes.submissions import submissions_bp
from concept_labels import alternative_display_label, human_concept_label
from source_period import format_source_period


ROOT = Path(__file__).resolve().parents[1]


class ImmediateAcceptanceRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "immediate.db"

        db = self.connect()
        crear_esquema(db)
        db.execute(
            """
            INSERT INTO source(
                source_name, start_year, end_year, end_year_status
            )
            VALUES('S', 2000, 2005, 'known')
            """
        )
        db.execute(
            "INSERT INTO concept(preferred_label) VALUES('C')"
        )
        db.execute(
            """
            INSERT INTO occurrence(
                source_id, original_gloss, occurrence_year
            )
            VALUES(1, 'TEST', 2001)
            """
        )
        db.execute(
            """
            INSERT INTO occurrence_concept_reference(
                occurrence_id, concept_id
            )
            VALUES(1, 1)
            """
        )
        db.execute(
            """
            INSERT INTO alternative(concept_id, working_label)
            VALUES(1, '1')
            """
        )
        db.execute(
            "INSERT INTO collaborator(display_name) VALUES('Persona')"
        )
        db.commit()
        db.close()

        self.app = Flask(
            __name__,
            template_folder=str(ROOT / "templates"),
        )
        self.app.testing = True
        self.app.jinja_env.filters.update(
            alternative_display_label=alternative_display_label,
            human_concept_label=human_concept_label,
            source_period=format_source_period,
        )
        self.app.register_blueprint(occurrences_bp)
        self.app.register_blueprint(submissions_bp)

        @self.app.before_request
        def role():
            g.current_access_role = self.role

        self.client = FormClient(self.app.test_client())
        self.patches = [
            patch(
                "routes.occurrences.conectar",
                side_effect=self.connect,
            ),
            patch(
                "routes.submissions.conectar",
                side_effect=self.connect,
            ),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def test_roles_buttons_and_crafted_post(self):
        self.role = "analyst"

        html = self.client.get(
            "/ocurrencias/1/gramatica"
        ).get_data(as_text=True)

        self.assertIn("Mandar a revisión", html)
        self.assertNotIn("Aceptar inmediatamente", html)

        self.assertEqual(
            404,
            self.client.post(
                "/ocurrencias/1/gramatica/aceptacion-inmediata/preview",
                data={
                    "gender": "FEM-A",
                    "access_role": "master",
                },
            ).status_code,
        )

        for role in ("reviewer", "master"):
            self.role = role
            html = self.client.get(
                "/ocurrencias/1/gramatica"
            ).get_data(as_text=True)

            self.assertIn("Aceptar inmediatamente", html)
            self.assertLess(
                html.index("Mandar a revisión"),
                html.index("Aceptar inmediatamente"),
            )

    def test_grammar_preview_abandon_and_confirm_prg(self):
        self.role = "reviewer"

        data = {
            "gender": "FEM-A",
            "collaborator_id": "1",
        }

        preview = self.client.post(
            "/ocurrencias/1/gramatica/aceptacion-inmediata/preview",
            data=data,
        )

        self.assertEqual(200, preview.status_code)
        self.assertIn(
            "Aceptar inmediatamente este análisis",
            preview.get_data(as_text=True),
        )

        db = self.connect()
        self.assertEqual(
            (0, 0, 0),
            tuple(
                db.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "submission",
                    "occurrence_grammar",
                    "activity_event",
                )
            ),
        )
        db.close()

        confirmed = dict(
            data,
            confirm_immediate="yes",
        )

        response = self.client.post(
            "/ocurrencias/1/gramatica/aceptacion-inmediata/confirmar",
            data=confirmed,
        )

        self.assertEqual(302, response.status_code)

        db = self.connect()

        self.assertEqual(
            ("resolved", "accepted"),
            tuple(
                db.execute(
                    "SELECT status, resolution FROM submission"
                ).fetchone()
            ),
        )

        self.assertEqual(
            1,
            db.execute(
                """
                SELECT created_from_submission_id
                FROM occurrence_grammar
                """
            ).fetchone()[0],
        )

        self.assertEqual(
            (1, "Persona", "reviewer"),
            tuple(
                db.execute(
                    """
                    SELECT
                        collaborator_id,
                        collaborator_name_snapshot,
                        access_role
                    FROM activity_event
                    WHERE event_type='grammar_submission_accepted'
                    """
                ).fetchone()
            ),
        )

        db.close()

    def test_grammar_immediate_rejects_unresolved_uncertainty(self):
        self.role = "reviewer"

        data = {
            "gender": "FEM-A",
            "gender_uncertain": "on",
            "note": "Existe una duda sobre el género.",
            "collaborator_id": "1",
        }

        response = self.client.post(
            "/ocurrencias/1/gramatica/aceptacion-inmediata/preview",
            data=data,
        )

        self.assertEqual(400, response.status_code)
        self.assertIn(
            "resolver todas las dudas",
            response.get_data(as_text=True),
        )

        db = self.connect()

        self.assertEqual(
            (0, 0),
            tuple(
                db.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "submission",
                    "occurrence_grammar",
                )
            ),
        )

        db.close()

    def test_existing_alternative_and_concept_immediate(self):
        self.role = "master"

        data = {
            "proposal_kind": "EXISTING",
            "proposed_existing_alternative_id": "1",
            "canonical_decision": "existing",
            "canonical_alternative_id": "1",
            "collaborator_id": "1",
        }

        self.assertEqual(
            200,
            self.client.post(
                "/ocurrencias/1/clasificar/aceptacion-inmediata/preview",
                data=data,
            ).status_code,
        )

        db = self.connect()
        self.assertEqual(
            0,
            db.execute(
                "SELECT count(*) FROM submission"
            ).fetchone()[0],
        )
        db.close()

        self.assertEqual(
            302,
            self.client.post(
                "/ocurrencias/1/clasificar/aceptacion-inmediata/confirmar",
                data=dict(
                    data,
                    confirm_immediate="yes",
                ),
            ).status_code,
        )

        concept = {
            "source_id": "1",
            "original_gloss": "NUEVO",
            "occurrence_year": "2002",
            "reference_kind": "new",
            "proposed_label": "CONCEPTO-NUEVO",
            "concept_immediate_action": "new",
            "collaborator_id": "1",
        }

        self.assertEqual(
            200,
            self.client.post(
                "/aportes/concepto/aceptacion-inmediata/preview",
                data=concept,
            ).status_code,
        )

        response = self.client.post(
            "/aportes/concepto/aceptacion-inmediata/confirmar",
            data=dict(
                concept,
                confirm_immediate="yes",
            ),
        )

        self.assertEqual(302, response.status_code)

        db = self.connect()

        self.assertEqual(
            ("resolved", "CONCEPTO-NUEVO"),
            tuple(
                db.execute(
                    """
                    SELECT cp.status, c.preferred_label
                    FROM concept_proposal cp
                    JOIN concept c
                      ON c.concept_id=cp.resolved_concept_id
                    """
                ).fetchone()
            ),
        )

        self.assertEqual(
            0,
            db.execute(
                """
                SELECT count(*)
                FROM assignment
                WHERE occurrence_id=2
                """
            ).fetchone()[0],
        )

        db.close()


    def test_derived_lexical_groups_preview_and_confirm(self):
        self.role = 'reviewer'
        data = {'proposal_kind': 'NEW', 'phonological_relation_answer': 'YES',
                'relation_target_type': 'alternative', 'relation_target_id': '1',
                'relation_parameter': 'CM_1', 'morphology_component_count': 'N/A',
                'canonical_decision': 'new', 'collaborator_id': '1',
                'review_note': 'Reviewed', 'confirm_immediate': 'yes'}
        base = '/ocurrencias/1/clasificar/aceptacion-inmediata/'
        self.assertEqual(200, self.client.post(base + 'preview', data=data).status_code)
        db = self.connect()
        self.assertEqual(0, db.execute('SELECT count(*) FROM submission').fetchone()[0])
        db.close()
        self.assertEqual(302, self.client.post(base + 'confirmar', data=data).status_code)
        db = self.connect()
        self.assertEqual(('CREATE_NEW', 'ACCEPTED', 'ACCEPTED', 'CREATED'), tuple(db.execute(
            'SELECT decision_action,relations_resolution,morphology_resolution,assignment_effect FROM submission_lexical_decision').fetchone()))
        db.close()

    def test_new_ignores_obsolete_existing_override_without_note(self):
        self.role = 'master'
        data = {'proposal_kind': 'NEW', 'phonological_relation_answer': 'NO',
                'morphology_component_count': 'N/A', 'canonical_decision': 'existing',
                'canonical_alternative_id': '1', 'collaborator_id': '1',
                'morphology_resolution': 'REJECTED', 'confirm_immediate': 'yes'}
        url = '/ocurrencias/1/clasificar/aceptacion-inmediata/confirmar'
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        db = self.connect()
        self.assertEqual(('CREATE_NEW', 'NOT_PROPOSED', 'ACCEPTED'), tuple(db.execute(
            'SELECT decision_action,relations_resolution,morphology_resolution FROM submission_lexical_decision').fetchone()))
        self.assertEqual(1, db.execute('SELECT count(*) FROM alternative_morphology').fetchone()[0])
        db.close()

    def test_classification_ui_has_no_duplicate_decisions(self):
        self.role = 'reviewer'
        html = self.client.get('/ocurrencias/1/clasificar').get_data(as_text=True)
        self.assertNotIn('name="relations_resolution" value="ACCEPTED"', html)
        self.assertNotIn('name="relations_resolution" value="REJECTED"', html)
        self.assertNotIn('name="morphology_resolution" value="ACCEPTED"', html)
        self.assertNotIn('name="morphology_resolution" value="REJECTED"', html)
        self.assertNotIn('name="approve_relations"', html)
        self.assertNotIn('name="approve_morphology"', html)
        self.assertIn('Nota de revisión', html)
        self.assertNotIn('Modificar antes de aceptar', html)

    def test_create_new_summary_shows_explicit_group_resolutions(self):
        self.role = 'reviewer'
        data = {
            'proposal_kind': 'NEW', 'phonological_relation_answer': 'YES',
            'relation_target_type': 'alternative', 'relation_target_id': '1',
            'relation_parameter': 'CM_1', 'morphology_component_count': 'N/A',
            'canonical_decision': 'new', 'canonical_alternative_id': '',
            'relations_resolution': 'ACCEPTED',
            'morphology_resolution': 'REJECTED',
            'collaborator_id': '1', 'review_note': 'Decisión documentada',
        }
        response = self.client.post(
            '/ocurrencias/1/clasificar/aceptacion-inmediata/preview',
            data=data,
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('Concepto</dt>', html)
        self.assertIn('Crear una alternativa nueva', html)
        self.assertIn('Relaciones</dt><dd>Aceptadas', html)
        self.assertIn('Morfología</dt><dd>Aceptada', html)
        self.assertIn('Decisión documentada', html)

    def test_nomenclature_preview_matches_confirmation_and_rolls_back(self):
        import re
        self.role = 'reviewer'
        base = '/ocurrencias/1/clasificar/aceptacion-inmediata/'
        common = dict(proposal_kind='NEW', canonical_decision='new',
                      morphology_component_count='N/A', morphology_resolution='REJECTED',
                      collaborator_id='1', review_note='Decision documentada')
        from contextlib import closing
        with closing(self.connect()) as db:
            before = list(db.iterdump())
            previews = {}
            tables = {}
            for resolution in ('ACCEPTED', 'REJECTED', 'NOT_PROPOSED'):
                data = dict(common, phonological_relation_answer='NO')
                if resolution != 'NOT_PROPOSED':
                    data.update(phonological_relation_answer='YES', relation_target_type='alternative',
                                relation_target_id='1', relation_parameter='CM_1', relations_resolution=resolution)
                response = self.client.post(base+'preview', data=data)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn('Vista previa de solo lectura', html)
                tables[resolution] = html.split('<table>')[1].split('</table>')[0]
                previews[resolution] = re.search(r'class="preview-nueva".*?<td>Nueva</td><td>.*?</td><td>(.*?)</td>', html).group(1)
                self.assertEqual(before, list(db.iterdump()))
            self.assertEqual(tables['ACCEPTED'], tables['REJECTED'])
            self.assertNotEqual(tables['ACCEPTED'], tables['NOT_PROPOSED'])
            self.assertEqual(302, self.client.post(base+'confirmar', data=dict(data, confirm_immediate='yes')).status_code)
            label = db.execute('SELECT alternative_label_snapshot FROM submission_lexical_decision').fetchone()[0]
            self.assertEqual(previews['NOT_PROPOSED'], label)

    def test_existing_summary_has_full_destination(self):
        self.role = 'reviewer'
        response = self.client.post('/ocurrencias/1/clasificar/aceptacion-inmediata/preview', data={
            'proposal_kind':'EXISTING', 'proposed_existing_alternative_id':'1',
            'canonical_decision':'existing', 'collaborator_id':'1'})
        self.assertEqual(200, response.status_code)
        self.assertIn('Alternativa destino</dt><dd>C-1</dd>', response.get_data(as_text=True))

    def test_new_accepts_proposed_groups_despite_obsolete_post_fields(self):
        self.role = 'reviewer'
        data = {
            'proposal_kind': 'NEW', 'phonological_relation_answer': 'YES',
            'relation_target_type': 'alternative', 'relation_target_id': '1',
            'relation_parameter': 'CM_1', 'morphology_component_count': 'N/A',
            'canonical_decision': 'existing', 'canonical_alternative_id': '1',
            'relations_resolution': 'REJECTED',
            'morphology_resolution': 'REJECTED', 'collaborator_id': '1',
            'confirm_immediate': 'yes',
        }
        url = '/ocurrencias/1/clasificar/aceptacion-inmediata/confirmar'
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        db = self.connect()
        self.assertEqual(
            ('CREATE_NEW', 'ACCEPTED', 'ACCEPTED'),
            tuple(db.execute(
                'SELECT decision_action,relations_resolution,morphology_resolution '
                'FROM submission_lexical_decision'
            ).fetchone()),
        )
        db.close()


if __name__ == "__main__":
    unittest.main()