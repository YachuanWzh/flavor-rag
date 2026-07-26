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


@pytest.mark.asyncio
async def test_query_embedding_uses_bounded_attempts_and_cache(monkeypatch):
    from app.llm import embedding as embedding_module

    embedding_module._query_cache.clear()
    client = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    calls = []

    async def fake_call(texts, *, timeout_sec=120.0, max_attempts=3):
        calls.append((list(texts), timeout_sec, max_attempts))
        return [[0.25, 0.75]]

    monkeypatch.setattr(client, "_call_with_retry", fake_call)
    monkeypatch.setattr(
        embedding_module.settings,
        "embedding_query_timeout_sec",
        7.0,
    )
    monkeypatch.setattr(
        embedding_module.settings,
        "embedding_query_max_attempts",
        1,
    )

    first = await client.embed_query("cache-me")
    second = await client.embed_query("cache-me")

    assert first == second == [0.25, 0.75]
    assert calls == [(["cache-me"], 7.0, 1)]
