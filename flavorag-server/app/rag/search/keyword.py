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

    async def close(self) -> None:
        if self._es is not None:
            await self._es.close()
            self._es = None
            self._initialized = False

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
                    block_type=src.get("block_type", ""),
                    page_start=src.get("page_start"),
                    page_end=src.get("page_end"),
                ))
            return results
        except Exception:
            return []
        finally:
            await self.close()

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
                        "tenant_id": c.get("tenant_id", "default"),
                        "department_id": c.get("department_id"),
                        "doc_id": c.get("doc_id", ""),
                        "content": c["content"],
                        "chunk_index": c.get("chunk_index", 0),
                    },
                }
                for c in chunks
            ]
            await async_bulk(es, actions)
        except Exception:
            pass
        finally:
            await self.close()

    async def delete_by_ids(self, chunk_ids: list[str]) -> None:
        if not chunk_ids or not self.enabled:
            return
        es = await self._get_es()
        if es is None:
            return
        from elasticsearch.helpers import async_bulk

        try:
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
        finally:
            await self.close()
