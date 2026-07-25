"""RAG Pipeline — rewrite → intent → multi-channel search → fusion → rerank."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from sqlalchemy import select

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
from app.rag.model_router import ModelRouter
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


class RAGPipeline:
    """Full RAG pipeline with multi-channel search + fusion + rerank + trace."""

    def __init__(self, trace_logger=None):
        self.milvus = MilvusSearchChannel()
        self.es = ESKeywordSearchChannel()
        self.reranker = Reranker()
        self.graph_client = LightRAGClient()
        self.model_router = ModelRouter()
        self._trace = trace_logger

    async def run(self, ctx: RAGContext) -> RAGResult:
        t0 = time.time()
        trace_id: str | None = None
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

        # 4. Multi-channel search
        t_search = datetime.now(timezone.utc).replace(tzinfo=None)
        all_results: list[list[SearchResult]] = []

        # Vector search
        t_vector = time.time()
        vector_results = await self.milvus.search(search_question, collection_name, top_k=10)
        vector_ms = int((time.time() - t_vector) * 1000)
        _pipeline_log.info("vector_retrieval", collection=collection_name, count=len(vector_results), took_ms=vector_ms)
        all_results.append(vector_results)

        # ES keyword search
        if settings.es_enabled:
            t_keyword = time.time()
            kw_results = await self.es.search(search_question, collection_name, top_k=10)
            kw_ms = int((time.time() - t_keyword) * 1000)
            _pipeline_log.info("bm25_retrieval", collection=collection_name, count=len(kw_results), took_ms=kw_ms)
            if kw_results:
                all_results.append(kw_results)

        # Graph search
        if settings.graph_enabled:
            t_graph = time.time()
            graph_resp = await self.graph_client.query_graph(search_question, top_k=5)
            graph_hits = graph_resp.get("results", [])
            graph_ms = int((time.time() - t_graph) * 1000)
            for hit in graph_hits:
                all_results.append([SearchResult(
                    chunk_id=hit.get("id", ""),
                    content=hit.get("content", ""),
                    score=float(hit.get("score", 0.5)),
                )])
            _pipeline_log.info("graph_retrieval", count=len(graph_hits), took_ms=graph_ms)

        t_search_end = datetime.now(timezone.utc).replace(tzinfo=None)
        search_ms = int((t_search_end - t_search).total_seconds() * 1000)
        total_hits = sum(len(r) for r in all_results)
        _pipeline_log.info("multi_search_complete", channels=len(all_results), total_hits=total_hits, took_ms=search_ms)

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
        dedup_ms = int((time.time() - t_dedup) * 1000)
        recall_count = len(deduped)
        removed = len(merged) - len(deduped)
        _pipeline_log.info("deduplicate", before=len(merged), after=recall_count, removed=removed, took_ms=dedup_ms)

        # 7. Rerank
        t_rerank = datetime.now(timezone.utc).replace(tzinfo=None)
        # Log pre-rerank order
        pre_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in deduped[:5]]
        _pipeline_log.info("rerank_pre", candidates=recall_count, top_ids=pre_rerank_ids)
        reranked = await self.reranker.rerank(search_question, deduped, top_n=5)
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

        # 9. Resolve doc_name + chunk_index from PG
        await self._resolve_metadata(reranked)

        # 10. Build chunks
        chunks = [
            {"content": r.content, "chunk_id": r.chunk_id, "score": r.score}
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
            }
            for r in reranked
        ]

        duration = int((time.time() - t0) * 1000)

        _pipeline_log.info(
            "rag_pipeline_complete",
            question=ctx.question[:60],
            rewrite=rewrite_ms,
            intent=intent_ms,
            vector=vector_ms,
            fusion=fusion_ms,
            dedup=dedup_ms,
            rerank=rerank_ms,
            total_ms=duration,
            recall_count=recall_count,
            final_count=final_count,
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
        )

    async def _resolve_metadata(self, results: list[SearchResult]) -> None:
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
        hash_to_indices: dict[str, list[int]] = {}
        for i, r in enumerate(results):
            if r.content:
                h = hashlib.sha256(r.content.encode()).hexdigest()[:16]
                hash_to_indices.setdefault(h, []).append(i)

        if not hash_to_indices:
            return

        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(
                        KnowledgeChunk.content_hash,
                        KnowledgeChunk.id,
                        KnowledgeChunk.chunk_index,
                        KnowledgeChunk.doc_id,
                        KnowledgeDocument.doc_name,
                    )
                    .outerjoin(KnowledgeDocument, KnowledgeChunk.doc_id == KnowledgeDocument.id)
                    .where(
                        KnowledgeChunk.content_hash.in_(hash_to_indices.keys()),
                        KnowledgeChunk.deleted == 0,
                    )
                )
                for row in rows:
                    content_hash, chunk_id, chunk_index, doc_id, doc_name = row
                    for idx in hash_to_indices.get(content_hash, []):
                        r = results[idx]
                        r.chunk_id = chunk_id
                        r.chunk_index = chunk_index
                        r.doc_id = doc_id
                        r.doc_name = doc_name or "unknown"
        except Exception as exc:
            _log.warning("Failed to resolve chunk metadata from PG: %s", exc)
