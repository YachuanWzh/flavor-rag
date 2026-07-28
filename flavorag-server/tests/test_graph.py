"""Unit tests for LightRAG client."""
from types import SimpleNamespace

import pytest
from app.rag.graph.lightrag_client import LightRAGClient
from app.config.settings import settings


class TestLightRAGClient:
    @pytest.mark.asyncio
    async def test_query_graph_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "graph_enabled", False)
        client = LightRAGClient()
        result = await client.query_graph("test")
        assert result == {"disabled": True, "results": []}

    @pytest.mark.asyncio
    async def test_insert_document_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "graph_enabled", False)
        client = LightRAGClient()
        result = await client.insert_document("kb1", "content")
        assert result == {"disabled": True}

    def test_query_references_are_normalized_and_scoped(self):
        result = LightRAGClient._normalise_query_response(
            {
                "references": [
                    {
                        "reference_id": "internal-1",
                        "file_path": "kb_demo_doc-42",
                        "content": ["first chunk", "second chunk"],
                    },
                    {
                        "reference_id": "other",
                        "file_path": "another_kb_doc-9",
                        "content": ["must not leak"],
                    },
                ]
            },
            scope_tokens=["kb_demo"],
            top_k=5,
        )
        assert [item["content"] for item in result] == [
            "first chunk",
            "second chunk",
        ]
        assert all(item["doc_id"] == "doc-42" for item in result)

    def test_graph_view_filters_edges_with_filtered_nodes(self):
        result = LightRAGClient._normalise_graph_response(
            {
                "nodes": [
                    {
                        "id": "1",
                        "labels": ["A"],
                        "properties": {
                            "file_path": "kb_demo_doc-42",
                            "entity_id": "Agent",
                            "entity_type": "concept",
                        },
                    },
                    {
                        "id": "2",
                        "labels": ["B"],
                        "properties": {
                            "file_path": "other_doc-9",
                            "entity_id": "Secret",
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "1", "target": "2", "properties": {}}
                ],
            },
            scope_tokens=["kb_demo"],
            limit=50,
        )
        assert [node["name"] for node in result["nodes"]] == ["Agent"]
        assert result["edges"] == []


class TestImport:
    def test_module_importable(self):
        from app.rag.graph import lightrag_client
        assert hasattr(lightrag_client, "LightRAGClient")


@pytest.mark.asyncio
async def test_graph_view_degrades_when_neo4j_is_unavailable(monkeypatch):
    from app.api import graph as graph_module

    async def fake_require_kb(*_args, **_kwargs):
        return SimpleNamespace(id="kb-1", collection_name="collection-1")

    class UnavailableNeo4j:
        async def fetch_graph(self, **_kwargs):
            raise RuntimeError("neo4j unavailable")

    class AvailableLightRAG:
        async def fetch_graph(self, **_kwargs):
            return {
                "nodes": [{"id": "node-1", "name": "Flavor"}],
                "edges": [],
                "truncated": False,
            }

    monkeypatch.setattr(graph_module, "require_kb", fake_require_kb)
    monkeypatch.setattr(graph_module, "principal_from_user", lambda _user: object())
    monkeypatch.setattr(graph_module, "Neo4jGraphStore", UnavailableNeo4j)
    monkeypatch.setattr(graph_module, "LightRAGClient", AvailableLightRAG)
    monkeypatch.setattr(graph_module._log, "warning", lambda *args, **kwargs: None)

    result = await graph_module.graph_view(
        kb_id="kb-1",
        entity="*",
        depth=2,
        limit=80,
        db=None,
        user=object(),
    )

    assert result["code"] == "0"
    assert result["data"]["nodes"] == [{"id": "node-1", "name": "Flavor"}]
