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


def variation_network(concept):
    groups = variation_groups(concept); nodes = []; positions = {}; width = 640
    for row, group in enumerate(groups):
        gap = width / (len(group) + 1)
        for column, alternative in enumerate(group, 1):
            node = {"alternative": alternative, "x": round(gap * column), "y": 55 + row * 105}
            nodes.append(node); positions[alternative["alternative_id"]] = node
    edges = []
    for edge in relation_edges(concept):
        low = positions.get(edge["low_id"]); high = positions.get(edge["high_id"])
        if low and high:
            edges.append({**edge, "x1": low["x"], "y1": low["y"], "x2": high["x"], "y2": high["y"],
                          "label_x": round((low["x"] + high["x"]) / 2), "label_y": round((low["y"] + high["y"]) / 2) - 8})
    return {"width": width, "height": max(120, len(groups) * 105), "nodes": nodes, "edges": edges}
