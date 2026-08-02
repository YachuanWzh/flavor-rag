"""Unit tests for embedding module — mock client only."""
import asyncio
import httpx
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

    def test_legacy_qwen_model_alias_is_canonicalized(self):
        client = get_embedding_client(
            api_key="sk-fake-xxx",
            model="qwen3-embedding-8b",
        )

        assert client.model == "Qwen/Qwen3-Embedding-8B"

    def test_missing_model_uses_configured_default(self):
        from app.config.settings import settings

        client = get_embedding_client(api_key="sk-fake-xxx", model=None)

        assert client.model == settings.embedding_model


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


@pytest.mark.asyncio
async def test_concurrent_query_embeddings_share_one_provider_call(monkeypatch):
    from app.llm import embedding as embedding_module

    embedding_module._query_cache.clear()
    if hasattr(embedding_module, "_query_inflight"):
        embedding_module._query_inflight.clear()
    first = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    second = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    calls = 0
    release = asyncio.Event()

    async def fake_call(texts, *, timeout_sec=120.0, max_attempts=3):
        nonlocal calls
        calls += 1
        await release.wait()
        return [[0.25, 0.75]]

    monkeypatch.setattr(first, "_call_with_retry", fake_call)
    monkeypatch.setattr(second, "_call_with_retry", fake_call)

    tasks = [
        asyncio.create_task(first.embed_query("shared-query")),
        asyncio.create_task(second.embed_query("shared-query")),
    ]
    await asyncio.sleep(0)
    release.set()
    vectors = await asyncio.gather(*tasks)

    assert vectors == [[0.25, 0.75], [0.25, 0.75]]
    assert calls == 1


@pytest.mark.asyncio
async def test_cancelled_embedding_waiter_does_not_cancel_shared_request(monkeypatch):
    from app.llm import embedding as embedding_module

    embedding_module._query_cache.clear()
    embedding_module._query_inflight.clear()
    client = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    release = asyncio.Event()
    calls = 0

    async def fake_call(texts, *, timeout_sec=120.0, max_attempts=3):
        nonlocal calls
        calls += 1
        await release.wait()
        return [[0.25, 0.75]]

    monkeypatch.setattr(client, "_call_with_retry", fake_call)
    cancelled = asyncio.create_task(client.embed_query("shared-cancel"))
    survivor = asyncio.create_task(client.embed_query("shared-cancel"))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()

    assert await survivor == [0.25, 0.75]
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_inflight_embedding_is_evicted_for_retry(monkeypatch):
    from app.llm import embedding as embedding_module

    embedding_module._query_cache.clear()
    embedding_module._query_inflight.clear()
    client = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    calls = 0

    async def flaky_call(texts, *, timeout_sec=120.0, max_attempts=3):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider failure")
        return [[0.25, 0.75]]

    monkeypatch.setattr(client, "_call_with_retry", flaky_call)

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await client.embed_query("retry-after-failure")
    await asyncio.sleep(0)

    assert await client.embed_query("retry-after-failure") == [0.25, 0.75]
    assert calls == 2


@pytest.mark.asyncio
async def test_embedding_retries_transient_timeout(monkeypatch):
    from app.llm import embedding as embedding_module

    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.25, 0.75]}]}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("")
            return FakeResponse()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(embedding_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(embedding_module._log, "warning", lambda *args, **kwargs: None)

    client = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    vectors = await client._call_with_retry(
        ["retry-me"],
        timeout_sec=1.0,
        max_attempts=2,
    )

    assert vectors == [[0.25, 0.75]]
    assert calls == 2


@pytest.mark.asyncio
async def test_embedding_error_preserves_empty_exception_type(monkeypatch):
    from app.llm import embedding as embedding_module

    class AlwaysTimeoutClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(
        embedding_module.httpx,
        "AsyncClient",
        AlwaysTimeoutClient,
    )
    monkeypatch.setattr(embedding_module._log, "warning", lambda *args, **kwargs: None)

    client = embedding_module.EmbeddingClient(
        api_key="test-key",
        base_url="https://embedding.invalid/v1",
        model="test-model",
    )
    with pytest.raises(RuntimeError, match="ReadTimeout"):
        await client._call_with_retry(
            ["fail-me"],
            timeout_sec=1.0,
            max_attempts=1,
        )
