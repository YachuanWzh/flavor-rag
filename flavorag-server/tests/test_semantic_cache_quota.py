"""Tests for F2: Semantic cache and token quota enforcement."""
from __future__ import annotations

import time

import pytest

# ─── F2.1 Semantic Cache ───


def test_semantic_cache_config_defaults():
    from app.config.settings import settings

    assert settings.semantic_cache_enabled is False
    assert settings.semantic_cache_threshold == 0.96
    assert settings.semantic_cache_ttl_sec == 3600


def test_cached_answer_dataclass():
    from app.rag.semantic_cache import CachedAnswer

    answer = CachedAnswer(
        content="test answer",
        sources=[{"chunkId": "c1"}],
        model="qwen-plus-latest",
        cached_at=time.time(),
    )
    assert answer.content == "test answer"
    assert answer.sources == [{"chunkId": "c1"}]


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache(backend="memory")
    result = await cache.get(
        query_embedding=[0.1] * 8,
        tenant_id="default",
        kb_scope="kb1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_cache_put_and_hit():
    from app.rag.semantic_cache import CachedAnswer, SemanticCache

    cache = SemanticCache(backend="memory")
    embedding = [1.0, 0.0, 0.0, 0.0]
    answer = CachedAnswer(
        content="hello world",
        sources=[],
        model="test",
        cached_at=time.time(),
    )
    await cache.put(
        query_embedding=embedding,
        tenant_id="default",
        kb_scope="kb1",
        answer=answer,
    )
    # Exact same embedding → similarity 1.0 ≥ threshold
    hit = await cache.get(
        query_embedding=embedding,
        tenant_id="default",
        kb_scope="kb1",
    )
    assert hit is not None
    assert hit.content == "hello world"


@pytest.mark.asyncio
async def test_cache_threshold_boundary():
    from app.rag.semantic_cache import CachedAnswer, SemanticCache

    cache = SemanticCache(backend="memory", threshold=0.96)
    stored = [1.0, 0.0, 0.0, 0.0]
    await cache.put(
        query_embedding=stored,
        tenant_id="t",
        kb_scope="kb",
        answer=CachedAnswer(content="x", sources=[], model="m", cached_at=time.time()),
    )
    # Cosine similarity of [1,0,0,0] and [0.95, 0.31, 0, 0] ≈ 0.95 < 0.96
    miss = await cache.get(
        query_embedding=[0.95, 0.31225, 0.0, 0.0],
        tenant_id="t",
        kb_scope="kb",
    )
    assert miss is None
    # Cosine similarity of [1,0,0,0] and [0.99, 0.14, 0, 0] ≈ 0.99 ≥ 0.96
    hit = await cache.get(
        query_embedding=[0.99, 0.1411, 0.0, 0.0],
        tenant_id="t",
        kb_scope="kb",
    )
    assert hit is not None


@pytest.mark.asyncio
async def test_cache_ttl_expiry():
    from app.rag.semantic_cache import CachedAnswer, SemanticCache

    cache = SemanticCache(backend="memory", ttl_sec=0)
    embedding = [1.0, 0.0]
    await cache.put(
        query_embedding=embedding,
        tenant_id="t",
        kb_scope="kb",
        answer=CachedAnswer(content="x", sources=[], model="m", cached_at=time.time()),
    )
    # TTL=0 means immediately expired
    hit = await cache.get(query_embedding=embedding, tenant_id="t", kb_scope="kb")
    assert hit is None


@pytest.mark.asyncio
async def test_cache_invalidate_kb():
    from app.rag.semantic_cache import CachedAnswer, SemanticCache

    cache = SemanticCache(backend="memory")
    embedding = [1.0, 0.0]
    await cache.put(
        query_embedding=embedding,
        tenant_id="t",
        kb_scope="kb1",
        answer=CachedAnswer(content="a", sources=[], model="m", cached_at=time.time()),
    )
    await cache.put(
        query_embedding=embedding,
        tenant_id="t",
        kb_scope="kb2",
        answer=CachedAnswer(content="b", sources=[], model="m", cached_at=time.time()),
    )
    await cache.invalidate_kb("t", "kb1")
    assert await cache.get(query_embedding=embedding, tenant_id="t", kb_scope="kb1") is None
    assert await cache.get(query_embedding=embedding, tenant_id="t", kb_scope="kb2") is not None


@pytest.mark.asyncio
async def test_cache_tenant_isolation():
    from app.rag.semantic_cache import CachedAnswer, SemanticCache

    cache = SemanticCache(backend="memory")
    embedding = [1.0, 0.0]
    await cache.put(
        query_embedding=embedding,
        tenant_id="tenant-a",
        kb_scope="kb",
        answer=CachedAnswer(content="secret", sources=[], model="m", cached_at=time.time()),
    )
    hit = await cache.get(query_embedding=embedding, tenant_id="tenant-b", kb_scope="kb")
    assert hit is None


# ─── F2.2 Token Quota ───


def test_token_quota_config_defaults():
    from app.config.settings import settings

    assert settings.token_quota_enabled is False
    assert settings.token_quota_daily_default == 1_000_000


@pytest.mark.asyncio
async def test_quota_allows_under_limit():
    from app.rag.quota import TokenQuota

    quota = TokenQuota(backend="memory", daily_default=1000)
    status = await quota.check("tenant-a")
    assert status.allowed is True
    assert status.remaining == 1000


@pytest.mark.asyncio
async def test_quota_records_and_limits():
    from app.rag.quota import TokenQuota

    quota = TokenQuota(backend="memory", daily_default=100)
    await quota.record("tenant-a", prompt_tokens=60, completion_tokens=30)
    status = await quota.check("tenant-a")
    assert status.allowed is True
    assert status.remaining == 10

    await quota.record("tenant-a", prompt_tokens=20, completion_tokens=0)
    status = await quota.check("tenant-a")
    assert status.allowed is False
    assert status.remaining == 0


@pytest.mark.asyncio
async def test_quota_tenant_isolation():
    from app.rag.quota import TokenQuota

    quota = TokenQuota(backend="memory", daily_default=100)
    await quota.record("tenant-a", prompt_tokens=100, completion_tokens=0)
    status_b = await quota.check("tenant-b")
    assert status_b.allowed is True
    assert status_b.remaining == 100


@pytest.mark.asyncio
async def test_quota_reset_on_new_day():
    from app.rag.quota import TokenQuota

    quota = TokenQuota(backend="memory", daily_default=100)
    await quota.record("t", prompt_tokens=100, completion_tokens=0)
    # Simulate day change
    quota._usage.clear()
    status = await quota.check("t")
    assert status.allowed is True
    assert status.remaining == 100
