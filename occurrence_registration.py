import sqlite3

from concept_labels import InvalidConceptLabel, normalize_concept_label
from activity import record_activity
from source_period import validate_occurrence_year
from source_details import normalize_occurrence_details


class RegistrationError(ValueError):
    pass


def _text(value):
    value = "" if value is None else str(value).strip()
    return value or None


def _year(value):
    value = _text(value)
    if value is None:
        return None
    if not value.isdigit() or len(value) != 4 or int(value) < 1:
        raise RegistrationError("El año de la ocurrencia no es válido.")
    return int(value)


def _flag(value):
    return 1 if str(value or "").strip().upper() in ("1", "YES", "SI", "SÍ", "ON") else 0


def resolve_concept_reference(connection, concept_id=None,
                              concept_proposal_id=None, proposed_label=None,
                              force_new_proposal=False):
    choices = sum(value not in (None, "") for value in (
        concept_id, concept_proposal_id, proposed_label
    ))
    if choices != 1:
        raise RegistrationError("Debe indicar exactamente un concepto de referencia.")
    if concept_id not in (None, ""):
        row = connection.execute(
            "SELECT concept_id FROM concept WHERE concept_id = ?", (concept_id,)
        ).fetchone()
        if row is None:
            raise RegistrationError("El concepto de referencia no existe.")
        return int(concept_id), None
    if concept_proposal_id not in (None, ""):
        row = connection.execute(
            "SELECT concept_proposal_id FROM concept_proposal "
            "WHERE concept_proposal_id = ? AND status = 'pending'",
            (concept_proposal_id,),
        ).fetchone()
        if row is None:
            raise RegistrationError("La propuesta conceptual no está pendiente.")
        return None, int(concept_proposal_id)

    normalized = normalize_concept_label(proposed_label)
    if force_new_proposal:
        cursor = connection.execute(
            "INSERT INTO concept_proposal (proposed_label, status) VALUES (?, 'pending')",
            (normalized,),
        )
        return None, cursor.lastrowid
    concept = connection.execute(
        "SELECT concept_id FROM concept WHERE UPPER(preferred_label) = UPPER(?)",
        (normalized,),
    ).fetchone()
    if concept is not None and not force_new_proposal:
        return concept[0], None
    proposals = connection.execute(
        "SELECT concept_proposal_id, proposed_label FROM concept_proposal "
        "WHERE status = 'pending'"
    ).fetchall()
    for proposal in proposals:
        try:
            if normalize_concept_label(proposal[1]) == normalized:
                return None, proposal[0]
        except InvalidConceptLabel:
            continue
    cursor = connection.execute(
        "INSERT INTO concept_proposal (proposed_label, status) VALUES (?, 'pending')",
        (normalized,),
    )
    return None, cursor.lastrowid


def save_draft(connection, draft_id=None, *, collaborator_id=None, access_role=None, **values):
    fields = (
        "source_id", "original_gloss", "occurrence_year", "source_detail_1",
        "source_detail_2", "source_detail_1_status", "source_detail_2_status", "usage_examples_present", "grammatical_info_present",
        "grammatical_note", "source_locator", "provenance_note", "reference_concept_id",
        "reference_concept_proposal_id",
    )
    data = {
        "source_id": int(values["source_id"]) if values.get("source_id") else None,
        "original_gloss": _text(values.get("original_gloss")),
        "occurrence_year": _year(values.get("occurrence_year")),
        "source_detail_1": _text(values.get("source_detail_1")),
        "source_detail_2": _text(values.get("source_detail_2")),
        "source_detail_1_status": values.get("source_detail_1_status") or ("VALUE" if _text(values.get("source_detail_1")) else "UNKNOWN"),
        "source_detail_2_status": values.get("source_detail_2_status") or ("VALUE" if _text(values.get("source_detail_2")) else "UNKNOWN"),
        "usage_examples_present": _flag(values.get("usage_examples_present")),
        "grammatical_info_present": _flag(values.get("grammatical_info_present")),
        "grammatical_note": _text(values.get("grammatical_note")) if _flag(values.get("grammatical_info_present")) else None,
        "source_locator": _text(values.get("source_locator")),
        "provenance_note": _text(values.get("provenance_note")),
        "reference_concept_id": (
            int(values["reference_concept_id"])
            if values.get("reference_concept_id") else None
        ),
        "reference_concept_proposal_id": (
            int(values["reference_concept_proposal_id"])
            if values.get("reference_concept_proposal_id") else None
        ),
    }
    if data["source_id"] is not None:
        data["occurrence_year"] = validate_occurrence_year(
            connection, data["source_id"], values.get("occurrence_year")
        )
    if data["reference_concept_id"] and data["reference_concept_proposal_id"]:
        raise RegistrationError("Un borrador no puede tener dos referencias conceptuales.")
    if draft_id is None:
        cursor = connection.execute(
            f"INSERT INTO occurrence_draft ({', '.join(fields)}) "
            f"VALUES ({', '.join('?' for _ in fields)})",
            tuple(data[field] for field in fields),
        )
        if access_role:
            record_activity(connection, "occurrence_draft_created", entity_type="occurrence_draft",
                            entity_id=cursor.lastrowid, collaborator_id=collaborator_id,
                            access_role=access_role)
        connection.commit()
        return cursor.lastrowid
    cursor = connection.execute(
        "UPDATE occurrence_draft SET "
        + ", ".join(f"{field} = ?" for field in fields)
        + ", updated_at = CURRENT_TIMESTAMP WHERE draft_id = ?",
        (*[data[field] for field in fields], draft_id),
    )
    if cursor.rowcount != 1:
        connection.rollback()
        raise RegistrationError("El borrador no existe.")
    connection.commit()
    return int(draft_id)


