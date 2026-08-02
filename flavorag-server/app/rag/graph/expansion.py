"""Interactive graph subgraph expansion — neighbor traversal and merge logic."""
from __future__ import annotations

from collections import deque


def expand_neighbors(
    graph: dict,
    *,
    node_id: str,
    depth: int = 1,
    limit: int = 20,
    exclude_ids: set[str] | None = None,
) -> dict:
    """BFS from node_id up to depth hops, returning discovered neighbors.

    The source node itself is NOT included in the result — only newly
    discovered neighbors and their connecting edges.
    """
    excluded = exclude_ids or set()
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])

    if node_id not in nodes_by_id:
        return {"nodes": [], "edges": []}

    # Build adjacency
    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        adjacency.setdefault(src, []).append((tgt, edge))
        adjacency.setdefault(tgt, []).append((src, edge))

    # BFS
    visited: set[str] = {node_id}
    frontier = deque([node_id])
    discovered_nodes: list[dict] = []
    discovered_edges: list[dict] = []
    seen_edges: set[int] = set()
    current_depth = 0

    while frontier and current_depth < depth:
        next_frontier: deque[str] = deque()
        while frontier:
            current = frontier.popleft()
            for neighbor_id, edge in adjacency.get(current, []):
                edge_idx = id(edge)
                if edge_idx not in seen_edges:
                    seen_edges.add(edge_idx)
                    if neighbor_id not in excluded and neighbor_id not in visited:
                        discovered_edges.append(edge)
                if neighbor_id in visited or neighbor_id in excluded:
                    continue
                visited.add(neighbor_id)
                if neighbor_id in nodes_by_id:
                    discovered_nodes.append(nodes_by_id[neighbor_id])
                next_frontier.append(neighbor_id)
                if len(discovered_nodes) >= limit:
                    break
            if len(discovered_nodes) >= limit:
                break
        frontier = next_frontier
        current_depth += 1
        if len(discovered_nodes) >= limit:
            break

    return {"nodes": discovered_nodes[:limit], "edges": discovered_edges}


def mark_expandable(graph: dict, visible_ids: set[str]) -> list[dict]:
    """Annotate nodes with expandable=True if they have non-visible neighbors."""
    edges = graph.get("edges", [])
    neighbor_map: dict[str, set[str]] = {}
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        neighbor_map.setdefault(src, set()).add(tgt)
        neighbor_map.setdefault(tgt, set()).add(src)

    result = []
    for node in graph.get("nodes", []):
        nid = node["id"]
        neighbors = neighbor_map.get(nid, set())
        has_hidden = any(n not in visible_ids for n in neighbors)
        result.append({**node, "expandable": has_hidden})
    return result


def merge_graph_views(existing: dict, incoming: dict) -> dict:
    """Merge incoming graph fragment into existing view without duplicates."""
    seen_node_ids = {n["id"] for n in existing.get("nodes", [])}
    seen_edge_keys = {
        (e.get("source"), e.get("target"), e.get("relation", ""))
        for e in existing.get("edges", [])
    }

    merged_nodes = list(existing.get("nodes", []))
    for node in incoming.get("nodes", []):
        if node["id"] not in seen_node_ids:
            merged_nodes.append(node)
            seen_node_ids.add(node["id"])

    merged_edges = list(existing.get("edges", []))
    for edge in incoming.get("edges", []):
        key = (edge.get("source"), edge.get("target"), edge.get("relation", ""))
        if key not in seen_edge_keys:
            merged_edges.append(edge)
            seen_edge_keys.add(key)

    return {"nodes": merged_nodes, "edges": merged_edges}
