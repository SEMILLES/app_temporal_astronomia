import re


LABEL_PATTERN = re.compile(r"[1-9][0-9]*[a-z]")
CREATED_AT_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
)


class InconclusiveNomenclatureError(ValueError):
    pass


class InvalidNomenclatureError(ValueError):
    pass


def temporal_reference(occurrence_year, start_year, end_year, end_year_status):
    if occurrence_year is not None:
        return int(occurrence_year), "occurrence_year"
    if start_year is None:
        return None, None
    if end_year == start_year and end_year_status == "known":
        return int(start_year), "source_single_year"
    return int(start_year), "source_range_start"


def _alternative_rows(connection, concept_id, occurrence_overrides=None,
                      virtual_occurrences=None):
    overrides = occurrence_overrides or {}
    alternatives = connection.execute(
        "SELECT alternative_id, working_label, created_at FROM alternative "
        "WHERE concept_id=? AND retired_at IS NULL", (concept_id,)
    ).fetchall()
    result = []
    for alternative in alternatives:
        occurrence_id = overrides.get(alternative[0])
        if occurrence_id is None:
            evidence = connection.execute("""
                SELECT o.occurrence_year,s.start_year,s.end_year,s.end_year_status,
                       s.source_name,s.source_reference
                FROM assignment a JOIN occurrence o USING(occurrence_id)
                JOIN source s USING(source_id)
                WHERE a.alternative_id=? AND a.is_current=1
            """, (alternative[0],)).fetchall()
        else:
            evidence = connection.execute("""
                SELECT o.occurrence_year,s.start_year,s.end_year,s.end_year_status,
                       s.source_name,s.source_reference
                FROM occurrence o JOIN source s USING(source_id)
                WHERE o.occurrence_id=?
            """, (occurrence_id,)).fetchall()
        references = [(*temporal_reference(*row[:4]), row[5] or row[4]) for row in evidence]
        usable = [item for item in references if item[0] is not None]
        reference = min(usable, default=(None, None, None), key=lambda item: item[0])
        result.append({
            "alternative_id": alternative[0], "current_label": alternative[1],
            "reference_year": reference[0], "reference_basis": reference[1],
            "reference_source": reference[2],
            "created_at": alternative[2],
        })
    for alternative_id, occurrence_id in (virtual_occurrences or {}).items():
        evidence = connection.execute("""
            SELECT o.occurrence_year,s.start_year,s.end_year,s.end_year_status,
                   s.source_name,s.source_reference
            FROM occurrence o JOIN source s USING(source_id)
            WHERE o.occurrence_id=?
        """, (occurrence_id,)).fetchall()
        references = [(*temporal_reference(*row[:4]), row[5] or row[4]) for row in evidence]
        usable = [item for item in references if item[0] is not None]
        reference = min(usable, default=(None, None, None), key=lambda item: item[0])
        result.append({"alternative_id": alternative_id, "current_label": None,
                       "reference_year": reference[0],
                       "reference_basis": reference[1],
                       "reference_source": reference[2], "created_at": None})
    return result


def _registration_key(row):
    """Stable technical order; it does not assert historical precedence."""
    created_at = row.get("created_at")
    identifier = row["alternative_id"]
    identifier_key = (0, identifier) if isinstance(identifier, int) else (1, str(identifier))
    reliable = str(created_at) if created_at and CREATED_AT_PATTERN.fullmatch(str(created_at)) else None
    return (0, reliable, identifier_key) if reliable else (1, "", identifier_key)


def _temporal_key(row):
    year = row["reference_year"]
    return (year is None, year if year is not None else 0, _registration_key(row))


def connected_components(node_ids, edges):
    graph = {node: set() for node in node_ids}
    for left, right in edges:
        if left in graph and right in graph and left != right:
            graph[left].add(right); graph[right].add(left)
    components = []
    unseen = set(node_ids)
    while unseen:
        seed = next(iter(unseen))
        component = set(); stack = [seed]
        while stack:
            node = stack.pop()
            if node in component: continue
            component.add(node); unseen.discard(node); stack.extend(graph[node]-component)
        components.append(component)
    return components


