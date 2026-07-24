"""Unit tests for embedding module — mock client only."""
import pytest
from app.llm.embedding import MockEmbeddingClient, get_embedding_client


class TestMockEmbeddingClient:
    @pytest.mark.asyncio
    async def test_embed_query_returns_correct_dim(self):
        client = MockEmbeddingClient(dim=1536)
        vec = await client.embed_query("hello")
        assert len(vec) == 1536
        # Should be normalised (length ≈ 1.0)
        norm = sum(v * v for v in vec)
        assert abs(norm - 1.0) < 0.0001

    @pytest.mark.asyncio
    async def test_embed_documents_batch(self):
        client = MockEmbeddingClient(dim=128)
        vecs = await client.embed_documents(["a", "b", "c"])
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == 128

    @pytest.mark.asyncio
    async def test_different_inputs_different_vectors(self):
        client = MockEmbeddingClient(dim=8)
        v1 = await client.embed_query("hello")
        v2 = await client.embed_query("world")
        # Extremely unlikely to be identical with random
        assert v1 != v2


class TestFactory:
    def test_no_api_key_returns_mock(self):
        client = get_embedding_client(api_key="")
        assert isinstance(client, MockEmbeddingClient)

    def test_with_api_key_returns_real(self):
        # Even with a fake key, should return real client (import check only)
        client = get_embedding_client(api_key="sk-fake-xxx")
        from app.llm.embedding import EmbeddingClient
        assert isinstance(client, EmbeddingClient)
