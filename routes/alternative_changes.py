import json
import secrets
import sqlite3
from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from database import conectar
from access_control import requires_analyst, requires_reviewer
from alternative_change_workflow import create_proposal, get_proposal, review_proposal, baseline, pending_changes
from alternative_admin import relation_preview
from edit_concurrency import sign, unsign
from phonological_parameters import PHONOLOGICAL_PARAMETERS
from routes.alternatives import _components_from_form
from source_details import occurrence_presentation

alternative_changes_bp = Blueprint('alternative_changes', __name__)


def _csrf():
    if 'alternative_change_csrf' not in session:
        session['alternative_change_csrf'] = secrets.token_urlsafe(32)
    if request.method == 'POST' and not secrets.compare_digest(session['alternative_change_csrf'], request.form.get('csrf_token', '')):
        abort(400)
    return session['alternative_change_csrf']


def proposal_rows(db, pending=False):
    return db.execute("""SELECT s.*,p.change_kind,p.concept_label_snapshot,p.alternative_label_snapshot
        FROM submission s JOIN alternative_change_submission p USING(submission_id)
        """ + (" WHERE s.status='pending'" if pending else '') + ' ORDER BY s.submission_id DESC').fetchall()


def _context(db, aid):
    target = db.execute('''SELECT a.*,c.preferred_label FROM alternative a JOIN concept c USING(concept_id)
                           WHERE a.alternative_id=?''', (aid,)).fetchone()
    if not target:
        abort(404)
    morphology = db.execute('SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1',(aid,)).fetchone()
    components = db.execute('''SELECT c.* FROM alternative_component c JOIN alternative_morphology m USING(alternative_morphology_id)
        WHERE m.alternative_id=? AND m.is_current=1 ORDER BY position''',(aid,)).fetchall()
    relations = db.execute('''SELECT r.*,lo.working_label AS low_label,hi.working_label AS high_label
        FROM alternative_relation r JOIN alternative lo ON lo.alternative_id=r.alternative_low_id
        JOIN alternative hi ON hi.alternative_id=r.alternative_high_id
        WHERE r.is_current=1 AND (r.alternative_low_id=? OR r.alternative_high_id=?)''',(aid,aid)).fetchall()
    evidence = db.execute('''SELECT o.*,s.source_name,s.legacy_source_code,s.source_type
        FROM assignment a JOIN occurrence o USING(occurrence_id) JOIN source s USING(source_id)
        WHERE a.alternative_id=? AND a.is_current=1 ORDER BY o.occurrence_id''',(aid,)).fetchall()
    options = db.execute('SELECT alternative_id,working_label FROM alternative WHERE concept_id=? AND retired_at IS NULL AND alternative_id!=? ORDER BY alternative_id',(target['concept_id'],aid)).fetchall()
    return dict(alternative=target,morphology=morphology,components=components,relations=relations,evidence=[occurrence_presentation(o) for o in evidence],options=options,parameters=PHONOLOGICAL_PARAMETERS)


@alternative_changes_bp.route('/alternativas/<int:alternative_id>/proponer', methods=['GET','POST'])
@requires_analyst
def propose(alternative_id):
    mode = request.args.get('mode')
    if mode not in (None, 'morphology', 'relation'):
        abort(404)
    csrf = _csrf(); db = conectar()
    try:
        context = _context(db,alternative_id)
        if context['alternative']['retired_at']:
            abort(404)
        error = None
        if request.method == 'POST':
            try:
                kind = request.form.get('kind')
                if kind != {'morphology':'MORPHOLOGY','relation':'RELATION'}.get(mode):
                    raise ValueError('Seleccione la tarea correspondiente antes de enviar la propuesta.')
                state = unsign(request.form.get('state_token'))
                if state.get('aid') != alternative_id or state.get('kind') != kind:
                    raise ValueError('La alternativa cambió; recargue la página antes de proponer.')
                if kind == 'MORPHOLOGY':
                    count = request.form.get('component_count','').strip()
                    values = dict(component_count=None if count=='N/A' else count,
                        component_count_not_applicable=count=='N/A',free_permutation=request.form.get('free_permutation'),
                        note=request.form.get('morphology_note'),components=_components_from_form(request.form))
                else:
                    values = dict(target_id=request.form.get('target_id'),parameter=request.form.get('parameter'))
                sid = create_proposal(db,alternative_id,kind,values,collaborator_id=request.form.get('collaborator_id'),access_role=g.current_access_role,expected_baseline=state['baseline'])
                return redirect(url_for('alternative_changes.detail',submission_id=sid))
            except (ValueError,TypeError,sqlite3.IntegrityError) as exc:
                error = 'No fue posible guardar el aporte.' if isinstance(exc,sqlite3.IntegrityError) else str(exc)
        tokens = {kind:sign({'aid':alternative_id,'kind':kind,'baseline':baseline(db,alternative_id,kind)}) for kind in ('MORPHOLOGY','RELATION')}
        pending = pending_changes(db,[alternative_id])[alternative_id]
        return render_template('alternative_change_propose.html',**context,tokens=tokens,csrf_token=csrf,error=error,mode=mode,pending=pending), 400 if error else 200
    finally:
        db.close()


@alternative_changes_bp.get('/aportes/alternativas/<int:submission_id>')
@requires_analyst
def detail(submission_id):
    db=conectar()
    try:
        proposal=get_proposal(db,submission_id)
        if not proposal:abort(404)
        context=_context(db,proposal['alternative_id'])
        preview=None;warning=None
        if proposal['status']=='pending' and proposal['change_kind']=='RELATION':
            try:preview=relation_preview(db,proposal['alternative_id'],action='add',**json.loads(proposal['payload']))
            except (ValueError,TypeError) as exc:warning=str(exc)
        return render_template('alternative_change_detail.html',**context,proposal=proposal,
            proposed=json.loads(proposal['payload']),preview=preview,warning=warning,csrf_token=_csrf())
    finally:db.close()


@alternative_changes_bp.post('/aportes/alternativas/<int:submission_id>/decidir')
@requires_reviewer
def decide(submission_id):
    _csrf();db=conectar()
    try:
        review_proposal(db,submission_id,request.form.get('decision'),collaborator_id=request.form.get('collaborator_id'),
            access_role=g.current_access_role,note=request.form.get('review_note'))
        return redirect(url_for('alternative_changes.detail',submission_id=submission_id))
    except (ValueError,sqlite3.IntegrityError) as exc:
        return render_template('alternative_change_error.html',error='No fue posible aplicar el cambio.' if isinstance(exc,sqlite3.IntegrityError) else str(exc),submission_id=submission_id),400
    finally:db.close()