def complete_registration(connection, *, draft_id=None, source_id=None,
                          original_gloss=None, occurrence_year=None,
                          source_detail_1=None, source_detail_2=None,
                          source_detail_1_status=None, source_detail_2_status=None,
                          usage_examples_present=False,
                          grammatical_info_present=False, grammatical_note=None,
                          source_locator=None, provenance_note=None,
                          hyperlink=None, concept_id=None,
                          concept_proposal_id=None, proposed_label=None,
                          collaborator_id=None, access_role=None,
                          force_new_proposal=False):
    owns_transaction = not connection.in_transaction
    try:
        connection.execute("BEGIN IMMEDIATE" if owns_transaction else "SAVEPOINT registration")
        if draft_id is not None:
            draft = connection.execute(
                "SELECT * FROM occurrence_draft WHERE draft_id = ?", (draft_id,)
            ).fetchone()
            if draft is None:
                raise RegistrationError("El borrador no existe.")
            source_id = source_id or draft["source_id"]
            original_gloss = original_gloss or draft["original_gloss"]
            occurrence_year = occurrence_year or draft["occurrence_year"]
            source_detail_1 = source_detail_1 or draft["source_detail_1"]
            source_detail_2 = source_detail_2 or draft["source_detail_2"]
            source_detail_1_status = source_detail_1_status or draft["source_detail_1_status"]
            source_detail_2_status = source_detail_2_status or draft["source_detail_2_status"]
            usage_examples_present = usage_examples_present or draft["usage_examples_present"]
            grammatical_info_present = grammatical_info_present or draft["grammatical_info_present"]
            grammatical_note = grammatical_note or draft["grammatical_note"]
            source_locator = source_locator or draft["source_locator"]
            provenance_note = provenance_note or draft["provenance_note"]
            concept_id = concept_id or draft["reference_concept_id"]
            concept_proposal_id = (
                concept_proposal_id or draft["reference_concept_proposal_id"]
            )
        gloss = _text(original_gloss)
        if not source_id or gloss is None:
            raise RegistrationError("La fuente y la glosa original son obligatorias.")
        source = connection.execute("SELECT source_type FROM source WHERE source_id = ? AND retired_at IS NULL", (source_id,)).fetchone()
        if source is None:
            raise RegistrationError("La fuente no existe.")
        source_detail_1_status = source_detail_1_status or ("VALUE" if _text(source_detail_1) else "UNKNOWN")
        source_detail_2_status = source_detail_2_status or ("VALUE" if _text(source_detail_2) else "UNKNOWN")
        try:
            source_detail_1_status, source_detail_1, source_detail_2_status, source_detail_2 = normalize_occurrence_details(source[0], source_detail_1_status, source_detail_1, source_detail_2_status, source_detail_2)
        except ValueError as error: raise RegistrationError(str(error)) from error
        reference_concept_id, reference_proposal_id = resolve_concept_reference(
            connection, concept_id, concept_proposal_id, proposed_label,
            force_new_proposal=force_new_proposal
        )
        if access_role and proposed_label not in (None, "") and reference_proposal_id is not None:
            record_activity(connection, "concept_proposal_created",
                            entity_type="concept_proposal", entity_id=reference_proposal_id,
                            collaborator_id=collaborator_id, access_role=access_role)
        cursor = connection.execute(
            "INSERT INTO occurrence (source_id, original_gloss, hyperlink, occurrence_year, "
            "source_detail_1, source_detail_2, source_detail_1_status, source_detail_2_status, usage_examples_present, "
            "grammatical_info_present, grammatical_note, source_locator, provenance_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(source_id), gloss, _text(hyperlink),
             validate_occurrence_year(connection, source_id, occurrence_year),
             _text(source_detail_1), _text(source_detail_2), source_detail_1_status, source_detail_2_status,
             _flag(usage_examples_present), _flag(grammatical_info_present),
             _text(grammatical_note) if _flag(grammatical_info_present) else None,
             _text(source_locator), _text(provenance_note)),
        )
        occurrence_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO occurrence_concept_reference "
            "(occurrence_id, concept_id, concept_proposal_id) VALUES (?, ?, ?)",
            (occurrence_id, reference_concept_id, reference_proposal_id),
        )
        if draft_id is not None:
            connection.execute("DELETE FROM occurrence_draft WHERE draft_id = ?", (draft_id,))
        if access_role:
            record_activity(connection, "occurrence_registered", entity_type="occurrence",
                            entity_id=occurrence_id, collaborator_id=collaborator_id,
                            access_role=access_role)
        connection.commit() if owns_transaction else connection.execute("RELEASE SAVEPOINT registration")
        return occurrence_id
    except Exception:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute("ROLLBACK TO SAVEPOINT registration")
            connection.execute("RELEASE SAVEPOINT registration")
        raise
