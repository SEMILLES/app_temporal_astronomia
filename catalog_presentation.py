"""Presentation-only structures derived from a frozen catalog projection."""


def variation_groups(concept):
    """Return connected components from explicit relations, plus singletons."""
    alternatives = {item["alternative_id"]: item for item in concept.get("alternatives", [])}
    adjacency = {identifier: set() for identifier in alternatives}
    for relation in concept.get("relations", []):
        low = relation["alternative_low_id"]; high = relation["alternative_high_id"]
        if low in adjacency and high in adjacency:
            adjacency[low].add(high); adjacency[high].add(low)
    groups = []
    unseen = set(alternatives)
    while unseen:
        start = min(unseen); stack = [start]; component = []
        while stack:
            identifier = stack.pop()
            if identifier not in unseen: continue
            unseen.remove(identifier); component.append(alternatives[identifier])
            stack.extend(sorted(adjacency[identifier] & unseen, reverse=True))
        component.sort(key=lambda item: ((item.get("working_label") or "").casefold(), item["alternative_id"]))
        groups.append(component)
    groups.sort(key=lambda group: ((group[0].get("working_label") or "").casefold(), group[0]["alternative_id"]))
    return groups


def relation_edges(concept):
    """Combine explicit parameters per pair without adding transitive edges."""
    edges = {}
    for relation in concept.get("relations", []):
        pair = (relation["alternative_low_id"], relation["alternative_high_id"])
        edge = edges.setdefault(pair, {"low_id": pair[0], "high_id": pair[1],
            "low_name": relation["alternative_low_name"], "high_name": relation["alternative_high_name"], "parameters": []})
        edge["parameters"].append(relation["phonological_parameter"])
    return [edges[key] for key in sorted(edges)]


def variation_network_groups(concept):
    """Build stable, separately colored network cards for each component."""
    cards = []
    explicit_edges = relation_edges(concept)
    for index, group in enumerate(variation_groups(concept), 1):
        identifiers = {alternative["alternative_id"] for alternative in group}
        width = max(280, 150 * len(group)); height = 150
        gap = width / (len(group) + 1); nodes = []; positions = {}
        for column, alternative in enumerate(group, 1):
            node = {"alternative": alternative, "x": round(gap * column), "y": 75}
            nodes.append(node); positions[alternative["alternative_id"]] = node
        edges = []
        for edge in explicit_edges:
            if edge["low_id"] not in identifiers or edge["high_id"] not in identifiers: continue
            low = positions[edge["low_id"]]; high = positions[edge["high_id"]]
            edges.append({**edge, "x1": low["x"], "y1": low["y"], "x2": high["x"], "y2": high["y"],
                          "label_x": round((low["x"] + high["x"]) / 2), "label_y": 62})
        cards.append({"number": index, "color_index": (index - 1) % 6,
                      "width": width, "height": height, "nodes": nodes, "edges": edges})
    return cards
