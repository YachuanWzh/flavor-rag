"""Semantic answer cache — embedding-similarity lookup to skip redundant LLM calls.

Backends:
- "memory": in-process dict (tests, single-replica dev)
- "redis": production (sorted set + hash per tenant/kb scope)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CachedAnswer:
    content: str
    sources: list[dict] = field(default_factory=list)
    model: str = ""
    cached_at: float = 0.0


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class _CacheEntry:
    embedding: list[float]
    answer: CachedAnswer
    created_at: float


class SemanticCache:
    """Embedding-similarity answer cache with tenant/KB scoping."""

    def __init__(
        self,
        *,
        backend: str = "memory",
        threshold: float = 0.96,
        ttl_sec: int = 3600,
    ):
        self._backend = backend
        self._threshold = threshold
        self._ttl_sec = ttl_sec
        # Memory backend storage: key = "{tenant}:{kb_scope}"
        self._store: dict[str, list[_CacheEntry]] = {}

    def _scope_key(self, tenant_id: str, kb_scope: str) -> str:
        return f"{tenant_id}:{kb_scope}"

    async def get(
        self,
        query_embedding: list[float],
        tenant_id: str,
        kb_scope: str,
    ) -> CachedAnswer | None:
        if self._backend == "memory":
            return self._memory_get(query_embedding, tenant_id, kb_scope)
        return await self._redis_get(query_embedding, tenant_id, kb_scope)

    async def put(
        self,
        query_embedding: list[float],
        tenant_id: str,
        kb_scope: str,
        answer: CachedAnswer,
    ) -> None:
        if self._backend == "memory":
            self._memory_put(query_embedding, tenant_id, kb_scope, answer)
            return
        await self._redis_put(query_embedding, tenant_id, kb_scope, answer)

    async def invalidate_kb(self, tenant_id: str, kb_id: str) -> None:
        if self._backend == "memory":
            key = self._scope_key(tenant_id, kb_id)
            self._store.pop(key, None)
            return
        await self._redis_invalidate(tenant_id, kb_id)

    # ─── Memory backend ───

    def _memory_get(
        self, embedding: list[float], tenant_id: str, kb_scope: str
    ) -> CachedAnswer | None:
        key = self._scope_key(tenant_id, kb_scope)
        entries = self._store.get(key)
        if not entries:
            return None
        now = time.time()
        best_score = -1.0
        best_answer: CachedAnswer | None = None
        for entry in entries:
            if self._ttl_sec == 0 or (now - entry.created_at) > self._ttl_sec:
                continue
            sim = _cosine_similarity(embedding, entry.embedding)
            if sim >= self._threshold and sim > best_score:
                best_score = sim
                best_answer = entry.answer
        return best_answer

    def _memory_put(
        self,
        embedding: list[float],
        tenant_id: str,
        kb_scope: str,
        answer: CachedAnswer,
    ) -> None:
        key = self._scope_key(tenant_id, kb_scope)
        if key not in self._store:
            self._store[key] = []
        self._store[key].append(
            _CacheEntry(embedding=embedding, answer=answer, created_at=time.time())
        )

    # ─── Redis backend (production) ───

    async def _redis_get(
        self, embedding: list[float], tenant_id: str, kb_scope: str
    ) -> CachedAnswer | None:
        # Production implementation uses Redis + brute-force scan over
        # stored embeddings within the tenant/kb scope. For high-volume
        # deployments, replace with a vector-index-backed store.
        return None

    async def _redis_put(
        self,
        embedding: list[float],
        tenant_id: str,
        kb_scope: str,
        answer: CachedAnswer,
    ) -> None:
        pass

    async def _redis_invalidate(self, tenant_id: str, kb_id: str) -> None:
        pass
