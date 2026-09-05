"""Optimistic preconditions using existing canonical rows and history."""

import hashlib
import json

from itsdangerous import BadData

STALE_EDIT = "Este registro cambió desde que lo abriste. Recarga para revisar los cambios antes de guardar."
STALE_PREVIEW = "Los datos cambiaron desde la previsualización. Revise nuevamente antes de confirmar."


class StaleEdit(ValueError):
    pass


def fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def sign(state):
    # Same configured secret / process fallback as Source previews.
    from routes.source_retirement import serializer
    return serializer().dumps(state)


def unsign(token, message=STALE_EDIT):
    from routes.source_retirement import serializer
    try:
        return serializer().loads(token or "", max_age=3600)
    except BadData:
        raise StaleEdit(message) from None


def rows(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args)]


def edit_state(db, kind, identifier):
    if kind in ("occurrence", "source", "concept"):
        state = {"row": rows(db, f"SELECT * FROM {kind} WHERE {kind}_id=?", (identifier,))}
        if kind != "concept":
            state["revision"] = db.execute(
                f"SELECT max({kind}_revision_id) FROM {kind}_revision WHERE {kind}_id=?", (identifier,)).fetchone()[0]
        else:
            state["revision"] = db.execute(
                "SELECT max(activity_event_id) FROM activity_event WHERE entity_type='concept' AND entity_id=? AND event_type='concept_renamed'", (identifier,)).fetchone()[0]
        return state
    if kind == "video":
        return rows(db, "SELECT * FROM alternative_media WHERE alternative_id=? AND role='catalog_video' AND is_current=1", (identifier,))
    if kind == "grammar":
        return rows(db, "SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1", (identifier,))
    if kind == "morphology":
        return {"alternative": rows(db, "SELECT alternative_id,concept_id,retired_at FROM alternative WHERE alternative_id=?", (identifier,)),
                "current": rows(db, "SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1", (identifier,)),
                "components": rows(db, "SELECT c.* FROM alternative_component c JOIN alternative_morphology m USING(alternative_morphology_id) WHERE m.alternative_id=? AND m.is_current=1 ORDER BY position", (identifier,))}
    raise ValueError("Unknown edit scope")


def edit_token(db, kind, identifier):
    return sign({"purpose": "edit", "kind": kind, "id": identifier,
                 "fingerprint": fingerprint(edit_state(db, kind, identifier))})


def check_edit(db, kind, identifier, token):
    expected = unsign(token)
    actual = {"purpose": "edit", "kind": kind, "id": identifier,
              "fingerprint": fingerprint(edit_state(db, kind, identifier))}
    if expected != actual:
        raise StaleEdit(STALE_EDIT)
