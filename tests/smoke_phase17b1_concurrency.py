"""Exercise real baseline data exclusively in a disposable copy."""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from flask import Flask, g

import database
from concept_labels import alternative_display_label, human_concept_label
from source_period import format_source_period
from routes.occurrences import occurrences_bp
from routes.alternatives import alternatives_bp
from tests.form_client import hidden


def run():
    root = Path(__file__).resolve().parents[1]
    baseline = root / 'import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase17a_2026-09-05.db'
    old = database.BASE_DATOS
    with tempfile.TemporaryDirectory(prefix='phase17b1-') as temp:
        path = Path(temp) / 'smoke.db'
        shutil.copy2(baseline, path)
        database.BASE_DATOS = path
        db = database.conectar()
        try:
            app = Flask(__name__, template_folder=str(root / 'templates'))
            app.config.update(TESTING=True, SECRET_KEY='disposable-smoke')
            app.jinja_env.filters.update(alternative_display_label=alternative_display_label,
                                         human_concept_label=human_concept_label, source_period=format_source_period)
            app.register_blueprint(occurrences_bp)
            app.register_blueprint(alternatives_bp)
            @app.before_request
            def role():
                g.current_access_role = 'reviewer'
            client = app.test_client()
            occurrence = dict(db.execute('SELECT * FROM occurrence WHERE occurrence_id=1').fetchone())
            page = client.get('/ocurrencias/1/editar').get_data(as_text=True)
            data = {key: '' if value is None else str(value) for key, value in occurrence.items()}
            data.update(edit_token=hidden(page, 'edit_token'), provenance_note='Phase17B1 disposable smoke')
            first = client.post('/ocurrencias/1/actualizar', data=data)
            assert first.status_code == 302, first.get_data(as_text=True)
            before = list(db.iterdump())
            second = client.post('/ocurrencias/1/actualizar', data=data | dict(original_gloss='STALE'))
            assert second.status_code == 409
            assert before == list(db.iterdump())

            # Use the first real Alternative and its real current assignments.
            aid = db.execute('SELECT min(alternative_id) FROM alternative WHERE retired_at IS NULL').fetchone()[0]
            assignments = db.execute('SELECT occurrence_id FROM assignment WHERE alternative_id=? AND is_current=1', (aid,)).fetchall()
            spec = {f'occurrence_{r[0]}': 'unassigned' for r in assignments}
            route = f'/alternativas/{aid}/gestionar'
            def preview():
                before = list(db.iterdump())
                response = client.post(route, data=spec | dict(action='preview_retire'))
                assert response.status_code == 200, response.get_data(as_text=True)
                assert before == list(db.iterdump())
                token = hidden(response.get_data(as_text=True), 'preview_token')
                assert token, response.get_data(as_text=True)
                return spec | dict(action='confirm_retire', confirm='yes', preview_token=token, reason='Disposable smoke')
            confirmation = preview()
            db.execute('UPDATE occurrence SET original_gloss=original_gloss || ? WHERE occurrence_id=?', (' smoke change', assignments[0][0]))
            db.commit()
            before = list(db.iterdump())
            stale = client.post(route, data=confirmation)
            assert stale.status_code == 409, stale.get_data(as_text=True)
            assert before == list(db.iterdump())
            fresh = client.post(route, data=preview())
            assert fresh.status_code == 302, fresh.get_data(as_text=True)
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
            fk = len(db.execute('PRAGMA foreign_key_check').fetchall())
            assert integrity == 'ok' and fk == 0
            return dict(occurrence_id=1, alternative_id=aid, occurrence_statuses=[first.status_code, second.status_code],
                        alternative_statuses=[stale.status_code, fresh.status_code], no_partial_writes=True,
                        integrity_check=integrity, foreign_key_check=fk, disposable_copy=True)
        finally:
            db.close()
            database.BASE_DATOS = old


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
