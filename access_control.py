import os
from functools import wraps

from flask import abort, g, request, render_template

from database import conectar


ROLES = ("analyst", "reviewer", "master")
CATALOG_ENDPOINTS = frozenset({
    "catalog.internal_catalog", "catalog.internal_concept", "catalog.internal_alternative",
    "catalog.external_catalog", "catalog.external_version", "catalog.external_concept",
    "catalog.external_version_concept", "catalog.external_alternative",
    "catalog.external_version_alternative",
})
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
            "static",
            "catalog.external_catalog", "catalog.external_version",
            "catalog.external_concept", "catalog.external_version_concept",
            "catalog.external_alternative", "catalog.external_version_alternative",
        }:
            return
        if role is None:
            abort(404)
        reviewer_endpoints = {
            "submissions.revisar_aportes",
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
        if request.endpoint in CATALOG_ENDPOINTS: return response
        db = conectar()
        try:
            collaborators = db.execute(
                "SELECT collaborator_id,display_name FROM collaborator "
                "WHERE active=1 ORDER BY display_name,collaborator_id"
            ).fetchall()
        finally: db.close()
        toolbar = render_template(
            "_internal_header.html",
            collaborators=collaborators,
            role=role,
            root=request.script_root,
            show_review=ROLE_LEVEL[role] >= ROLE_LEVEL["reviewer"],
            show_admin=role == "master",
        )
        body = response.get_data(as_text=True)
        body_start = body.find("<body")
        body_end = body.find(">", body_start) if body_start >= 0 else -1
        body = body[:body_end + 1] + toolbar + body[body_end + 1:] if body_end >= 0 else toolbar + body
        response.set_data(body)
        return response


def _escape(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
