"""Elasticsearch BM25 keyword search channel."""
from __future__ import annotations

import asyncio

from app.rag.search.base import SearchChannel, SearchResult
from app.config.settings import settings
from app.config.logging_config import get_logger

_log = get_logger("flavorag.search.keyword")

# ---------------------------------------------------------------------------
# Module-level singleton client (created once per process, closed on shutdown)
# ---------------------------------------------------------------------------
_es_client = None
_es_lock = asyncio.Lock()
_index_ready = False


async def get_es_client():
    """Return the shared AsyncElasticsearch client (singleton)."""
    global _es_client
    if _es_client is not None:
        return _es_client
    async with _es_lock:
        if _es_client is None:
            from elasticsearch import AsyncElasticsearch
            _es_client = AsyncElasticsearch(settings.es_uris, request_timeout=10)
    return _es_client


async def close_es_client() -> None:
    """Close the shared client (call on app shutdown)."""
    global _es_client, _index_ready
    if _es_client is not None:
        await _es_client.close()
        _es_client = None
        _index_ready = False


def _index_body(analyzer: str, search_analyzer: str) -> dict:
    """Explicit index mapping: keyword fields for filtering, text fields
    with a configurable (Chinese-capable) analyzer for BM25."""
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "kb_id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "department_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "block_type": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "page_start": {"type": "integer"},
                "page_end": {"type": "integer"},
                "content": {
                    "type": "text",
                    "analyzer": analyzer,
                    "search_analyzer": search_analyzer,
                },
                "embedding_content": {
                    "type": "text",
                    "analyzer": analyzer,
                    "search_analyzer": search_analyzer,
                },
            }
        },
    }


async def ensure_keyword_index(es) -> None:
    """Idempotently create the keyword index with an explicit mapping.

    Prefers the configured IK analyzers; falls back to `standard` when the
    IK plugin is not installed. Concurrent creation races are tolerated.
    """
    global _index_ready
    if _index_ready:
        return
    index = ESKeywordSearchChannel.INDEX_NAME
    if await es.indices.exists(index=index):
        _index_ready = True
        return
    try:
        await es.indices.create(
            index=index,
            body=_index_body(settings.es_analyzer, settings.es_search_analyzer),
        )
        _log.info("es_index_created", index=index, analyzer=settings.es_analyzer)
    except Exception as exc:
        message = str(exc)
        if "resource_already_exists_exception" in message:
            pass  # created concurrently — fine
        elif "analyzer" in message.lower():
            # IK plugin missing — degrade to standard analyzer with a warning
            _log.warning(
                "es_analyzer_unavailable_fallback_standard",
                index=index,
                analyzer=settings.es_analyzer,
                error=message,
            )
            try:
                await es.indices.create(
                    index=index, body=_index_body("standard", "standard")
                )
                _log.info("es_index_created", index=index, analyzer="standard")
            except Exception as retry_exc:
                if "resource_already_exists_exception" not in str(retry_exc):
                    raise
        else:
            raise
    _index_ready = True


class ESKeywordSearchChannel(SearchChannel):
    """BM25 full-text search via Elasticsearch."""

    INDEX_NAME = "rag_keyword_store"

    @property
    def enabled(self) -> bool:
        return bool(settings.es_enabled)

    async def search(
        self, query: str, collection_name: str, top_k: int = 10
    ) -> list[SearchResult]:
        if not self.enabled:
            return []

        try:
            es = await get_es_client()
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
                    block_type=src.get("block_type", ""),
                    page_start=src.get("page_start"),
                    page_end=src.get("page_end"),
                ))
            return results
        except Exception as exc:
            # Search is best-effort: degrade to empty but never silently
            _log.warning("es_search_failed", kb_id=collection_name, error=str(exc))
            return []

    async def insert(self, chunks: list[dict], kb_id: str):
        """Bulk-index documents into ES.

        Raises on failure — callers decide whether to degrade (log & continue)
        or propagate. No silent swallowing here.
        """
        if not self.enabled or not chunks:
            return

        from elasticsearch.helpers import async_bulk

        es = await get_es_client()
        await ensure_keyword_index(es)
        actions = [
            {
                "_index": self.INDEX_NAME,
                "_id": c["id"],
                "_source": {
                    "kb_id": kb_id,
                    "tenant_id": c.get("tenant_id", "default"),
                    "department_id": c.get("department_id"),
                    "doc_id": c.get("doc_id", ""),
                    "content": c["content"],
                    "embedding_content": c.get("embedding_content"),
                    "chunk_index": c.get("chunk_index", 0),
                    "block_type": c.get("block_type"),
                    "page_start": c.get("page_start"),
                    "page_end": c.get("page_end"),
                },
            }
            for c in chunks
        ]
        await async_bulk(es, actions)

    async def delete_by_ids(self, chunk_ids: list[str]) -> None:
        if not chunk_ids or not self.enabled:
            return
        from elasticsearch.helpers import async_bulk

        es = await get_es_client()
        await async_bulk(
            es,
            [
                {
                    "_op_type": "delete",
                    "_index": self.INDEX_NAME,
                    "_id": chunk_id,
                }
                for chunk_id in chunk_ids
            ],
            raise_on_error=False,
        )
