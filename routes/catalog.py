import json

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from access_control import requires_master
from catalog_diff import summary_totals
from catalog_presentation import (relation_edges, variation_groups,
                                  variation_network_groups)
from catalog_publication import (IdenticalPublication, PublicationBlocked,
                                 PublicationError, publication_preview,
                                 publish_catalog)
from catalog_projection import build_catalog_projection
from database import conectar
from conflict_presentation import local_timestamp

catalog_bp = Blueprint("catalog", __name__)


def _presentation(concept):
    return {"variation_groups": variation_groups(concept) if concept else [],
            "relation_edges": relation_edges(concept) if concept else [],
            "variation_network_groups": variation_network_groups(concept) if concept else []}


def _catalog_counts(projection):
    alternatives = [a for c in projection["concepts"] for a in c["alternatives"]]
    return {"concepts": len(projection["concepts"]), "alternatives": len(alternatives),
            "occurrences": sum(len(a["occurrences"]) for a in alternatives)}


def _matches(concept, query):
    if not query:
        return True
    terms = [
        concept["preferred_label"],
        *(alternative["name"] for alternative in concept["alternatives"]),
        *(
            occurrence["original_gloss"] or ""
            for alternative in concept["alternatives"]
            for occurrence in alternative["occurrences"]
        ),
    ]
    return any(query in value.casefold() for value in terms)


def _load():
    db = conectar()
    try:
        projection = build_catalog_projection(db)
        blocking = db.execute(
            "SELECT count(*) FROM conflict "
            "WHERE status='open' AND severity='blocking'"
        ).fetchone()[0]
        non_blocking = db.execute(
            "SELECT count(*) FROM conflict "
            "WHERE status='open' AND severity='non_blocking'"
        ).fetchone()[0]
    finally:
        db.close()
    return projection, blocking, non_blocking


def _banner_context(blocking, non_blocking):
    return {
        "blocking_conflicts": blocking,
        "non_blocking_conflicts": non_blocking,
        "can_open_conflicts": getattr(g, "current_access_role", None)
        in ("reviewer", "master"),
    }


@catalog_bp.get("/catalogo-interno")
def internal_catalog():
    projection, blocking, non_blocking = _load()
    raw_query = (request.args.get("q") or "").strip()
    concepts = [
        concept for concept in projection["concepts"]
        if _matches(concept, raw_query.casefold())
    ]
    return render_template(
        "catalogo_lesico.html", catalog_kind="internal",
        concepts=concepts,
        query=raw_query,
        selected_concept=None, selected_alternative=None, selected_relations=[],
        catalog_counts=_catalog_counts(projection),
        **_presentation(None),
        **_banner_context(blocking, non_blocking),
    )


@catalog_bp.get("/catalogo-interno/conceptos/<int:concept_id>")
def internal_concept(concept_id):
    projection, blocking, non_blocking = _load()
    concept = next(
        (item for item in projection["concepts"] if item["concept_id"] == concept_id),
        None,
    )
    if concept is None:
        abort(404)
    alternative = concept["alternatives"][0] if concept["alternatives"] else None
    return render_template(
        "catalogo_lesico.html", catalog_kind="internal",
        concepts=projection["concepts"], query="", selected_concept=concept,
        selected_alternative=alternative, selected_relations=[],
        catalog_counts=_catalog_counts(projection),
        **_presentation(concept),
        **_banner_context(blocking, non_blocking),
    )


@catalog_bp.get("/catalogo-interno/alternativas/<int:alternative_id>")
def internal_alternative(alternative_id):
    projection, blocking, non_blocking = _load()
    concept = alternative = None
    for item in projection["concepts"]:
        candidate = next(
            (row for row in item["alternatives"]
             if row["alternative_id"] == alternative_id),
            None,
        )
        if candidate is not None:
            concept, alternative = item, candidate
            break
    if alternative is None:
        abort(404)
    relations = [
        relation for relation in concept["relations"]
        if relation["alternative_relation_id"] in alternative["relation_ids"]
    ]
    return render_template(
        "catalogo_lesico.html", catalog_kind="internal",
        concepts=projection["concepts"], query="", selected_concept=concept,
        selected_alternative=alternative, selected_relations=relations,
        catalog_counts=_catalog_counts(projection),
        **_presentation(concept),
        **_banner_context(blocking, non_blocking),
    )


def _publication(version_number=None):
    db = conectar()
    try:
        if version_number is None:
            row = db.execute("SELECT * FROM catalog_publication ORDER BY version_number DESC LIMIT 1").fetchone()
        else:
            row = db.execute("SELECT * FROM catalog_publication WHERE version_number=?", (version_number,)).fetchone()
        latest = db.execute("SELECT max(version_number) FROM catalog_publication").fetchone()[0]
        publication = dict(row) if row else None
        if publication:
            publication["published_at_display"] = local_timestamp(publication["published_at"])
        return publication, latest
    finally: db.close()


