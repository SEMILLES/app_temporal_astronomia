"""Presentation-only structures derived from a frozen catalog projection."""

import math


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


def _topology_order(group, edges):
    """Order nodes by graph traversal, independent from their display labels."""
    identifiers = {alternative["alternative_id"] for alternative in group}
    adjacency = {identifier: set() for identifier in identifiers}
    for edge in edges:
        adjacency[edge["low_id"]].add(edge["high_id"])
        adjacency[edge["high_id"]].add(edge["low_id"])
    start = min(identifiers); ordered = []; queued = {start}; queue = [start]
    while queue:
        identifier = queue.pop(0); ordered.append(identifier)
        neighbors = sorted(adjacency[identifier] - queued,
                           key=lambda item: (-len(adjacency[item]), item))
        queue.extend(neighbors); queued.update(neighbors)
    return ordered


def _node_positions(group, edges):
    """Place small graphs on a roomy deterministic grid inspired by legacy UI."""
    count = len(group)
    if count == 1:
        return 280, 150, {group[0]["alternative_id"]: (140, 75)}
    order = _topology_order(group, edges)
    columns = 2 if count <= 4 else math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns); x_gap = 240; y_gap = 135
    width = 200 + x_gap * (columns - 1); height = 170 + y_gap * (rows - 1)
    positions = {}
    for index, identifier in enumerate(order):
        row, column = divmod(index, columns)
        items_in_row = min(columns, count - row * columns)
        row_width = x_gap * (items_in_row - 1)
        positions[identifier] = (round((width - row_width) / 2 + column * x_gap),
                                 85 + row * y_gap)
    return width, height, positions


def _edge_label_position(low, high, nodes):
    """Keep the relation capsule near a free portion of its edge."""
    midpoint_x = (low["x"] + high["x"]) / 2
    midpoint_y = (low["y"] + high["y"]) / 2
    dx = high["x"] - low["x"]; dy = high["y"] - low["y"]
    length = math.hypot(dx, dy) or 1
    candidates = []
    for offset in (-20, 20, -42, 42):
        x = midpoint_x - dy / length * offset
        y = midpoint_y + dx / length * offset
        clearance = min(math.hypot(x - node["x"], y - node["y"]) for node in nodes)
        candidates.append((clearance, round(x), round(y)))
    _, x, y = max(candidates)
    return x, y


def variation_network_groups(concept):
    """Build stable, separately colored network cards for each component."""
    cards = []
    explicit_edges = relation_edges(concept)
    for index, group in enumerate(variation_groups(concept), 1):
        identifiers = {alternative["alternative_id"] for alternative in group}
        group_edges = [edge for edge in explicit_edges
                       if edge["low_id"] in identifiers and edge["high_id"] in identifiers]
        width, height, coordinates = _node_positions(group, group_edges)
        alternatives = {alternative["alternative_id"]: alternative for alternative in group}
        nodes = [{"alternative": alternatives[identifier], "x": x, "y": y}
                 for identifier, (x, y) in coordinates.items()]
        positions = {node["alternative"]["alternative_id"]: node for node in nodes}
        edges = []
        for edge in group_edges:
            low = positions[edge["low_id"]]; high = positions[edge["high_id"]]
            label_x, label_y = _edge_label_position(low, high, nodes)
            label = " · ".join(edge["parameters"])
            edges.append({**edge, "x1": low["x"], "y1": low["y"], "x2": high["x"], "y2": high["y"],
                          "label_x": label_x, "label_y": label_y,
                          "label_half_width": max(34, min(86, 7 * len(label) / 2 + 10))})
        cards.append({"number": index, "color_index": (index - 1) % 6,
                      "width": width, "height": height, "nodes": nodes, "edges": edges})
    return cards
