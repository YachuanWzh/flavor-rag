"""Tests for F6: Knowledge graph interactive subgraph expansion."""
from __future__ import annotations

# ─── F6.1 Neighbor expansion logic ───


def test_expand_neighbors_basic():
    from app.rag.graph.expansion import expand_neighbors

    graph = {
        "nodes": [
            {"id": "n1", "name": "Python"},
            {"id": "n2", "name": "Java"},
            {"id": "n3", "name": "Go"},
            {"id": "n4", "name": "Rust"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "relation": "similar"},
            {"source": "n1", "target": "n3", "relation": "similar"},
            {"source": "n3", "target": "n4", "relation": "alternative"},
        ],
    }
    result = expand_neighbors(graph, node_id="n1", depth=1, limit=10)
    # n1's neighbors are n2 and n3
    neighbor_ids = {n["id"] for n in result["nodes"]}
    assert "n2" in neighbor_ids
    assert "n3" in neighbor_ids
    # n4 is depth 2 from n1, should not be included at depth=1
    assert "n4" not in neighbor_ids


def test_expand_neighbors_depth_2():
    from app.rag.graph.expansion import expand_neighbors

    graph = {
        "nodes": [
            {"id": "n1", "name": "A"},
            {"id": "n2", "name": "B"},
            {"id": "n3", "name": "C"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "relation": "r"},
            {"source": "n2", "target": "n3", "relation": "r"},
        ],
    }
    result = expand_neighbors(graph, node_id="n1", depth=2, limit=10)
    ids = {n["id"] for n in result["nodes"]}
    assert ids == {"n2", "n3"}


def test_expand_neighbors_exclude_ids():
    from app.rag.graph.expansion import expand_neighbors

    graph = {
        "nodes": [
            {"id": "n1", "name": "A"},
            {"id": "n2", "name": "B"},
            {"id": "n3", "name": "C"},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "relation": "r"},
            {"source": "n1", "target": "n3", "relation": "r"},
        ],
    }
    result = expand_neighbors(
        graph, node_id="n1", depth=1, limit=10, exclude_ids={"n2"}
    )
    ids = {n["id"] for n in result["nodes"]}
    assert "n2" not in ids
    assert "n3" in ids


def test_expand_neighbors_limit():
    from app.rag.graph.expansion import expand_neighbors

    graph = {
        "nodes": [{"id": f"n{i}", "name": f"N{i}"} for i in range(10)],
        "edges": [{"source": "n0", "target": f"n{i}", "relation": "r"} for i in range(1, 10)],
    }
    result = expand_neighbors(graph, node_id="n0", depth=1, limit=3)
    assert len(result["nodes"]) <= 3


def test_expand_neighbors_unknown_node():
    from app.rag.graph.expansion import expand_neighbors

    graph = {"nodes": [{"id": "n1", "name": "A"}], "edges": []}
    result = expand_neighbors(graph, node_id="nonexistent", depth=1, limit=10)
    assert result["nodes"] == []
    assert result["edges"] == []


def test_expandable_flag():
    from app.rag.graph.expansion import mark_expandable

    graph = {
        "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    visible_ids = {"n1", "n2"}
    result = mark_expandable(graph, visible_ids)
    # n2 has neighbor n3 not in visible → expandable
    expandable_ids = {n["id"] for n in result if n.get("expandable")}
    assert "n2" in expandable_ids
    # n1's only neighbor n2 is already visible → not expandable
    assert "n1" not in expandable_ids


# ─── F6.2 Frontend merge logic ───


def test_merge_graph_incremental():
    from app.rag.graph.expansion import merge_graph_views

    existing = {
        "nodes": [{"id": "n1", "name": "A"}],
        "edges": [],
    }
    incoming = {
        "nodes": [{"id": "n2", "name": "B"}, {"id": "n1", "name": "A"}],
        "edges": [{"source": "n1", "target": "n2", "relation": "r"}],
    }
    merged = merge_graph_views(existing, incoming)
    # No duplicate nodes
    ids = [n["id"] for n in merged["nodes"]]
    assert ids.count("n1") == 1
    assert "n2" in ids
    assert len(merged["edges"]) == 1
