"""RAG Pipeline — rewrite → intent → multi-channel search → fusion → rerank."""
from __future__ import annotations

import hashlib
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.database.session import async_session_factory
from app.models import KnowledgeChunk, KnowledgeDocument

from app.rag.search.vector import MilvusSearchChannel
from app.rag.search.keyword import ESKeywordSearchChannel
from app.rag.postprocess.fusion import rrf_fusion, deduplicate
from app.rag.postprocess.reranker import Reranker
from app.rag.search.base import SearchResult
from app.rag.rewrite import rewrite_query
from app.rag.intent import recognize_intent
from app.rag.graph.lightrag_client import LightRAGClient
from app.rag.graph.neo4j_store import Neo4jGraphStore
from app.rag.model_router import ModelRouter
from app.rag.decompose import decompose_query
from app.rag.governance import (
    RetrievalBudget,
    run_search_channels,
    select_context,
)
from app.security.access import Principal
from app.security.service import filter_authorized_results
from app.config.settings import settings
from app.config.logging_config import get_logger

_pipeline_log = get_logger("flavorag.rag.pipeline")


@dataclass
class RAGContext:
    question: str
    conversation_id: str | None = None
    kb_id: str | None = None
    collection_name: str | None = None
    history: list[dict] = field(default_factory=list)
    deep_thinking: bool = False
    graph_rag: bool | None = None
    trace_run_id: str | None = None
    user_id: str = ""
    tenant_id: str = "default"
    department_id: str = ""
    role: str = "user"


@dataclass
class RAGResult:
    question: str
    rewrite: str | None
    intent: dict | None
    context_chunks: list[dict]
    sources: list[dict]
    duration_ms: int
    trace_run_id: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_api_key: str | None = None
    answerable: bool = True
    rejection_reason: str | None = None
    channel_statuses: dict = field(default_factory=dict)
    subqueries: list[str] = field(default_factory=list)


