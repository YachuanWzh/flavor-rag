"""RAG Pipeline — rewrite → intent → multi-channel search → fusion → rerank."""
from __future__ import annotations

import time
from datetime import datetime
from dataclasses import dataclass, field

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

        # 1. Query rewrite
        t_rewrite = datetime.utcnow()
        rewritten = await rewrite_query(ctx.question, ctx.history)
        t_rewrite_end = datetime.utcnow()
        if self._trace:
            await self._trace.trace_node(trace_id or "", "rewrite", "query_rewrite",
                                         t_rewrite, t_rewrite_end,
                                         input_data={"query": ctx.question},
                                         output_data={"rewritten": rewritten})

        # 2. Intent recognition
        t_intent = datetime.utcnow()
        intent = await recognize_intent(rewritten or ctx.question)
        t_intent_end = datetime.utcnow()
        if self._trace:
            await self._trace.trace_node(trace_id or "", "intent", "intent_recognition",
                                         t_intent, t_intent_end,
                                         input_data={"query": rewritten or ctx.question},
                                         output_data=intent)

        # 3. Resolve collection
        collection_name = (
            ctx.collection_name
            or (intent.get("collection_name") if intent else None)
            or "rag_default_store"
        )

        search_question = rewritten or ctx.question

        # 4. Multi-channel search
        t_search = datetime.utcnow()
        all_results: list[list[SearchResult]] = []

        # Vector search
        vector_results = await self.milvus.search(search_question, collection_name, top_k=10)
        all_results.append(vector_results)

        # ES keyword search
        if settings.es_enabled:
            kw_results = await self.es.search(search_question, collection_name, top_k=10)
            if kw_results:
                all_results.append(kw_results)

        # Graph search
        if settings.graph_enabled:
            graph_resp = await self.graph_client.query_graph(search_question, top_k=5)
            graph_hits = graph_resp.get("results", [])
            for hit in graph_hits:
                all_results.append([SearchResult(
                    chunk_id=hit.get("id", ""),
                    content=hit.get("content", ""),
                    score=float(hit.get("score", 0.5)),
                )])
        t_search_end = datetime.utcnow()
        search_ms = int((t_search_end - t_search).total_seconds() * 1000)

        # 5. RRF fusion
        t_fuse = datetime.utcnow()
        if len(all_results) > 1:
            merged = rrf_fusion(*all_results)
        elif all_results:
            merged = all_results[0]
        else:
            merged = []
        t_fuse_end = datetime.utcnow()
        if self._trace:
            await self._trace.trace_node(trace_id or "", "fusion", "rrf_fusion",
                                         t_fuse, t_fuse_end,
                                         input_data={"channel_count": len(all_results), "total_hits": sum(len(r) for r in all_results)},
                                         output_data={"merged_count": len(merged)})

        # 6. Dedup
        deduped = deduplicate(merged)
        recall_count = len(deduped)

        # 7. Rerank
        t_rerank = datetime.utcnow()
        reranked = await self.reranker.rerank(search_question, deduped, top_n=5)
        t_rerank_end = datetime.utcnow()
        final_count = len(reranked)

        if self._trace:
            await self._trace.trace_node(trace_id or "", "rerank", "rerank",
                                         t_rerank, t_rerank_end,
                                         input_data={"candidate_count": recall_count},
                                         output_data={"reranked_count": final_count})

        # 8. Model routing
        intent_name = intent.get("intent", "general") if intent else "general"
        model_name, model_base_url, model_api_key = self.model_router.route(intent_name)

        # 9. Build chunks
        chunks = [
            {"content": r.content, "chunk_id": r.chunk_id, "score": r.score}
            for r in reranked
        ]
        sources = [
            {"docName": r.doc_name or "unknown", "chunkIndex": r.chunk_index,
             "content": r.content[:200], "score": r.score}
            for r in reranked
        ]

        duration = int((time.time() - t0) * 1000)

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