def calculate_nomenclature_preview(connection, concept_id, *, extra_edges=(),
                                   occurrence_overrides=None,
                                   virtual_occurrences=None):
    rows = _alternative_rows(
        connection, concept_id, occurrence_overrides, virtual_occurrences
    )
    ids = {row["alternative_id"] for row in rows}
    relation_edges = [tuple(row) for row in connection.execute("""
        SELECT r.alternative_low_id,r.alternative_high_id
        FROM alternative_relation r
        JOIN alternative low ON low.alternative_id=r.alternative_low_id
        JOIN alternative high ON high.alternative_id=r.alternative_high_id
        WHERE r.is_current=1 AND low.concept_id=? AND high.concept_id=?
          AND low.retired_at IS NULL AND high.retired_at IS NULL
    """, (concept_id, concept_id)).fetchall()]
    components = connected_components(ids, [*relation_edges, *extra_edges])
    by_id = {row["alternative_id"]: row for row in rows}
    component_rows = []
    for component in components:
        members = [by_id[node] for node in component]
        known = [row["reference_year"] for row in members if row["reference_year"] is not None]
        first_registered = min(_registration_key(row) for row in members)
        component_rows.append((min(known) if known else None, first_registered, members))
    component_rows.sort(key=lambda item: (
        item[0] is None, item[0] if item[0] is not None else 0, item[1]
    ))
    suggestions = {}
    for group_number, (_, _, members) in enumerate(component_rows, 1):
        members.sort(key=_temporal_key)
        for index, row in enumerate(members):
            suggestions[row["alternative_id"]] = f"{group_number}{chr(97+index)}"
    for row in rows:
        row["proposed_label"] = suggestions.get(row["alternative_id"])
    return {"conclusive": True, "problems": [], "rows": rows, "suggestions": suggestions}


def validate_final_labels(connection, concept_id, labels, required_edges=()):
    active = {row[0] for row in connection.execute(
        "SELECT alternative_id FROM alternative WHERE concept_id=? AND retired_at IS NULL",
        (concept_id,),
    )}
    if set(labels) != active:
        raise InvalidNomenclatureError("Deben indicarse labels para todas las alternatives vigentes.")
    cleaned = {key: str(value).strip() for key, value in labels.items()}
    if any(not value or LABEL_PATTERN.fullmatch(value) is None for value in cleaned.values()):
        raise InvalidNomenclatureError("Hay una working_label vacía o no válida.")
    if len(set(cleaned.values())) != len(cleaned):
        raise InvalidNomenclatureError("No puede repetirse working_label dentro del concept.")
    current_edges = [tuple(row) for row in connection.execute("""
        SELECT r.alternative_low_id,r.alternative_high_id
        FROM alternative_relation r
        JOIN alternative low ON low.alternative_id=r.alternative_low_id
        JOIN alternative high ON high.alternative_id=r.alternative_high_id
        WHERE r.is_current=1 AND low.concept_id=? AND high.concept_id=?
          AND low.retired_at IS NULL AND high.retired_at IS NULL
    """, (concept_id, concept_id)).fetchall()]
    for component in connected_components(active, [*current_edges, *required_edges]):
        if len(component) < 2:
            continue
        parsed = [re.fullmatch(r"([1-9][0-9]*)([a-z])", cleaned[item]) for item in component]
        if any(match is None for match in parsed) or len({match.group(1) for match in parsed}) != 1:
            raise InvalidNomenclatureError(
                "Las alternatives conectadas deben compartir número de grupo y usar letras."
            )
    return cleaned


def apply_nomenclature(connection, concept_id, labels, *, origin, reason=None,
                       submission_id=None, created_by=None, required_edges=()):
    labels = validate_final_labels(connection, concept_id, labels, required_edges)
    if origin not in ("automatic_assisted", "manual"):
        raise InvalidNomenclatureError("Origen de nomenclatura no válido.")
    reason = (reason or "").strip() or None
    if origin == "manual" and reason is None:
        raise InvalidNomenclatureError("La nomenclatura manual exige una justificación.")
    current = dict(connection.execute(
        "SELECT alternative_id,working_label FROM alternative WHERE concept_id=? AND retired_at IS NULL",
        (concept_id,),
    ).fetchall())
    changes = [(key, current[key], labels[key]) for key in labels if current[key] != labels[key]]
    if not changes:
        return None
    cursor = connection.execute("""
        INSERT INTO renumber_event(concept_id,origin,reason,created_from_submission_id,created_by)
        VALUES(?,?,?,?,?)
    """, (concept_id, origin, reason, submission_id, created_by))
    event_id = cursor.lastrowid
    for alternative_id, old, new in changes:
        connection.execute(
            "INSERT INTO renumber_change(renumber_event_id,alternative_id,old_working_label,new_working_label) VALUES(?,?,?,?)",
            (event_id, alternative_id, old, new),
        )
        connection.execute("UPDATE alternative SET working_label=? WHERE alternative_id=?", (new, alternative_id))
    from conflicts import detect_conflicts_after_change
    detect_conflicts_after_change(connection,"renumber_event",event_id)
    return event_id
