"""Embedding client — OpenAI-compatible API + local mock fallback."""
from __future__ import annotations

import asyncio
import math
import random
from collections import OrderedDict

import httpx
from app.config.logging_config import get_logger
from app.config.settings import settings

_BATCH_SIZE = 16
_QUERY_CACHE_MAX_SIZE = 256
_query_cache: OrderedDict[tuple[str, str, str], list[float]] = OrderedDict()
_query_cache_lock = asyncio.Lock()
_log = get_logger("flavorag.embedding")


class EmbeddingClient:
    """OpenAI-compatible embedding API client via HTTPX."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.siliconflow_api_key
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.dim = settings.embedding_dim

    async def embed_query(self, text: str) -> list[float]:
        cache_key = (self.base_url, self.model, text)
        async with _query_cache_lock:
            cached = _query_cache.get(cache_key)
            if cached is not None:
                _query_cache.move_to_end(cache_key)
                return list(cached)

        results = await self._call_with_retry(
            [text],
            timeout_sec=settings.embedding_query_timeout_sec,
            max_attempts=settings.embedding_query_max_attempts,
        )
        vector = results[0]
        async with _query_cache_lock:
            _query_cache[cache_key] = list(vector)
            _query_cache.move_to_end(cache_key)
            while len(_query_cache) > _QUERY_CACHE_MAX_SIZE:
                _query_cache.popitem(last=False)
        return vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            vectors = await self._call_with_retry(batch)
            all_vectors.extend(vectors)
            if i + _BATCH_SIZE < len(texts):
                await asyncio.sleep(0.05)
        return all_vectors

    async def _call_with_retry(
        self,
        texts: list[str],
        *,
        timeout_sec: float = 120.0,
        max_attempts: int = 3,
    ) -> list[list[float]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}

        last_err: Exception | None = None
        attempts = max(1, max_attempts)
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    resp = await client.post(
                        f"{self.base_url}/embeddings",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    items = sorted(data["data"], key=lambda x: x["index"])
                    vectors = [item["embedding"] for item in items]
                    if vectors:
                        self.dim = len(vectors[0])
                    return vectors
            except Exception as e:
                last_err = e
                error_detail = str(e) or type(e).__name__
                _log.warning(
                    "embedding_attempt_failed",
                    attempt=attempt + 1,
                    max_attempts=attempts,
                    model=self.model,
                    error_type=type(e).__name__,
                    error=error_detail[:300],
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.0 * (attempt + 1))

        error_detail = (
            str(last_err) or type(last_err).__name__
            if last_err is not None
            else "unknown error"
        )
        raise RuntimeError(
            f"Embedding failed after {attempts} attempt(s): {error_detail}"
        )


class MockEmbeddingClient:
    """Local deterministic vectors for repeatable development and tests."""

    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embedding_dim

    async def embed_query(self, text: str) -> list[float]:
        return self._vector_for(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for(text) for text in texts]

    def _vector_for(self, text: str) -> list[float]:
        import hashlib

        seed = int.from_bytes(
            hashlib.sha256(text.encode("utf-8")).digest()[:8], "big"
        )
        rng = random.Random(seed)
        raw = [rng.gauss(0, 1) for _ in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in raw))
        if norm == 0:
            return [0.0] * self.dim
        return [v / norm for v in raw]


def get_embedding_client(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> EmbeddingClient | MockEmbeddingClient:
    key = api_key if api_key is not None else settings.siliconflow_api_key
    if not key:
        return MockEmbeddingClient()
    return EmbeddingClient(api_key=key, base_url=base_url, model=model)
