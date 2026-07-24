"""Embedding client — OpenAI-compatible API + local mock fallback."""
from __future__ import annotations

import math
import random

import httpx
from app.config.settings import settings


class EmbeddingClient:
    """OpenAI-compatible embedding API client via HTTPX."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or settings.siliconflow_api_key
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.dim = settings.embedding_dim

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single text (e.g., a user query)."""
        results = await self.embed_documents([text])
        return results[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            # Sort by index to preserve input order
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]


class MockEmbeddingClient:
    """Local mock that returns random normalised vectors when no API key is set."""

    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embedding_dim

    async def embed_query(self, text: str) -> list[float]:
        _ = text
        return self._random_vector()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._random_vector() for _ in texts]

    def _random_vector(self) -> list[float]:
        raw = [random.gauss(0, 1) for _ in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in raw))
        if norm == 0:
            return [0.0] * self.dim
        return [v / norm for v in raw]


def get_embedding_client(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> EmbeddingClient | MockEmbeddingClient:
    """Factory: return real client when an API key is present, else mock."""
    key = api_key or settings.siliconflow_api_key
    if not key:
        return MockEmbeddingClient()
    return EmbeddingClient(api_key=key, base_url=base_url, model=model)
