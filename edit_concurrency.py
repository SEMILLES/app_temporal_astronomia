"""Optimistic preconditions using existing canonical rows and history."""

import hashlib
import json

from itsdangerous import BadData

STALE_EDIT = "El registro cambió después de abrir esta página. Es necesario recargarla para revisar los cambios antes de guardar."
STALE_PREVIEW = "El estado cambió desde la vista previa. Vuelve a revisar la decisión antes de confirmarla."


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


def submission_concept_scope(db, submission_id, metadata_target=None):
    """Bind the displayed destination and the original/current Concept context."""
    reference = db.execute('''SELECT a.reference_concept_id,a.reference_concept_proposal_id,
        p.proposed_label FROM alternative_submission a LEFT JOIN concept_proposal p
        ON p.concept_proposal_id=a.reference_concept_proposal_id WHERE a.submission_id=?''',
        (submission_id,)).fetchone()
    resolution = db.execute('SELECT concept_id FROM submission_concept_resolution WHERE submission_id=? AND is_current=1',
                            (submission_id,)).fetchone()
    relevant = {r[0] for r in db.execute('''SELECT r.concept_id FROM occurrence_concept_reference r
        JOIN submission s USING(occurrence_id) WHERE s.submission_id=? AND r.is_current=1 AND r.concept_id IS NOT NULL''',
        (submission_id,))}
    original = reference['reference_concept_id'] if reference else None
    current = resolution[0] if resolution else None
    for table in ('submission_classification_proposal', 'submission_collection_proposal'):
        relevant.update(r[0] for r in db.execute(f'SELECT base_concept_id FROM {table} WHERE submission_id=? AND base_concept_id IS NOT NULL',
                                                (submission_id,)))
    proposed_target = None
    if reference and reference['reference_concept_proposal_id']:
        from submission_concept_resolution import proposed_concept_decision
        from concept_labels import InvalidConceptLabel
        try:
            _, proposed_target, _ = proposed_concept_decision(db, reference)
        except InvalidConceptLabel:
            pass  # An invalid historical label can still be corrected by the reviewer.
    target = metadata_target if metadata_target not in (None, '') else current or original or proposed_target
    if target is not None:
        target = int(target)
        if not db.execute('SELECT 1 FROM concept WHERE concept_id=?', (target,)).fetchone():
            raise StaleEdit('El Concept de destino no existe.')
    relevant.update(cid for cid in (original, current, target) if cid is not None)
    return {'target_concept_id': target, 'concept_ids': sorted(relevant), 'allows_new': True}


def edit_state(db, kind, identifier, *, metadata_target=None):
    if kind in ("occurrence", "source", "concept"):
        state = {"row": rows(db, f"SELECT * FROM {kind} WHERE {kind}_id=?", (identifier,))}
        if kind != "concept":
            state["revision"] = db.execute(
                f"SELECT max({kind}_revision_id) FROM {kind}_revision WHERE {kind}_id=?", (identifier,)).fetchone()[0]
        else:
            from concept_classification import concept_state, catalog_state
            state['classifications'] = concept_state(db, identifier)
            state['controlled_catalogs'] = catalog_state(db)
            state["revision"] = db.execute(
                "SELECT max(activity_event_id) FROM activity_event WHERE entity_type='concept' AND entity_id=? AND event_type='concept_renamed'", (identifier,)).fetchone()[0]
        return state
    if kind == "submission_concept":
        from concept_classification import catalog_state
        scope = submission_concept_scope(db, identifier, metadata_target)
        concepts = scope['concept_ids']
        placeholders = ','.join('?' for _ in concepts) or 'NULL'
        return {"submission": rows(db, "SELECT * FROM submission WHERE submission_id=?", (identifier,)),
                "concept_scope": scope,
                "controlled_catalogs": catalog_state(db),
                "classification_history": rows(db, f'SELECT * FROM concept_classification_revision WHERE concept_id IN ({placeholders}) ORDER BY revision_id', concepts),
                "memberships": rows(db, f'SELECT * FROM collection_membership WHERE concept_id IN ({placeholders}) ORDER BY membership_id', concepts),
                "classification_proposals": rows(db, 'SELECT * FROM submission_classification_proposal WHERE submission_id=? ORDER BY system_id', (identifier,)),
                "collection_proposals": rows(db, 'SELECT * FROM submission_collection_proposal WHERE submission_id=? ORDER BY collection_id', (identifier,)),
                "resolution": rows(db, "SELECT * FROM submission_concept_resolution WHERE submission_id=? AND is_current=1", (identifier,)),
                "reference": rows(db, "SELECT r.* FROM occurrence_concept_reference r JOIN submission s USING(occurrence_id) WHERE s.submission_id=? AND r.is_current=1", (identifier,)),
                "concepts": rows(db, "SELECT concept_id,preferred_label FROM concept ORDER BY concept_id")}
    if kind == 'classification_catalog':
        from concept_classification import catalog_state
        return catalog_state(db)
    if kind == "video":
        return rows(db, "SELECT * FROM alternative_media WHERE alternative_id=? AND role='catalog_video' AND is_current=1", (identifier,))
    if kind == "grammar":
        return rows(db, "SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1", (identifier,))
    if kind == "morphology":
        return {"alternative": rows(db, "SELECT alternative_id,concept_id,retired_at FROM alternative WHERE alternative_id=?", (identifier,)),
                "current": rows(db, "SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1", (identifier,)),
                "components": rows(db, "SELECT c.* FROM alternative_component c JOIN alternative_morphology m USING(alternative_morphology_id) WHERE m.alternative_id=? AND m.is_current=1 ORDER BY position", (identifier,))}
    raise ValueError("Unknown edit scope")


def edit_token(db, kind, identifier, *, metadata_target=None):
    state = edit_state(db, kind, identifier, metadata_target=metadata_target)
    payload = {"purpose": "edit", "kind": kind, "id": identifier,
               "fingerprint": fingerprint(state)}
    if kind == 'submission_concept':
        payload['concept_scope'] = state['concept_scope']
    return sign(payload)


def check_edit(db, kind, identifier, token):
    expected = unsign(token)
    scope = expected.get('concept_scope') if kind == 'submission_concept' else None
    if kind == 'submission_concept' and not isinstance(scope, dict):
        raise StaleEdit(STALE_EDIT)
    state = edit_state(db, kind, identifier,
                      metadata_target=scope.get('target_concept_id') if scope else None)
    actual = {"purpose": "edit", "kind": kind, "id": identifier,
              "fingerprint": fingerprint(state)}
    if kind == 'submission_concept':
        actual['concept_scope'] = state['concept_scope']
    if expected != actual:
        raise StaleEdit(STALE_EDIT)
    return scope
