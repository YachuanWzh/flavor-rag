"""Unit tests for LightRAG client."""
import pytest
from app.rag.graph.lightrag_client import LightRAGClient


class TestLightRAGClient:
    @pytest.mark.asyncio
    async def test_query_graph_disabled_returns_empty(self):
        client = LightRAGClient()
        result = await client.query_graph("test")
        assert result == {"disabled": True, "results": []}

    @pytest.mark.asyncio
    async def test_insert_document_disabled(self):
        client = LightRAGClient()
        result = await client.insert_document("kb1", "content")
        assert result == {"disabled": True}


class TestImport:
    def test_module_importable(self):
        from app.rag.graph import lightrag_client
        assert hasattr(lightrag_client, "LightRAGClient")
