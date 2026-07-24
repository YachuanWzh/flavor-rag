"""LightRAG client — wraps the LightRAG HTTP API for graph-based retrieval."""
from __future__ import annotations

import httpx

from app.config.settings import settings


class LightRAGClient:
    """HTTP client for LightRAG graph service.

    Base URL is read from ``settings.lightrag_base_url``.
    All methods return empty results when ``settings.graph_enabled`` is False.
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.lightrag_base_url).rstrip("/")

    # ---- guard ----

    @staticmethod
    def _disabled() -> bool:
        return not settings.graph_enabled

    # ---- document ingestion ----

    async def insert_document(self, kb_id: str, content: str) -> dict:
        """Insert a document (or chunk) into the LightRAG knowledge graph.

        Args:
            kb_id: Knowledge-base / namespace identifier.
            content: Plain-text content to insert.

        Returns:
            API response dict, or ``{"disabled": True}`` when ``graph_enabled=False``.
        """
        if self._disabled():
            return {"disabled": True}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/documents",
                json={"kb_id": kb_id, "content": content},
            )
            resp.raise_for_status()
            return resp.json()

    async def insert_documents_batch(
        self, kb_id: str, contents: list[dict]
    ) -> list[dict]:
        """Insert multiple document chunks in a batch.

        Args:
            kb_id: Namespace identifier.
            contents: List of dicts, each with at least ``{"content": "..."}``.

        Returns:
            List of API response dicts.
        """
        if self._disabled():
            return [{"disabled": True} for _ in contents]

        results: list[dict] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for item in contents:
                resp = await client.post(
                    f"{self.base_url}/documents",
                    json={"kb_id": kb_id, **item},
                )
                resp.raise_for_status()
                results.append(resp.json())
        return results

    # ---- graph query ----

    async def query_graph(
        self,
        query: str,
        mode: str = "local",
        top_k: int = 5,
    ) -> dict:
        """Query the LightRAG knowledge graph.

        Args:
            query: Search query string.
            mode: Retrieval mode (``"local"``, ``"global"``, or ``"hybrid"``).
            top_k: Maximum number of graph nodes to retrieve.

        Returns:
            Graph query result dict, or ``{"disabled": True, "results": []}``
            when ``graph_enabled=False``.
        """
        if self._disabled():
            return {"disabled": True, "results": []}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/query",
                json={
                    "query": query,
                    "mode": mode,
                    "top_k": top_k,
                },
            )
            resp.raise_for_status()
            return resp.json()

    # ---- health / status ----

    async def health(self) -> dict:
        """Check whether the LightRAG service is reachable."""
        if self._disabled():
            return {"disabled": True}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.json()
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}
