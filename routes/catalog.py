from flask import Blueprint, abort, g, render_template, request

from catalog_projection import build_catalog_projection
from database import conectar

catalog_bp = Blueprint("catalog", __name__)


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
        "catalogo_interno.html",
        concepts=concepts,
        query=raw_query,
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
    return render_template(
        "catalogo_concepto.html",
        concept=concept,
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
        "catalogo_alternativa.html",
        concept=concept,
        alternative=alternative,
        relations=relations,
        **_banner_context(blocking, non_blocking),
    )
