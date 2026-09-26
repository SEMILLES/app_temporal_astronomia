import json
import re
import secrets
import sqlite3
from itertools import zip_longest
from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from database import conectar
from access_control import requires_analyst, requires_reviewer
from alternative_change_workflow import create_proposal, create_relation_proposals, get_proposal, review_proposal, baseline, pending_changes
from alternative_change_workflow import relation_review_preview, relation_review_history
from edit_concurrency import sign, unsign
from phonological_parameters import PHONOLOGICAL_PARAMETERS
from routes.alternatives import _components_from_form
from source_details import occurrence_presentation
from concept_labels import alternative_display_label

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


def _morphology_form_context(db, context, form=None):
    """Keep raw form values separate from canonical state and stored proposals."""
    options = [dict(row) for row in db.execute('''
        SELECT a.alternative_id,a.working_label,c.preferred_label
        FROM alternative a JOIN concept c USING(concept_id)
        WHERE a.retired_at IS NULL AND a.alternative_id!=?
        ORDER BY c.preferred_label,a.working_label,a.alternative_id
    ''', (context['alternative']['alternative_id'],)).fetchall()]
    for option in options:
        option['display_label'] = alternative_display_label(
            option['preferred_label'], option['working_label']) or option['preferred_label'] + ' · Sin etiqueta'
    if form is not None:
        values = form.to_dict()
        indexes = sorted({int(match.group(1)) for key in form
                          if (match := re.fullmatch(r'component_(\d+)_position', key))})
        rows = [dict(index=i, position=form.get(f'component_{i}_position', ''),
                     component_alternative_id=form.get(f'component_{i}_alternative_id', ''),
                     component_label=form.get(f'component_{i}_label', ''),
                     note=form.get(f'component_{i}_note', ''),
                     kind=form.get(f'ui_component_type_{i}', '')) for i in indexes]
    else:
        morphology = context['morphology']
        values = dict(component_count=('N/A' if morphology['component_count_not_applicable']
                      else morphology['component_count'] or '') if morphology else '',
                      free_permutation=morphology['free_permutation'] if morphology else 'SIN INFORMACIÓN',
                      morphology_note=morphology['note'] or '' if morphology else '')
        rows = [dict(row, index=i) for i, row in enumerate(map(dict, context['components']), 1)]
    for row in rows:
        row['component_alternative_id'] = str(row['component_alternative_id'] or '')
        row['kind'] = row.get('kind') or ('existing' if row['component_alternative_id'] else
                                        'unapproved' if row['component_label'] or row['note'] else '')
    values.setdefault('ui_identified', 'yes' if rows else 'no')
    return dict(morphology_values=values, component_form_rows=rows,
                component_options=options, component_option_ids=[str(o['alternative_id']) for o in options])


def _relation_form_context(db, context, form=None):
    alternatives = {row['alternative_id']: dict(row, occurrences=[]) for row in map(dict, context['options'])}
    for item in alternatives.values():
        item['display_label'] = alternative_display_label(
            context['alternative']['preferred_label'], item['working_label']) or 'Sin etiqueta'
    rows = db.execute('''SELECT a.alternative_id,o.*,s.source_name,s.legacy_source_code,s.source_type
        FROM assignment a JOIN occurrence o USING(occurrence_id) JOIN source s USING(source_id)
        JOIN alternative alt ON alt.alternative_id=a.alternative_id
        WHERE alt.concept_id=? AND alt.retired_at IS NULL AND alt.alternative_id!=? AND a.is_current=1
        ORDER BY a.alternative_id,o.occurrence_id''',
        (context['alternative']['concept_id'], context['alternative']['alternative_id'])).fetchall()
    for row in rows:
        alternatives[row['alternative_id']]['occurrences'].append(occurrence_presentation(row))
    submitted = list(zip_longest(form.getlist('target_id'), form.getlist('parameter'), fillvalue='')) if form is not None else [('', '')]
    return dict(relation_options=list(alternatives.values()),
                relation_option_ids=[str(aid) for aid in alternatives],
                relation_rows=submitted or [('', '')],
                relation_answer=form.get('relation_answer', '') if form is not None else '',
                relation_needs_review=bool(re.search(r'[b-z]$', context['alternative']['working_label'] or '')) and not context['relations'],
                relation_collaborator=form.get('collaborator_id', '') if form is not None else '')


