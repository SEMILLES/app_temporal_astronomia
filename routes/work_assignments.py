import secrets
from contextlib import closing

from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for

from access_control import require_exact_role, requires_reviewer
from database import conectar
from work_assignments import assign, list_concepts, my_work, remove

work_assignments_bp = Blueprint('work_assignments', __name__)


@work_assignments_bp.get('/mi-trabajo')
@require_exact_role('analyst')
def personal_work():
    search = request.args.get('search', '')
    with closing(conectar()) as db:
        try:
            data = my_work(db, request.args.get('collaborator_id'), search=search,
                           page=request.args.get('page', '1'))
        except (ValueError, TypeError):
            abort(400, description='Página no válida.')
    return render_template('mi_trabajo.html', **data, search=search)


def _filters(values):
    return dict(search=values.get('search', ''), status=values.get('status', 'all'),
                concept_id=values.get('concept_id', ''), work_type=values.get('work_type', ''),
                analyst_id=values.get('analyst_id', ''), page=values.get('page', '1'))


@work_assignments_bp.route('/administracion/asignaciones', methods=['GET', 'POST'])
@requires_reviewer
def administration():
    filters = _filters(request.args if request.method == 'GET' else request.form)
    error = None
    with closing(conectar()) as db:
        if request.method == 'POST':
            token = session.get('work_assignment_csrf')
            if not token or not secrets.compare_digest(token, request.form.get('csrf_token', '')):
                return render_template('asignacion_trabajo_error.html',
                                       message='La sesión del formulario venció. Recargue la página.'), 400
            try:
                # Validate navigation before committing a mutation.
                list_concepts(db, pending_only=True, **filters)
                if request.form.get('action') == 'assign':
                    changed = assign(db, request.form.getlist('concept_ids'), request.form.getlist('analyst_ids'),
                                     actor_id=request.form.get('collaborator_id'), access_role=g.current_access_role)
                    session['work_assignment_notice'] = f'Asignaciones nuevas: {changed}.'
                elif request.form.get('action') == 'remove':
                    identifier = int(request.form.get('assignment_id', ''))
                    remove(db, identifier, actor_id=request.form.get('collaborator_id'), access_role=g.current_access_role)
                    session['work_assignment_notice'] = 'Asignación retirada; el historial se conserva.'
                else:
                    raise ValueError('Operación no válida.')
                return redirect(url_for('.administration', **filters))
            except (ValueError, TypeError):
                error = 'No se guardaron cambios. Revise la selección de conceptos y colaboradores activos.'
        try:
            data = list_concepts(db, pending_only=True, **filters)
        except (ValueError, TypeError):
            return render_template('asignacion_trabajo_error.html', message='Filtros no válidos.'), 400
        analysts = db.execute('SELECT collaborator_id,display_name,active FROM collaborator ORDER BY display_name,collaborator_id').fetchall()
    if 'work_assignment_csrf' not in session:
        session['work_assignment_csrf'] = secrets.token_urlsafe(32)
    return render_template('asignacion_trabajo.html', **data, filters=filters, analysts=analysts,
                           csrf_token=session['work_assignment_csrf'], error=error,
                           notice=session.pop('work_assignment_notice', None)), 400 if error else 200
