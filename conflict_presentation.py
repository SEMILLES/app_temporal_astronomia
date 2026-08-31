from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UI_LABELS = {
    "open": "Abierto", "resolved": "Resuelto",
    "failed": "Fallido", "succeeded": "Exitoso",
    "automatic": "Automático", "manual": "Manual",
    "blocking": "Bloquea publicación",
    "non_blocking": "NO bloquea publicación",
    "workflow": "Flujo de trabajo",
    "global_validation": "Validación global",
}

SUBJECT_TYPE_LABELS = {
    "occurrence": "Ocurrencia", "concept": "Concept",
    "concept_proposal": "Propuesta de concept",
    "alternative": "Alternative", "alternative_relation": "Relación",
    "assignment": "Asignación", "submission": "Aporte",
    "renumber_event": "Reordenamiento",
    "alternative_morphology": "Morphology de alternative",
}


def ui_label(value):
    return UI_LABELS.get(value, value or "")


def local_timestamp(value, timezone_name="America/Bogota"):
    if not value:
        return ""
    parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=timezone.utc)
    try:
        local_zone=ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name!="America/Bogota":
            raise
        local_zone=timezone(timedelta(hours=-5),"America/Bogota")
    return parsed.astimezone(local_zone).strftime("%Y-%m-%d %H:%M")


def _alternative_label(connection, alternative_id):
    row=connection.execute("""SELECT c.preferred_label,a.working_label FROM alternative a
        JOIN concept c USING(concept_id) WHERE a.alternative_id=?""",(alternative_id,)).fetchone()
    return f"{row[0]}-{row[1]}" if row and row[1] else (row[0] if row else None)


def format_subject(connection, subject_type, subject_id):
    subject_id=int(subject_id)
    if subject_type=="occurrence":
        row=connection.execute("SELECT original_gloss FROM occurrence WHERE occurrence_id=?",(subject_id,)).fetchone()
        return f"Ocurrencia {subject_id} — {row[0] or 'Sin glosa'}" if row else f"Ocurrencia {subject_id}"
    if subject_type=="concept":
        row=connection.execute("SELECT preferred_label FROM concept WHERE concept_id=?",(subject_id,)).fetchone()
        return row[0] if row else f"Concept {subject_id}"
    if subject_type=="alternative":
        return _alternative_label(connection,subject_id) or f"Alternative {subject_id}"
    if subject_type=="submission":
        row=connection.execute("SELECT submission_type FROM submission WHERE submission_id=?",(subject_id,)).fetchone()
        kind={"ALTERNATIVE":"Análisis de alternativa","GRAMMAR":"Análisis gramatical"}.get(row[0],row[0]) if row else None
        return f"Aporte #{subject_id} — {kind}" if kind else f"Aporte #{subject_id}"
    if subject_type=="alternative_relation":
        row=connection.execute("SELECT alternative_low_id,alternative_high_id,phonological_parameter FROM alternative_relation WHERE alternative_relation_id=?",(subject_id,)).fetchone()
        if row:return f"{_alternative_label(connection,row[0])} ↔ {_alternative_label(connection,row[1])} — {row[2]}"
    return f"{SUBJECT_TYPE_LABELS.get(subject_type,subject_type)} #{subject_id}"


def subject_choices(connection):
    choices=[]
    queries={
      "occurrence":"SELECT occurrence_id,coalesce(original_gloss,'Sin glosa') FROM occurrence ORDER BY occurrence_id",
      "concept":"SELECT concept_id,preferred_label FROM concept ORDER BY preferred_label",
      "alternative":"SELECT alternative_id,alternative_id FROM alternative ORDER BY alternative_id",
      "submission":"SELECT submission_id,submission_id FROM submission ORDER BY submission_id",
      "alternative_relation":"SELECT alternative_relation_id,alternative_relation_id FROM alternative_relation ORDER BY alternative_relation_id",
    }
    for subject_type,sql in queries.items():
        for row in connection.execute(sql):
            choices.append({"type":subject_type,"id":row[0],"label":format_subject(connection,subject_type,row[0])})
    return choices
