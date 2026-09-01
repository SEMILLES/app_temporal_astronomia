"""Creation and retrieval of immutable versioned catalog publications."""
import hashlib
import json

from activity import record_activity, resolve_collaborator
from catalog_diff import build_catalog_diff
from catalog_projection import build_catalog_projection
from conflicts import run_global_conflict_validation


class PublicationError(ValueError): pass
class PublicationBlocked(PublicationError): pass
class IdenticalPublication(PublicationError): pass


def serialize_catalog_projection(projection):
    return json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_sha256(snapshot_json):
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


def verify_publication_hash(publication):
    return snapshot_sha256(publication["snapshot_json"]) == publication["snapshot_sha256"]


def projection_counts(projection):
    concepts = projection.get("concepts", [])
    alternatives = [a for c in concepts for a in c.get("alternatives", [])]
    occurrences = [o for a in alternatives for o in a.get("occurrences", [])]
    relation_ids = {r["alternative_relation_id"] for c in concepts for r in c.get("relations", [])}
    return len(concepts), len(alternatives), len(occurrences), len(relation_ids)


def _open_conflicts(connection, severity):
    return connection.execute("SELECT * FROM conflict WHERE status='open' AND severity=? ORDER BY conflict_id", (severity,)).fetchall()


def publication_preview(connection, actor_context):
    if (actor_context or {}).get("access_role") != "master": raise PublicationError("Se requiere acceso Máster.")
    validation = run_global_conflict_validation(connection, actor_context)
    projection = build_catalog_projection(connection)
    latest = connection.execute("SELECT * FROM catalog_publication ORDER BY version_number DESC LIMIT 1").fetchone()
    previous = json.loads(latest["snapshot_json"]) if latest else None
    serialized = serialize_catalog_projection(projection)
    return {"validation": validation, "projection": projection, "sha256": snapshot_sha256(serialized),
            "identical": bool(latest and latest["snapshot_sha256"] == snapshot_sha256(serialized)),
            "next_version": (latest["version_number"] if latest else 0) + 1,
            "summary": build_catalog_diff(previous, projection),
            "blocking": _open_conflicts(connection, "blocking"),
            "non_blocking": _open_conflicts(connection, "non_blocking")}


def publish_catalog(connection, *, publication_comment, actor_context):
    context = actor_context or {}
    if context.get("access_role") != "master": raise PublicationError("Se requiere acceso Máster.")
    comment = (publication_comment or "").strip()
    if not comment: raise PublicationError("El comentario de publicación es obligatorio.")
    owns = not connection.in_transaction
    connection.execute("BEGIN IMMEDIATE" if owns else "SAVEPOINT publish_catalog")
    try:
        validation = run_global_conflict_validation(connection, context)
        if validation["blocking_open_ids"]:
            raise PublicationBlocked("No se puede actualizar el catálogo mientras existan conflictos que bloquean publicación.")
        projection = build_catalog_projection(connection)
        serialized = serialize_catalog_projection(projection); digest = snapshot_sha256(serialized)
        latest = connection.execute("SELECT * FROM catalog_publication ORDER BY version_number DESC LIMIT 1").fetchone()
        if latest and latest["snapshot_sha256"] == digest:
            raise IdenticalPublication("El catálogo interno no contiene cambios respecto de la última versión publicada.")
        version = (latest["version_number"] if latest else 0) + 1
        previous = json.loads(latest["snapshot_json"]) if latest else None
        summary_json = serialize_catalog_projection(build_catalog_diff(previous, projection))
        actor_id, actor_name = resolve_collaborator(connection, context.get("collaborator_id"))
        counts = projection_counts(projection)
        cursor = connection.execute("""INSERT INTO catalog_publication(version_number,snapshot_json,snapshot_sha256,change_summary_json,publication_comment,published_by_collaborator_id,published_by_name_snapshot,published_access_role,concept_count,alternative_count,occurrence_count,relation_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (version, serialized, digest, summary_json, comment, actor_id, actor_name, "master", *counts))
        publication_id = cursor.lastrowid
        open_rows = _open_conflicts(connection, "non_blocking")
        connection.executemany("""INSERT INTO publication_open_conflict(publication_id,conflict_id,conflict_type_snapshot,description_snapshot,severity_snapshot,subject_signature_snapshot) VALUES(?,?,?,?,?,?)""",
            [(publication_id, row["conflict_id"], row["rule_code"] or row["origin_kind"], row["description"], row["severity"], row["subject_signature"]) for row in open_rows])
        record_activity(connection, "catalog_published", entity_type="catalog_publication", entity_id=publication_id, collaborator_id=actor_id, access_role="master", comment=comment)
        connection.commit() if owns else connection.execute("RELEASE SAVEPOINT publish_catalog")
        return connection.execute("SELECT * FROM catalog_publication WHERE publication_id=?", (publication_id,)).fetchone()
    except Exception:
        if owns: connection.rollback()
        else:
            connection.execute("ROLLBACK TO SAVEPOINT publish_catalog")
            connection.execute("RELEASE SAVEPOINT publish_catalog")
        raise
