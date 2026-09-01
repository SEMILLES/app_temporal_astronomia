import os
from functools import wraps

from flask import abort, g, request

from database import conectar


ROLES = ("analyst", "reviewer", "master")
ROLE_LEVEL = {role: index for index, role in enumerate(ROLES, 1)}
ENV_BY_ROLE = {
    "analyst": "LESICO_ANALYST_ROUTE",
    "reviewer": "LESICO_REVIEWER_ROUTE",
    "master": "LESICO_MASTER_ROUTE",
}


def configured_routes(environ=None):
    environ = os.environ if environ is None else environ
    result = {}
    for role, variable in ENV_BY_ROLE.items():
        value = (environ.get(variable) or "").strip().strip("/")
        if value:
            if "/" in value:
                raise RuntimeError(f"{variable} debe ser un solo segmento de ruta")
            if value in result:
                raise RuntimeError("Las rutas internas configuradas deben ser distintas")
            result[value] = role
    return result


class RolePrefixMiddleware:
    """Resolve the private prefix before Flask routing, without logging tokens."""

    def __init__(self, app, routes=None):
        self.app = app
        self.routes = routes

    def __call__(self, environ, start_response):
        routes = self.routes if self.routes is not None else configured_routes()
        path = environ.get("PATH_INFO", "")
        segment, separator, remainder = path.lstrip("/").partition("/")
        role = routes.get(segment)
        if role is None:
            environ["LESICO_ACCESS_ROLE"] = ""
        else:
            environ["LESICO_ACCESS_ROLE"] = role
            prefix = "/" + segment
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + prefix
            environ["PATH_INFO"] = "/" + remainder if separator else "/"
        return self.app(environ, start_response)


def current_access_role():
    return getattr(g, "current_access_role", None)


def require_role(minimum):
    def decorator(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            role = current_access_role()
            if role is None or ROLE_LEVEL[role] < ROLE_LEVEL[minimum]:
                abort(404)
            return function(*args, **kwargs)
        return guarded
    return decorator


requires_analyst = require_role("analyst")
requires_reviewer = require_role("reviewer")
requires_master = require_role("master")


def install_access_context(app):
    app.wsgi_app = RolePrefixMiddleware(app.wsgi_app)

    @app.before_request
    def load_access_role():
        role = request.environ.get("LESICO_ACCESS_ROLE") or None
        g.current_access_role = role
        if request.endpoint in {
            "catalog.external_catalog", "catalog.external_version",
            "catalog.external_concept", "catalog.external_version_concept",
            "catalog.external_alternative", "catalog.external_version_alternative",
        }:
            return
        if role is None:
            abort(404)
        reviewer_endpoints = {
            "submissions.revisar_aportes", "submissions.detalle_aporte",
            "submissions.decidir_aporte",
            "conflicts.conflicts_list", "conflicts.new_conflict",
            "conflicts.conflict_detail", "conflicts.resolve_conflict",
            "conflicts.validate_conflicts",
        }
        master_endpoints = {
            "collaborators.collaborators", "collaborators.create_collaborator",
            "collaborators.rename_collaborator",
            "catalog.publication_update", "catalog.publish_catalog_route",
            "catalog.publications",
        }
        required = "master" if request.endpoint in master_endpoints else (
            "reviewer" if request.endpoint in reviewer_endpoints else "analyst"
        )
        if ROLE_LEVEL[role] < ROLE_LEVEL[required]:
            abort(404)

    @app.after_request
    def inject_internal_navigation(response):
        if response.status_code >= 400 or not response.content_type.startswith("text/html"):
            return response
        role = getattr(g, "current_access_role", None)
        if role is None: return response
        db = conectar()
        try:
            collaborators = db.execute(
                "SELECT collaborator_id,display_name FROM collaborator "
                "WHERE active=1 ORDER BY display_name,collaborator_id"
            ).fetchall()
        finally: db.close()
        options = "".join(
            f'<option value="{row[0]}">{_escape(row[1])}</option>' for row in collaborators
        )
        root = request.script_root
        review = (f'<section><strong>REVISIÓN</strong> '
                  f'<a href="{root}/aportes/pendientes">Aportes pendientes</a> '
                  f'<a href="{root}/conflictos">Conflictos</a></section>') \
                 if ROLE_LEVEL[role] >= ROLE_LEVEL["reviewer"] else ""
        admin = (f'<section><strong>ADMINISTRACIÓN</strong> '
                 f'<a href="{root}/colaboradores">Colaboradores</a> '
                 f'<a href="{root}/actualizar-catalogo">Actualizar catálogo</a> '
                 f'<a href="{root}/publicaciones">Publicaciones</a></section>') \
                if role == "master" else ""
        toolbar = f'''<aside id="lesico-internal-context" data-access-role="{role}">
<label>Trabajando como: <select id="lesico-collaborator"><option value="">Sin identificar</option>{options}</select></label>
<nav><section><strong>ANÁLISIS</strong> <a href="{root}/trabajo">Inicio</a> <a href="{root}/ocurrencias">Ocurrencias</a> <a href="{root}/borradores">Borradores</a> <a href="{root}/conceptos">Conceptos / Alternativas</a> <a href="{root}/aportes">Aportes</a></section><section><strong>CATÁLOGO</strong> <a href="{root}/catalogo-interno">Catálogo interno</a></section>{review}{admin}</nav></aside>
<script>document.addEventListener('DOMContentLoaded',function(){{const key='lesico-collaborator-id';const s=document.getElementById('lesico-collaborator');const saved=localStorage.getItem(key)||'';if([...s.options].some(o=>o.value===saved))s.value=saved;else localStorage.removeItem(key);s.addEventListener('change',()=>localStorage.setItem(key,s.value));document.querySelectorAll('form[method="post"],form[method="POST"]').forEach(f=>{{let i=f.querySelector('input[name="collaborator_id"]');if(!i){{i=document.createElement('input');i.type='hidden';i.name='collaborator_id';f.appendChild(i)}}f.addEventListener('submit',()=>i.value=s.value);i.value=s.value}})}});</script>'''
        body = response.get_data(as_text=True)
        marker = "<body>"
        body = body.replace(marker, marker + toolbar, 1) if marker in body else toolbar + body
        response.set_data(body)
        return response


def _escape(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
