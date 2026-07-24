"""Elasticsearch BM25 keyword search channel."""
from __future__ import annotations

from app.rag.search.base import SearchChannel, SearchResult
from app.config.settings import settings


class ESKeywordSearchChannel(SearchChannel):
    """BM25 full-text search via Elasticsearch."""

    INDEX_NAME = "rag_keyword_store"

    def __init__(self):
        self._es = None
        self._initialized = False

    async def _get_es(self):
        if self._es is not None:
            return self._es
        try:
            from elasticsearch import AsyncElasticsearch
            self._es = AsyncElasticsearch(settings.es_uris, request_timeout=10)
            self._initialized = True
        except Exception:
            self._es = None
        return self._es

    @property
    def enabled(self) -> bool:
        return bool(settings.es_enabled)

    async def search(
        self, query: str, collection_name: str, top_k: int = 10
    ) -> list[SearchResult]:
        if not self.enabled:
            return []

        es = await self._get_es()
        if es is None:
            return []

        try:
            body = {
                "query": {
                    "bool": {
                        "must": [{"match": {"content": query}}],
                        "filter": [{"term": {"kb_id": collection_name}}],
                    }
                },
                "size": top_k,
            }
            resp = await es.search(index=self.INDEX_NAME, body=body)
            results: list[SearchResult] = []
            for hit in resp["hits"]["hits"]:
                src = hit["_source"]
                results.append(SearchResult(
                    chunk_id=hit["_id"],
                    doc_id=src.get("doc_id", ""),
                    content=src.get("content", ""),
                    score=float(hit["_score"]),
                ))
            return results
        except Exception:
            return []

    async def insert(self, chunks: list[dict], kb_id: str):
        """Bulk-index documents into ES."""
        if not self.enabled:
            return

        es = await self._get_es()
        if es is None:
            return

        try:
            from elasticsearch.helpers import async_bulk
            actions = [
                {
                    "_index": self.INDEX_NAME,
                    "_id": c["id"],
                    "_source": {
                        "kb_id": kb_id,
                        "content": c["content"],
                        "chunk_index": c.get("chunk_index", 0),
                    },
                }
                for c in chunks
            ]
            await async_bulk(es, actions)
        except Exception:
            pass