@alternative_changes_bp.route('/alternativas/<int:alternative_id>/proponer', methods=['GET','POST'])
@requires_analyst
def propose(alternative_id):
    mode = request.args.get('mode')
    if mode not in (None, 'morphology', 'relation'):
        abort(404)
    csrf = _csrf(); db = conectar()
    try:
        if request.method=='GET':
            db.execute('BEGIN')
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
                    components = _components_from_form(request.form)
                    # UI choices are optional for already-open forms; never persist them.
                    if request.form.get('ui_identified') == 'no':
                        components = []
                    elif count != '1':
                        for key, component_kind in request.form.items():
                            match = re.fullmatch(r'ui_component_type_(\d+)', key)
                            if not match:
                                continue
                            prefix = f'component_{match.group(1)}_'
                            target = request.form.get(prefix + 'alternative_id', '').strip()
                            label = request.form.get(prefix + 'label', '').strip()
                            note = request.form.get(prefix + 'note', '').strip()
                            if component_kind == 'existing' and not target:
                                raise ValueError('Seleccione una alternativa vigente para el componente.')
                            if component_kind == 'unapproved' and (target or not (label or note)):
                                raise ValueError('El componente con dudas requiere una descripción o nota, sin alternativa vinculada.')
                            if component_kind not in ('existing', 'unapproved'):
                                raise ValueError('Seleccione el tipo de componente.')
                    values = dict(component_count=None if count=='N/A' else count,
                        component_count_not_applicable=count=='N/A',free_permutation=request.form.get('free_permutation'),
                        note=request.form.get('morphology_note'),components=components)
                else:
                    answer = request.form.get('relation_answer')
                    if answer == 'NO':
                        if 'target_id' in request.form or 'parameter' in request.form:
                            raise ValueError('La respuesta No no puede incluir relaciones.')
                        sid = create_proposal(db, alternative_id, 'RELATION', {'relation_answer': 'NO'},
                            collaborator_id=request.form.get('collaborator_id'), access_role=g.current_access_role,
                            expected_baseline=state['baseline'])
                        return redirect(url_for('alternative_changes.detail', submission_id=sid))
                    if answer != 'YES':
                        raise ValueError('Seleccione Sí o No antes de enviar.')
                    targets, parameters = request.form.getlist('target_id'), request.form.getlist('parameter')
                    if len(targets)!=len(parameters):
                        raise ValueError('Cada relación debe tener alternativa y parámetro.')
                    ids = create_relation_proposals(db,alternative_id,
                        [dict(target_id=t,parameter=p) for t,p in zip(targets,parameters)],
                        collaborator_id=request.form.get('collaborator_id'),access_role=g.current_access_role,expected_baseline=state['baseline'])
                    return redirect(url_for('alternative_changes.propose',alternative_id=alternative_id,mode='relation',sent=','.join(map(str,ids))))
                sid = create_proposal(db,alternative_id,kind,values,collaborator_id=request.form.get('collaborator_id'),access_role=g.current_access_role,expected_baseline=state['baseline'])
                return redirect(url_for('alternative_changes.detail',submission_id=sid))
            except (ValueError,TypeError,sqlite3.IntegrityError) as exc:
                error = 'No fue posible guardar el aporte.' if isinstance(exc,sqlite3.IntegrityError) else str(exc)
        tokens = {kind:sign({'aid':alternative_id,'kind':kind,'baseline':baseline(db,alternative_id,kind)}) for kind in ('MORPHOLOGY','RELATION')}
        pending = pending_changes(db,[alternative_id])[alternative_id]
        unavailable = [[r['alternative_high_id'] if r['alternative_low_id']==alternative_id else r['alternative_low_id'],r['phonological_parameter']] for r in context['relations']]
        unavailable += [[p['target_id'] if p['alternative_id']==alternative_id else p['alternative_id'],p['parameter']] for p in pending['RELATION'] if p['target_id'] is not None]
        if mode == 'morphology':
            context.update(_morphology_form_context(db, context, request.form if request.method == 'POST' else None))
        elif mode == 'relation':
            context.update(_relation_form_context(db, context, request.form if request.method == 'POST' else None))
        return render_template('alternative_change_propose.html',**context,tokens=tokens,csrf_token=csrf,error=error,mode=mode,pending=pending,unavailable=unavailable), 400 if error else 200
    finally:
        db.close()


@alternative_changes_bp.get('/aportes/alternativas/<int:submission_id>')
@requires_analyst
def detail(submission_id):
    db=conectar()
    try:
        db.execute('BEGIN')
        proposal=get_proposal(db,submission_id)
        if not proposal:abort(404)
        context=_context(db,proposal['alternative_id'])
        proposed=json.loads(proposal['payload'])
        previews={}; preview_errors={}
        if proposal['status']=='pending' and proposal['change_kind']=='RELATION':
            resolutions = ('NO_CONFIRMED',) if proposed.get('relation_answer') == 'NO' else ('ACCEPTED', 'REJECTED')
            for resolution in resolutions:
                try:
                    previews[resolution]=relation_review_preview(db,proposal['alternative_id'],proposed,resolution)
                except (ValueError,TypeError) as exc:
                    preview_errors[resolution]=str(exc)
        return render_template('alternative_change_detail.html',**context,proposal=proposal,
            proposed=proposed,previews=previews,preview_errors=preview_errors,
            relation_history=relation_review_history(db,submission_id) if proposal['change_kind']=='RELATION' else None,
            csrf_token=_csrf(),
            review_token=sign({'sid':submission_id,'baseline':baseline(db,proposal['alternative_id'],proposal['change_kind'])}) if proposal['status']=='pending' and not context['alternative']['retired_at'] else '')
    finally:db.close()


@alternative_changes_bp.post('/aportes/alternativas/<int:submission_id>/decidir')
@requires_reviewer
def decide(submission_id):
    _csrf();db=conectar()
    try:
        expected = None
        proposal = get_proposal(db,submission_id)
        decision = request.form.get('decision')
        resolution = request.form.get('relations_resolution')
        if proposal and proposal['change_kind']=='RELATION' and resolution == 'pending':
            decision = 'pending'
        if decision=='accepted' and proposal and proposal['change_kind']=='RELATION':
            if not resolution:
                raise ValueError('Seleccione explícitamente la resolución lingüística de la relación.')
            state = unsign(request.form.get('review_token'))
            if state.get('sid')!=submission_id:
                raise ValueError('Vuelva a abrir el aporte para revisar la decisión.')
            expected = state['baseline']
        review_proposal(db,submission_id,decision,collaborator_id=request.form.get('collaborator_id'),
            access_role=g.current_access_role,note=request.form.get('review_note'),expected_baseline=expected,
            relations_resolution=resolution)
        return redirect(url_for('alternative_changes.detail',submission_id=submission_id))
    except (ValueError,sqlite3.IntegrityError) as exc:
        return render_template('alternative_change_error.html',error='No fue posible aplicar el cambio.' if isinstance(exc,sqlite3.IntegrityError) else str(exc),submission_id=submission_id),400
    finally:db.close()
