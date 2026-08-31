VALID_ACCESS_ROLES = frozenset(("analyst", "reviewer", "master"))


class InvalidActivity(ValueError):
    pass


def resolve_collaborator(connection, collaborator_id):
    if collaborator_id in (None, ""):
        return None, None
    try:
        collaborator_id = int(collaborator_id)
    except (TypeError, ValueError):
        return None, None
    row = connection.execute(
        "SELECT collaborator_id, display_name FROM collaborator "
        "WHERE collaborator_id=? AND active=1", (collaborator_id,)
    ).fetchone()
    return (row[0], row[1]) if row is not None else (None, None)


def record_activity(connection, event_type, *, entity_type=None, entity_id=None,
                    collaborator_id=None, access_role, comment=None):
    if access_role not in VALID_ACCESS_ROLES:
        raise InvalidActivity("Rol de acceso no válido.")
    event_type = (event_type or "").strip()
    if not event_type:
        raise InvalidActivity("El tipo de actividad es obligatorio.")
    actor_id, snapshot = resolve_collaborator(connection, collaborator_id)
    return connection.execute("""
        INSERT INTO activity_event(
            event_type,entity_type,entity_id,collaborator_id,
            collaborator_name_snapshot,access_role,comment
        ) VALUES(?,?,?,?,?,?,?)
    """, (event_type, entity_type, entity_id, actor_id, snapshot,
          access_role, (comment or "").strip() or None)).lastrowid