def _external_context(version_number=None):
    publication, latest = _publication(version_number)
    if version_number is not None and publication is None: abort(404)
    projection = json.loads(publication["snapshot_json"]) if publication else {"concepts": []}
    return publication, latest, projection


@catalog_bp.get("/catalogo")
def external_catalog():
    publication, latest, projection = _external_context()
    query = (request.args.get("q") or "").strip()
    concepts = [c for c in projection["concepts"] if _matches(c, query.casefold())]
    return render_template("catalogo_lesico.html", catalog_kind="external",
        publication=publication, latest=latest, concepts=concepts, query=query,
        historical=False, selected_concept=None, selected_alternative=None, selected_relations=[])


@catalog_bp.get("/catalogo/v<int:version_number>")
def external_version(version_number):
    publication, latest, projection = _external_context(version_number)
    query = (request.args.get("q") or "").strip()
    return render_template("catalogo_lesico.html", catalog_kind="external",
        publication=publication, latest=latest,
        concepts=[c for c in projection["concepts"] if _matches(c, query.casefold())], query=query,
        historical=version_number != latest, selected_concept=None,
        selected_alternative=None, selected_relations=[])


def _external_concept(version_number, concept_id):
    publication, latest, projection = _external_context(version_number)
    concept = next((c for c in projection["concepts"] if c["concept_id"] == concept_id), None)
    if concept is None: abort(404)
    alternative = concept["alternatives"][0] if concept["alternatives"] else None
    return render_template("catalogo_lesico.html", catalog_kind="external",
        publication=publication, latest=latest, concepts=projection["concepts"], query="",
        selected_concept=concept, selected_alternative=alternative, selected_relations=[],
        **_presentation(concept),
        historical=publication["version_number"] != latest)


@catalog_bp.get("/catalogo/conceptos/<int:concept_id>")
def external_concept(concept_id): return _external_concept(None, concept_id)
@catalog_bp.get("/catalogo/v<int:version_number>/conceptos/<int:concept_id>")
def external_version_concept(version_number, concept_id): return _external_concept(version_number, concept_id)


def _external_alternative(version_number, alternative_id):
    publication, latest, projection = _external_context(version_number)
    concept = alternative = None
    for candidate in projection["concepts"]:
        alternative = next((a for a in candidate["alternatives"] if a["alternative_id"] == alternative_id), None)
        if alternative: concept = candidate; break
    if alternative is None: abort(404)
    relations = [r for r in concept["relations"] if r["alternative_relation_id"] in alternative["relation_ids"]]
    return render_template("catalogo_lesico.html", catalog_kind="external",
        publication=publication, latest=latest, concepts=projection["concepts"], query="",
        selected_concept=concept, selected_alternative=alternative, selected_relations=relations,
        **_presentation(concept),
        historical=publication["version_number"] != latest)


@catalog_bp.get("/catalogo/alternativas/<int:alternative_id>")
def external_alternative(alternative_id): return _external_alternative(None, alternative_id)
@catalog_bp.get("/catalogo/v<int:version_number>/alternativas/<int:alternative_id>")
def external_version_alternative(version_number, alternative_id): return _external_alternative(version_number, alternative_id)


@catalog_bp.get("/actualizar-catalogo")
@requires_master
def publication_update():
    db = conectar()
    try: preview = publication_preview(db, {"access_role": g.current_access_role})
    finally: db.close()
    return render_template("actualizar_catalogo.html", preview=preview, totals=summary_totals(preview["summary"]), error=None)


@catalog_bp.post("/actualizar-catalogo")
@requires_master
def publish_catalog_route():
    db = conectar()
    try:
        try:
            publication = publish_catalog(db, publication_comment=request.form.get("publication_comment"),
                actor_context={"access_role": g.current_access_role, "collaborator_id": request.form.get("collaborator_id")})
            return redirect(url_for("catalog.publications", published=publication["version_number"]))
        except (PublicationError, PublicationBlocked, IdenticalPublication) as error:
            preview = publication_preview(db, {"access_role": g.current_access_role})
            return render_template("actualizar_catalogo.html", preview=preview,
                totals=summary_totals(preview["summary"]), error=str(error)), 400
    finally: db.close()


@catalog_bp.get("/publicaciones")
@requires_master
def publications():
    db = conectar()
    try:
        rows = [dict(r) for r in db.execute("""SELECT p.*,(SELECT count(*) FROM publication_open_conflict pc WHERE pc.publication_id=p.publication_id) conflict_count FROM catalog_publication p ORDER BY version_number DESC""")]
        for row in rows:
            row["summary"] = summary_totals(json.loads(row["change_summary_json"]))
            row["conflicts"] = [dict(c) for c in db.execute("SELECT * FROM publication_open_conflict WHERE publication_id=? ORDER BY publication_open_conflict_id", (row["publication_id"],))]
    finally: db.close()
    return render_template("publicaciones.html", publications=rows)