class RAGPipeline:
    """Full RAG pipeline with multi-channel search + fusion + rerank + trace."""

    def __init__(self, trace_logger=None):
        self.milvus = MilvusSearchChannel()
        self.es = ESKeywordSearchChannel()
        self.reranker = Reranker()
        self.graph_client = LightRAGClient()
        self.native_graph = Neo4jGraphStore()
        self.model_router = ModelRouter()
        self._trace = trace_logger

    async def run(self, ctx: RAGContext) -> RAGResult:
        t0 = time.time()
        trace_id = ctx.trace_run_id
        budget = RetrievalBudget(
            per_channel_top_k=settings.retrieval_per_channel_top_k,
            max_candidates=settings.retrieval_max_candidates,
            final_top_k=settings.retrieval_final_top_k,
            channel_timeout_ms=settings.retrieval_channel_timeout_ms,
            total_timeout_ms=settings.retrieval_total_timeout_ms,
            context_max_chars=settings.retrieval_context_max_chars,
            max_subqueries=settings.query_decomposition_max_queries,
        )
        _pipeline_log.info("rag_pipeline_start", question=ctx.question[:80], kb_id=ctx.kb_id)

        # 1. Query rewrite
        t_rewrite = datetime.now(timezone.utc).replace(tzinfo=None)
        rewritten = await rewrite_query(ctx.question, ctx.history)
        t_rewrite_end = datetime.now(timezone.utc).replace(tzinfo=None)
        rewrite_ms = int((t_rewrite_end - t_rewrite).total_seconds() * 1000)
        _pipeline_log.info("rewrite", original=ctx.question[:60], rewritten=(rewritten or "(unchanged)")[:60], took_ms=rewrite_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "rewrite", "query_rewrite",
                                         t_rewrite, t_rewrite_end,
                                         input_data={"query": ctx.question},
                                         output_data={"rewritten": rewritten})

        # 2. Intent recognition
        t_intent = datetime.now(timezone.utc).replace(tzinfo=None)
        intent = await recognize_intent(rewritten or ctx.question)
        t_intent_end = datetime.now(timezone.utc).replace(tzinfo=None)
        intent_ms = int((t_intent_end - t_intent).total_seconds() * 1000)
        intent_name = intent.get("intent", "unknown") if intent else "unknown"
        _pipeline_log.info("intent", intent=intent_name, took_ms=intent_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "intent", "intent_recognition",
                                         t_intent, t_intent_end,
                                         input_data={"query": rewritten or ctx.question},
                                         output_data=intent)

        # 3. Resolve collection
        collection_name = (
            ctx.collection_name
            or (intent.get("collection_name") if intent else None)
            or "default_store"
        )
        _pipeline_log.info("collection_resolved", collection_name=collection_name)

        search_question = rewritten or ctx.question

        # 4. Bounded decomposition + concurrent multi-channel search
        subqueries = await decompose_query(
            search_question, max_queries=budget.max_subqueries
        )
        if not subqueries:
            subqueries = [search_question]
        t_search = datetime.now(timezone.utc).replace(tzinfo=None)

        async def vector_search() -> list[SearchResult]:
            output: list[SearchResult] = []
            for query in subqueries:
                output.extend(
                    await self.milvus.search(
                        query,
                        collection_name,
                        top_k=budget.per_channel_top_k,
                    )
                )
            return output

        async def keyword_search() -> list[SearchResult]:
            output: list[SearchResult] = []
            for query in subqueries:
                output.extend(
                    await self.es.search(
                        query,
                        ctx.kb_id or collection_name,
                        top_k=budget.per_channel_top_k,
                    )
                )
            return output

        async def graph_search() -> list[SearchResult]:
            output: list[SearchResult] = []
            for query in subqueries:
                native_results = await self.native_graph.search(
                    query,
                    kb_id=ctx.kb_id or "",
                    top_k=budget.per_channel_top_k,
                )
                output.extend(native_results)
                try:
                    response = await asyncio.wait_for(
                        self.graph_client.query_graph(
                            query,
                            top_k=budget.per_channel_top_k,
                            kb_id=ctx.kb_id or "",
                            collection_name=ctx.collection_name or "",
                            enabled=True,
                        ),
                        timeout=max(
                            0.5,
                            min(1.5, budget.channel_timeout_ms / 2000),
                        ),
                    )
                    output.extend(
                        SearchResult(
                            chunk_id=hit.get("chunk_id") or hit.get("id", ""),
                            doc_id=hit.get("doc_id", ""),
                            content=hit.get("content", ""),
                            score=float(hit.get("score", 0.0)),
                            metadata={"graphEntity": hit.get("entity", "")},
                        )
                        for hit in response.get("results", [])
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    _pipeline_log.warning(
                        "lightrag_query_timeout_using_native_graph",
                        query=query[:80],
                    )
                except Exception as exc:
                    _pipeline_log.warning(
                        "lightrag_query_failed_using_native_graph",
                        query=query[:80],
                        error=type(exc).__name__,
                    )
            return output

        channels = {"vector": vector_search}
        if settings.es_enabled:
            channels["keyword"] = keyword_search
        graph_enabled = (
            settings.graph_enabled if ctx.graph_rag is None else ctx.graph_rag
        )
        if graph_enabled:
            channels["graph"] = graph_search
        channel_results, channel_statuses = await run_search_channels(channels, budget)
        all_results = [items for items in channel_results.values() if items]

        t_search_end = datetime.now(timezone.utc).replace(tzinfo=None)
        search_ms = int((t_search_end - t_search).total_seconds() * 1000)
        total_hits = sum(len(r) for r in all_results)
        _pipeline_log.info("multi_search_complete", channels=len(all_results), total_hits=total_hits, took_ms=search_ms)
        if self._trace and trace_id:
            await self._trace.trace_node(
                trace_id,
                "retrieval",
                "parallel_channels",
                t_search,
                t_search_end,
                input_data={
                    "subquery_count": len(subqueries),
                    "channels": list(channels),
                },
                output_data={
                    "total_hits": total_hits,
                    "statuses": {
                        name: status.__dict__
                        for name, status in channel_statuses.items()
                    },
                },
            )

        # 5. RRF fusion
        t_fuse = datetime.now(timezone.utc).replace(tzinfo=None)
        if len(all_results) > 1:
            # Log pre-fusion per-channel top scores
            for i, channel_results in enumerate(all_results):
                if channel_results:
                    top_scores = [f"{r.score:.4f}" for r in channel_results[:3]]
                    _pipeline_log.info("rrf_pre_fusion_channel", channel=i, count=len(channel_results), top_scores=top_scores)
            merged = rrf_fusion(*all_results)
        elif all_results:
            merged = all_results[0]
        else:
            merged = []
        t_fuse_end = datetime.now(timezone.utc).replace(tzinfo=None)
        fusion_ms = int((t_fuse_end - t_fuse).total_seconds() * 1000)
        _pipeline_log.info("rrf_fusion", input_channels=len(all_results), input_total=total_hits, output_count=len(merged), took_ms=fusion_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "fusion", "rrf_fusion",
                                         t_fuse, t_fuse_end,
                                         input_data={"channel_count": len(all_results), "total_hits": total_hits},
                                         output_data={"merged_count": len(merged)})

        # 6. Dedup
        t_dedup = time.time()
        deduped = deduplicate(merged)
        deduped = await self._filter_unavailable_chunks(deduped)
        dedup_ms = int((time.time() - t_dedup) * 1000)
        recall_count = len(deduped)
        removed = len(merged) - len(deduped)
        _pipeline_log.info("deduplicate", before=len(merged), after=recall_count, removed=removed, took_ms=dedup_ms)

        # Resolve canonical IDs before the fail-closed authorization filter.
        await self._resolve_metadata(deduped, kb_id=ctx.kb_id)
        if ctx.kb_id:
            principal = Principal(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                department_id=ctx.department_id,
                role=ctx.role,
            )
            async with async_session_factory() as session:
                deduped = await filter_authorized_results(
                    session, principal, deduped, kb_id=ctx.kb_id
                )
        else:
            deduped = []
        recall_count = len(deduped)

        # 7. Rerank, relevance rejection, and context packing
        t_rerank = datetime.now(timezone.utc).replace(tzinfo=None)
        # Log pre-rerank order
        pre_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in deduped[:5]]
        _pipeline_log.info("rerank_pre", candidates=recall_count, top_ids=pre_rerank_ids)
        reranked = await self.reranker.rerank(
            search_question,
            deduped,
            top_n=min(budget.max_candidates, max(budget.final_top_k * 2, budget.final_top_k)),
        )
        reranked, retrieval_decision = select_context(
            reranked,
            budget,
            min_score=settings.retrieval_min_relevance_score,
        )
        t_rerank_end = datetime.now(timezone.utc).replace(tzinfo=None)
        rerank_ms = int((t_rerank_end - t_rerank).total_seconds() * 1000)
        final_count = len(reranked)
        # Log post-rerank order
        post_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in reranked]
        _pipeline_log.info("rerank_post", final_count=final_count, top_ids=post_rerank_ids, took_ms=rerank_ms)

        if self._trace:
            await self._trace.trace_node(trace_id or "", "rerank", "rerank",
                                         t_rerank, t_rerank_end,
                                         input_data={"candidate_count": recall_count},
                                         output_data={"reranked_count": final_count})

        # 8. Model routing
        intent_name = intent.get("intent", "general") if intent else "general"
        if ctx.deep_thinking and not settings.reasoning_model:
            _pipeline_log.warning(
                "deep_thinking_requested_but_no_reasoning_model",
                hint="Set REASONING_MODEL in .env to enable reasoning traces",
            )
        model_name, model_base_url, model_api_key = self.model_router.route(
            intent_name, deep_thinking=ctx.deep_thinking
        )

        # 9. Build chunks
        chunks = [
            {
                "content": r.content,
                "chunk_id": r.chunk_id,
                "score": r.score,
                "blockType": r.block_type,
                "pageStart": r.page_start,
                "pageEnd": r.page_end,
            }
            for r in reranked
        ]
        sources = [
            {
                "documentId": r.doc_id,
                "chunkId": r.chunk_id,
                "docName": r.doc_name or "unknown",
                "chunkIndex": r.chunk_index,
                "content": r.content[:300],
                "score": r.score,
                "blockType": r.block_type,
                "pageStart": r.page_start,
                "pageEnd": r.page_end,
                "bboxes": r.bboxes,
                "assets": r.assets,
            }
            for r in reranked
        ]

        duration = int((time.time() - t0) * 1000)

        _pipeline_log.info(
            "rag_pipeline_complete",
            question=ctx.question[:60],
            rewrite=rewrite_ms,
            intent=intent_ms,
            search=search_ms,
            fusion=fusion_ms,
            dedup=dedup_ms,
            rerank=rerank_ms,
            total_ms=duration,
            recall_count=recall_count,
            final_count=final_count,
            answerable=retrieval_decision.answerable,
            rejection_reason=retrieval_decision.reason or None,
            channel_statuses={
                name: status.__dict__ for name, status in channel_statuses.items()
            },
            subqueries=subqueries,
        )

        return RAGResult(
            question=ctx.question,
            rewrite=rewritten,
            intent=intent,
            context_chunks=chunks,
            sources=sources,
            duration_ms=duration,
            trace_run_id=trace_id,
            model_name=model_name,
            model_base_url=model_base_url,
            model_api_key=model_api_key,
            answerable=retrieval_decision.answerable,
            rejection_reason=retrieval_decision.reason or None,
            channel_statuses={
                name: status.__dict__ for name, status in channel_statuses.items()
            },
            subqueries=subqueries,
        )

    async def _filter_unavailable_chunks(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Remove disabled or deleted chunks from stale search-index results.

        Milvus, Elasticsearch, and graph indexes do not carry the relational
        ``enabled`` flag. The database is therefore the source of truth at
        retrieval time, so toggling a chunk takes effect without reindexing.
        """
        if not results:
            return results

        result_ids = {result.chunk_id for result in results if result.chunk_id}
        result_hashes = {
            hashlib.sha256(result.content.encode()).hexdigest()[:16]
            for result in results
            if result.content
        }
        if not result_ids and not result_hashes:
            return results

        predicates = []
        if result_ids:
            predicates.append(KnowledgeChunk.id.in_(result_ids))
        if result_hashes:
            predicates.append(KnowledgeChunk.content_hash.in_(result_hashes))

        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(
                        KnowledgeChunk.id,
                        KnowledgeChunk.content_hash,
                        KnowledgeChunk.enabled,
                        KnowledgeChunk.deleted,
                    ).where(or_(*predicates))
                )

                availability_by_id: dict[str, bool] = {}
                availability_by_hash: dict[str, bool] = {}
                for chunk_id, content_hash, enabled, deleted in rows:
                    available = deleted == 0 and enabled != 0
                    availability_by_id[chunk_id] = available
                    if content_hash:
                        availability_by_hash[content_hash] = (
                            availability_by_hash.get(content_hash, False) or available
                        )

            filtered: list[SearchResult] = []
            for result in results:
                if availability_by_id.get(result.chunk_id, False):
                    filtered.append(result)
                    continue

                if result.content:
                    content_hash = hashlib.sha256(result.content.encode()).hexdigest()[:16]
                    if content_hash in availability_by_hash:
                        if availability_by_hash[content_hash]:
                            filtered.append(result)
                        continue

                # Unknown external-index rows fail closed.

            removed = len(results) - len(filtered)
            if removed:
                _pipeline_log.info(
                    "disabled_chunks_filtered",
                    before=len(results),
                    after=len(filtered),
                    removed=removed,
                )
            return filtered
        except Exception as exc:
            _pipeline_log.warning("Failed to filter unavailable chunks: %s", exc)
            return []

    async def _resolve_metadata(
        self,
        results: list[SearchResult],
        *,
        kb_id: str | None = None,
    ) -> None:
        """Resolve doc_name + chunk_index + doc_id from PostgreSQL.

        Uses content_hash as the primary match key — ingestion stores
        sha256(content)[:16] in KnowledgeChunk.content_hash.  ID-based
        matching from Milvus is tried as a fast path but content never lies.
        """
        if not results:
            return

        import logging
        _log = logging.getLogger("flavorag.rag.pipeline")

        # Phase 1: build content hash → result index map
        id_to_index = {
            result.chunk_id: index
            for index, result in enumerate(results)
            if result.chunk_id
        }
        hash_to_indices: dict[str, list[int]] = {}
        for i, r in enumerate(results):
            if r.content:
                h = hashlib.sha256(r.content.encode()).hexdigest()[:16]
                hash_to_indices.setdefault(h, []).append(i)

        if not id_to_index and not hash_to_indices:
            return

        predicates = []
        if id_to_index:
            predicates.append(KnowledgeChunk.id.in_(id_to_index))
        if hash_to_indices:
            predicates.append(KnowledgeChunk.content_hash.in_(hash_to_indices))

        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(
                        KnowledgeChunk.content_hash,
                        KnowledgeChunk.id,
                        KnowledgeChunk.chunk_index,
                        KnowledgeChunk.doc_id,
                        KnowledgeChunk.block_type,
                        KnowledgeChunk.page_start,
                        KnowledgeChunk.page_end,
                        KnowledgeChunk.bbox_json,
                        KnowledgeChunk.metadata_json,
                        KnowledgeDocument.doc_name,
                    )
                    .outerjoin(KnowledgeDocument, KnowledgeChunk.doc_id == KnowledgeDocument.id)
                    .where(
                        or_(*predicates),
                        KnowledgeChunk.deleted == 0,
                        *([KnowledgeChunk.kb_id == kb_id] if kb_id else []),
                    )
                )
                for row in rows:
                    (
                        content_hash,
                        chunk_id,
                        chunk_index,
                        doc_id,
                        block_type,
                        page_start,
                        page_end,
                        bbox_json,
                        metadata_json,
                        doc_name,
                    ) = row
                    indices = (
                        [id_to_index[chunk_id]]
                        if chunk_id in id_to_index
                        else hash_to_indices.get(content_hash, [])
                    )
                    for idx in indices:
                        r = results[idx]
                        r.chunk_id = chunk_id
                        r.chunk_index = chunk_index
                        r.doc_id = doc_id
                        r.block_type = block_type or ""
                        r.page_start = page_start
                        r.page_end = page_end
                        r.bboxes = bbox_json or []
                        r.metadata = metadata_json or {}
                        r.doc_name = doc_name or "unknown"

                asset_ids = {
                    asset_id
                    for result in results
                    for asset_id in result.metadata.get("asset_ids", [])
                }
                if asset_ids:
                    from app.models import KnowledgeAsset
                    asset_rows = await session.execute(
                        select(
                            KnowledgeAsset.id,
                            KnowledgeAsset.storage_url,
                            KnowledgeAsset.mime_type,
                            KnowledgeAsset.description,
                            KnowledgeAsset.page_no,
                            KnowledgeAsset.bbox_json,
                        ).where(
                            KnowledgeAsset.id.in_(asset_ids),
                            *([KnowledgeAsset.kb_id == kb_id] if kb_id else []),
                            KnowledgeAsset.deleted == 0,
                        )
                    )
                    assets_by_id = {
                        row.id: {
                            "id": row.id,
                            "url": row.storage_url,
                            "mimeType": row.mime_type,
                            "description": row.description,
                            "pageNo": row.page_no,
                            "bbox": row.bbox_json,
                        }
                        for row in asset_rows
                    }
                    for result in results:
                        result.assets = [
                            assets_by_id[asset_id]
                            for asset_id in result.metadata.get("asset_ids", [])
                            if asset_id in assets_by_id
                        ]
        except Exception as exc:
            _log.warning("Failed to resolve chunk metadata from PG: %s", exc)
