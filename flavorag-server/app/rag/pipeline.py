"""RAG Pipeline — rewrite → intent → multi-channel search → fusion → rerank."""
from __future__ import annotations

import hashlib
import asyncio
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace

from sqlalchemy import or_, select

from app.database.session import async_session_factory
from app.models import KnowledgeChunk, KnowledgeDocument

from app.rag.search.vector import MilvusSearchChannel
from app.rag.search.keyword import ESKeywordSearchChannel
from app.rag.postprocess.fusion import rrf_fusion, deduplicate
from app.rag.postprocess.reranker import Reranker
from app.rag.search.base import SearchResult
# Compatibility exports remain importable because existing integrations monkeypatch
# these names while migrating to the structured entry points.
from app.rag.rewrite import rewrite_query, rewrite_query_result  # noqa: F401
from app.rag.intent import recognize_intent, resolve_intents  # noqa: F401
from app.rag.graph.lightrag_client import LightRAGClient
from app.rag.graph.neo4j_store import Neo4jGraphStore
from app.rag.model_router import ModelRouter
from app.rag.governance import (
    CircuitBreaker,
    RetrievalBudget,
    estimate_tokens,
    run_search_channels,
    select_context,
)
from app.security.access import Principal
from app.security.service import filter_authorized_results
from app.config.settings import settings
from app.config.hyperparam import get_hyperparam_typed
from app.config.logging_config import get_logger


def can_reuse_speculative_results(
    *,
    speculative_collection: str,
    final_collection: str,
    original_query: str,
    search_query: str,
) -> bool:
    """Speculation is valid only when both its corpus and query still match."""
    return (
        speculative_collection == final_collection
        and original_query == search_query
    )


def relevance_threshold(results: list[SearchResult], tenant_id: str = "default") -> float:
    """Select a threshold in the score domain produced by the last stage."""
    if any("rerank_score" in item.metadata for item in results):
        return get_hyperparam_typed(
            "retrieval_reranker_min_score", settings.retrieval_reranker_min_score,
            tenant_id=tenant_id,
        )
    if any("fusionScore" in item.metadata for item in results):
        return get_hyperparam_typed(
            "retrieval_rrf_min_score", settings.retrieval_rrf_min_score,
            tenant_id=tenant_id,
        )
    return get_hyperparam_typed(
        "retrieval_vector_min_score", settings.retrieval_vector_min_score,
        tenant_id=tenant_id,
    )
_pipeline_log = get_logger("flavorag.rag.pipeline")
_ORIGINAL_REWRITE_QUERY = rewrite_query
_ORIGINAL_RECOGNIZE_INTENT = recognize_intent


def _interleave_by_kb(items: list) -> list:
    """Round-robin interleave items grouped by kb_id.

    Ensures each KB gets fair positional representation in the merged list,
    preventing a high-scoring KB from monopolizing early positions and being
    the only one selected by downstream round-robin bounding.
    """
    from app.rag.search.base import SearchResult  # noqa: already imported at top

    groups: dict[str, list] = {}
    order: list[str] = []
    for item in items:
        kb = item.metadata.get("kb_id", "") if isinstance(item, SearchResult) else ""
        if kb not in groups:
            groups[kb] = []
            order.append(kb)
        groups[kb].append(item)
    # Sort within each KB group by score descending
    for kb in order:
        groups[kb].sort(key=lambda r: r.score, reverse=True)
    # Round-robin across KBs
    result: list = []
    rank = 0
    while True:
        added = False
        for kb in order:
            if rank < len(groups[kb]):
                result.append(groups[kb][rank])
                added = True
        if not added:
            break
        rank += 1
    return result


async def _rewrite_for_pipeline(
    ctx: RAGContext, budget: RetrievalBudget
):
    if rewrite_query is not _ORIGINAL_REWRITE_QUERY:
        from app.rag.rewrite import RewriteResult

        value = await rewrite_query(ctx.question, ctx.history)
        rewritten = value or ctx.question
        return RewriteResult(
            original_query=ctx.question,
            normalized_query=ctx.question.strip(),
            rewritten_query=rewritten,
            subqueries=[rewritten],
        )
    return await rewrite_query_result(
        ctx.question,
        ctx.history,
        kb_id=ctx.kb_id,
        tenant_id=ctx.tenant_id,
        max_queries=budget.max_subqueries,
    )


async def _intent_for_pipeline(
    queries: list[str], ctx: RAGContext
):
    if recognize_intent is not _ORIGINAL_RECOGNIZE_INTENT:
        from app.rag.intent import IntentMatch, IntentResolution, SubqueryIntent

        raw = await recognize_intent(queries[0] if queries else ctx.question)
        match = IntentMatch(
            intent_code=str(raw.get("intent", "general")),
            name=str(raw.get("intent", "general")),
            score=float(raw.get("confidence", 1.0)),
            collection_name=raw.get("collection_name"),
            search_channels=list(raw.get("search_channels", ["vector"])),
        )
        return IntentResolution(
            subqueries=[
                SubqueryIntent(query=query, matches=[match])
                for query in (queries or [ctx.question])
            ]
        )
    return await resolve_intents(
        queries,
        kb_id=ctx.kb_id,
        tenant_id=ctx.tenant_id,
    )


@dataclass(frozen=True)
class RetrievalScope:
    kb_id: str
    kb_name: str
    collection_name: str
    embedding_model: str | None = None


@dataclass
class RAGContext:
    question: str
    conversation_id: str | None = None
    kb_id: str | None = None
    collection_name: str | None = None
    history: list[dict] = field(default_factory=list)
    deep_thinking: bool = False
    graph_rag: bool | None = None
    enable_neighbor_expansion: bool = False
    enable_hyde: bool = False
    trace_run_id: str | None = None
    user_id: str = ""
    tenant_id: str = "default"
    department_id: str = ""
    role: str = "user"
    # ── mem0 long-term memory + user profile injection ──
    profile: dict | None = None       # user profile dict (7 dimensions)
    memories: list[dict] = field(default_factory=list)  # mem0 relevant facts
    final_top_k: int | None = None
    embedding_model: str | None = None
    retrieval_scopes: list[RetrievalScope] = field(default_factory=list)


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
    response_mode: str = "rag"
    direct_response: str | None = None
    prompt_template: str | None = None
    applied_mappings: list[dict] = field(default_factory=list)
    hyde_doc: str | None = None           # HyDE 生成的假设文档内容
    hyde_meta: dict = field(default_factory=dict)  # HyDE 元信息(model/duration_ms/timed_out)
    memories: list[dict] = field(default_factory=list)  # mem0 相关记忆事实（注入生成 prompt）
    profile: dict | None = None           # 用户画像（注入生成 prompt）


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
        self._channel_breakers = {
            name: CircuitBreaker(
                failure_threshold=settings.circuit_breaker_failures,
                recovery_timeout_sec=settings.circuit_breaker_recovery_sec,
            )
            for name in ("vector", "keyword", "graph", "hyde_vector")
        }

    async def _search_vector(
        self,
        query: str,
        collection_name: str,
        *,
        top_k: int,
        embedding_model: str | None,
    ) -> list[SearchResult]:
        if embedding_model:
            return await self.milvus.search(
                query,
                collection_name,
                top_k=top_k,
                embedding_model=embedding_model,
            )
        return await self.milvus.search(
            query, collection_name, top_k=top_k
        )

    @staticmethod
    def _resolved_scopes(
        ctx: RAGContext, fallback_collection: str
    ) -> list[RetrievalScope]:
        if ctx.retrieval_scopes:
            return ctx.retrieval_scopes
        if not ctx.kb_id:
            return []
        return [
            RetrievalScope(
                kb_id=ctx.kb_id,
                kb_name="",
                collection_name=ctx.collection_name or fallback_collection,
                embedding_model=ctx.embedding_model,
            )
        ]

    async def _search_vector_scopes(
        self,
        queries: list[str],
        scopes: list[RetrievalScope],
        *,
        top_k: int,
    ) -> list[SearchResult]:
        async def _search_one(query: str, scope: RetrievalScope) -> list[SearchResult]:
            try:
                results = await self._search_vector(
                    query,
                    scope.collection_name,
                    top_k=top_k,
                    embedding_model=scope.embedding_model,
                )
            except Exception as exc:
                _pipeline_log.warning(
                    "vector_scope_search_failed",
                    kb_id=scope.kb_id,
                    collection=scope.collection_name,
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
                return []
            if len(scopes) > 1:
                for r in results:
                    r.metadata.setdefault("kb_id", scope.kb_id)
            if len(scopes) > 1:
                _pipeline_log.info(
                    "vector_scope_result",
                    kb_id=scope.kb_id,
                    collection=scope.collection_name,
                    query_len=len(query),
                    results=len(results),
                )
            return results

        batches = await asyncio.gather(
            *(_search_one(query, scope) for query in queries for scope in scopes)
        )
        merged = [item for batch in batches for item in batch]
        # Interleave results across KBs so that round-robin bounding in
        # run_search_channels gives each KB fair representation regardless
        # of absolute score differences.
        if len(scopes) > 1:
            merged = _interleave_by_kb(merged)
        return merged

    async def _search_keyword_scopes(
        self,
        queries: list[str],
        scopes: list[RetrievalScope],
        *,
        top_k: int,
    ) -> list[SearchResult]:
        async def _search_one(query: str, scope: RetrievalScope) -> list[SearchResult]:
            results = await self.es.search(query, scope.kb_id, top_k=top_k)
            if len(scopes) > 1:
                for r in results:
                    r.metadata.setdefault("kb_id", scope.kb_id)
            return results

        batches = await asyncio.gather(
            *(_search_one(query, scope) for query in queries for scope in scopes)
        )
        merged = [item for batch in batches for item in batch]
        if len(scopes) > 1:
            merged = _interleave_by_kb(merged)
        return merged

    async def _search_graph_scopes(
        self,
        queries: list[str],
        scopes: list[RetrievalScope],
        *,
        top_k: int,
        timeout_seconds: float,
    ) -> list[SearchResult]:
        async def search_one(query: str, scope: RetrievalScope) -> list[SearchResult]:
            output = await self.native_graph.search(
                query,
                kb_id=scope.kb_id,
                top_k=top_k,
            )
            try:
                response = await asyncio.wait_for(
                    self.graph_client.query_graph(
                        query,
                        top_k=top_k,
                        kb_id=scope.kb_id,
                        collection_name=scope.collection_name,
                        enabled=True,
                    ),
                    timeout=timeout_seconds,
                )
                output.extend(
                    SearchResult(
                        chunk_id=hit.get("chunk_id") or hit.get("id", ""),
                        doc_id=hit.get("doc_id", ""),
                        content=hit.get("content", ""),
                        score=float(hit.get("score", 0.0)),
                        metadata={
                            "graphEntity": hit.get("entity", ""),
                            "knowledgeBaseId": scope.kb_id,
                        },
                    )
                    for hit in response.get("results", [])
                )
            except (TimeoutError, asyncio.TimeoutError):
                _pipeline_log.warning(
                    "lightrag_query_timeout_using_native_graph",
                    query=query[:80],
                    kb_id=scope.kb_id,
                )
            except Exception as exc:
                _pipeline_log.warning(
                    "lightrag_query_failed_using_native_graph",
                    query=query[:80],
                    kb_id=scope.kb_id,
                    error=type(exc).__name__,
                )
            if len(scopes) > 1:
                for r in output:
                    r.metadata.setdefault("kb_id", scope.kb_id)
            return output

        batches = await asyncio.gather(
            *(search_one(query, scope) for query in queries for scope in scopes)
        )
        merged = [item for batch in batches for item in batch]
        if len(scopes) > 1:
            merged = _interleave_by_kb(merged)
        return merged

    def _adjust_budget_for_profile(self, budget: RetrievalBudget, ctx: RAGContext) -> RetrievalBudget:
        """Adjust retrieval budget based on user profile (expertise level + intent distribution).

        - expert users get +3 top_k and +10 max_candidates (deeper retrieval)
        - junior users get default (simpler, faster)
        - users with high analysis intent rate get +2 final_top_k
        """
        profile = ctx.profile
        if not profile:
            return budget

        level = (profile.get("expertise_level") or "").lower()
        per_channel_top_k = budget.per_channel_top_k
        max_candidates = budget.max_candidates
        final_top_k = budget.final_top_k
        if level == "expert":
            per_channel_top_k += 3
            max_candidates += 10
        elif level == "junior":
            # Keep defaults for simplicity
            pass

        intent_dist = profile.get("intent_distribution") or {}
        # If analysis/reasoning intents dominate, give more final context
        analysis_rate = intent_dist.get("analysis", 0) + intent_dist.get("comparison", 0)
        if analysis_rate > 0.3:
            final_top_k = min(final_top_k + 2, 10)

        budget = replace(
            budget,
            per_channel_top_k=per_channel_top_k,
            max_candidates=max_candidates,
            final_top_k=final_top_k,
        )

        _pipeline_log.info(
            "profile_budget_adjusted",
            level=level,
            per_channel_top_k=budget.per_channel_top_k,
            max_candidates=budget.max_candidates,
            final_top_k=budget.final_top_k,
        )
        return budget

    async def run(self, ctx: RAGContext) -> RAGResult:
        t0 = time.time()
        trace_id = ctx.trace_run_id
        _tenant = ctx.tenant_id or "default"
        budget = RetrievalBudget(
            per_channel_top_k=get_hyperparam_typed(
                "retrieval_per_channel_top_k", settings.retrieval_per_channel_top_k,
                tenant_id=_tenant,
            ),
            max_candidates=get_hyperparam_typed(
                "retrieval_max_candidates", settings.retrieval_max_candidates,
                tenant_id=_tenant,
            ),
            final_top_k=get_hyperparam_typed(
                "retrieval_final_top_k", settings.retrieval_final_top_k,
                tenant_id=_tenant,
            ),
            channel_timeout_ms=get_hyperparam_typed(
                "retrieval_channel_timeout_ms", settings.retrieval_channel_timeout_ms,
                tenant_id=_tenant,
            ),
            total_timeout_ms=get_hyperparam_typed(
                "retrieval_total_timeout_ms", settings.retrieval_total_timeout_ms,
                tenant_id=_tenant,
            ),
            context_max_chars=get_hyperparam_typed(
                "retrieval_context_max_chars", settings.retrieval_context_max_chars,
                tenant_id=_tenant,
            ),
            context_max_tokens=get_hyperparam_typed(
                "retrieval_context_max_tokens", settings.retrieval_context_max_tokens,
                tenant_id=_tenant,
            ),
            max_subqueries=get_hyperparam_typed(
                "query_decomposition_max_queries", settings.query_decomposition_max_queries,
                tenant_id=_tenant,
            ),
        )
        budget = self._adjust_budget_for_profile(budget, ctx)
        _pipeline_log.info(
            "retrieval_budget_final",
            final_top_k=budget.final_top_k,
            per_channel_top_k=budget.per_channel_top_k,
            max_candidates=budget.max_candidates,
            context_max_tokens=budget.context_max_tokens,
            context_max_chars=budget.context_max_chars,
        )
        non_evidence_text = "\n".join(
            [
                ctx.question,
                *[
                    str(item.get("content", ""))
                    for item in (ctx.history or [])
                ],
                *[
                    str(item.get("content", ""))
                    for item in (ctx.memories or [])
                ],
                str(ctx.profile or ""),
            ]
        )
        reserved_tokens = (
            estimate_tokens(non_evidence_text)
            + settings.llm_max_output_tokens
            + settings.llm_prompt_reserve_tokens
        )
        available_evidence_tokens = max(
            1,
            min(
                budget.context_max_tokens,
                settings.llm_context_window_tokens - reserved_tokens,
            ),
        )
        # Scale token budget with final_top_k so select_context doesn't
        # silently truncate chunks due to a fixed token cap.  Estimate 450
        # tokens per chunk as a conservative average.
        _per_chunk_tokens = 450
        _target_tokens = budget.final_top_k * _per_chunk_tokens
        available_evidence_tokens = max(available_evidence_tokens, _target_tokens)
        # Never exceed the LLM context window minus already-reserved space.
        available_evidence_tokens = min(
            available_evidence_tokens,
            settings.llm_context_window_tokens - reserved_tokens,
        )
        budget = replace(
            budget,
            context_max_tokens=available_evidence_tokens,
        )
        _pipeline_log.info(
            "context_budget_scaled",
            final_top_k=budget.final_top_k,
            context_max_tokens=budget.context_max_tokens,
            estimated_chunks_fit=budget.context_max_tokens // _per_chunk_tokens,
        )
        if ctx.final_top_k is not None:
            budget = replace(
                budget,
                final_top_k=min(20, max(1, ctx.final_top_k)),
            )
        _pipeline_log.info("rag_pipeline_start", question=ctx.question[:80], kb_id=ctx.kb_id)

        # ─── TTFT optimization: parallel rewrite + intent + speculative search ───
        if settings.ttft_parallel_rewrite_intent or (
            ctx.enable_hyde and settings.hyde_enabled
        ):
            return await self._run_parallel(ctx, budget, t0, trace_id)

        # 1. Query rewrite (sequential fallback, graceful degradation)
        t_rewrite = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            rewrite_result = await asyncio.wait_for(
                _rewrite_for_pipeline(ctx, budget),
                timeout=settings.query_understanding_timeout_sec,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            from app.rag.rewrite import RewriteResult as _RR
            _pipeline_log.warning(
                "rewrite_timeout_fallback",
                error=type(exc).__name__,
                timeout_sec=settings.query_understanding_timeout_sec,
            )
            rewrite_result = _RR(
                original_query=ctx.question,
                normalized_query=ctx.question.strip(),
                rewritten_query=ctx.question,
                subqueries=[ctx.question],
            )
        rewritten = (
            rewrite_result.rewritten_query
            if rewrite_result.rewritten_query != ctx.question
            else None
        )
        t_rewrite_end = datetime.now(timezone.utc).replace(tzinfo=None)
        rewrite_ms = int((t_rewrite_end - t_rewrite).total_seconds() * 1000)
        _pipeline_log.info("rewrite", original=ctx.question[:60], rewritten=(rewritten or "(unchanged)")[:60], took_ms=rewrite_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "rewrite", "query_rewrite",
                                         t_rewrite, t_rewrite_end,
                                         input_data={"query": ctx.question},
                                         output_data={
                                             "normalized": rewrite_result.normalized_query,
                                             "rewritten": rewrite_result.rewritten_query,
                                             "subqueries": rewrite_result.subqueries,
                                             "applied_mappings": rewrite_result.applied_mappings,
                                         })

        # 2. Intent recognition (graceful degradation)
        t_intent = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            intent_resolution = await asyncio.wait_for(
                _intent_for_pipeline(
                    rewrite_result.subqueries or [rewrite_result.rewritten_query],
                    ctx,
                ),
                timeout=settings.query_understanding_timeout_sec,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            from app.rag.intent import IntentResolution as _IR, SubqueryIntent as _SI, IntentMatch as _IM
            _pipeline_log.warning(
                "intent_timeout_fallback",
                error=type(exc).__name__,
                timeout_sec=settings.query_understanding_timeout_sec,
            )
            intent_resolution = _IR(
                subqueries=[
                    _SI(
                        query=rewrite_result.rewritten_query or ctx.question,
                        matches=[_IM(intent_code="general", name="general", score=0.5)],
                    )
                ]
            )
        intent = intent_resolution.to_dict()
        t_intent_end = datetime.now(timezone.utc).replace(tzinfo=None)
        intent_ms = int((t_intent_end - t_intent).total_seconds() * 1000)
        intent_name = intent.get("intent", "unknown") if intent else "unknown"
        _pipeline_log.info("intent", intent=intent_name, took_ms=intent_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "intent", "intent_recognition",
                                         t_intent, t_intent_end,
                                         input_data={"query": rewritten or ctx.question},
                                         output_data=intent)
            if hasattr(self._trace, "update_understanding"):
                await self._trace.update_understanding(
                    trace_id or "",
                    rewrite_query=rewrite_result.rewritten_query,
                    intent=intent_name,
                    metadata={
                        "normalizedQuery": rewrite_result.normalized_query,
                        "subqueries": rewrite_result.subqueries,
                        "appliedMappings": rewrite_result.applied_mappings,
                        "intentResolution": intent,
                    },
                )

        if intent_resolution.needs_guidance:
            model_name, model_base_url, model_api_key = self.model_router.route(
                "general", deep_thinking=False
            )
            return RAGResult(
                question=ctx.question,
                rewrite=rewritten,
                intent=intent,
                context_chunks=[],
                sources=[],
                duration_ms=int((time.time() - t0) * 1000),
                trace_run_id=trace_id,
                model_name=model_name,
                model_base_url=model_base_url,
                model_api_key=model_api_key,
                subqueries=rewrite_result.subqueries,
                applied_mappings=rewrite_result.applied_mappings,
                response_mode="guidance",
                direct_response=intent_resolution.guidance_prompt,
            )
        if intent_resolution.system_only:
            model_name, model_base_url, model_api_key = self.model_router.route(
                "general", deep_thinking=ctx.deep_thinking
            )
            return RAGResult(
                question=ctx.question,
                rewrite=rewritten,
                intent=intent,
                context_chunks=[],
                sources=[],
                duration_ms=int((time.time() - t0) * 1000),
                trace_run_id=trace_id,
                model_name=model_name,
                model_base_url=model_base_url,
                model_api_key=model_api_key,
                subqueries=rewrite_result.subqueries,
                applied_mappings=rewrite_result.applied_mappings,
                response_mode="system",
            )

        # 3. Resolve collection
        primary_intent = intent_resolution.primary
        collection_name = (
            ctx.collection_name
            or (primary_intent.collection_name if primary_intent else None)
            or (intent.get("collection_name") if intent else None)
            or "default_store"
        )
        _pipeline_log.info("collection_resolved", collection_name=collection_name)
        scopes = self._resolved_scopes(ctx, collection_name)

        search_question = rewritten or ctx.question

        # 4. Bounded decomposition + concurrent multi-channel search
        subqueries = rewrite_result.subqueries
        if not subqueries:
            subqueries = [search_question]
        t_search = datetime.now(timezone.utc).replace(tzinfo=None)

        async def vector_search() -> list[SearchResult]:
            return await self._search_vector_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
            )

        async def keyword_search() -> list[SearchResult]:
            return await self._search_keyword_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
            )

        async def graph_search() -> list[SearchResult]:
            return await self._search_graph_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
                timeout_seconds=max(
                    0.5,
                    min(1.5, budget.channel_timeout_ms / 2000),
                ),
            )

        requested_channels = set(intent_resolution.search_channels)
        if "bm25" in requested_channels:
            requested_channels.add("keyword")
        channels = {}
        if not requested_channels or "vector" in requested_channels:
            channels["vector"] = vector_search
        if settings.es_enabled and (
            not requested_channels or "keyword" in requested_channels
        ):
            channels["keyword"] = keyword_search
        graph_enabled = (
            settings.graph_enabled if ctx.graph_rag is None else ctx.graph_rag
        )
        global_scope = ctx.kb_id is None and bool(ctx.retrieval_scopes)
        if graph_enabled and (
            global_scope or not requested_channels or "graph" in requested_channels
        ):
            channels["graph"] = graph_search
        guarded_channels = {}
        for name, operation in channels.items():
            breaker = self._channel_breakers[name]

            async def guarded_call(op=operation, channel_breaker=breaker):
                return await channel_breaker.call(op)

            guarded_channels[name] = guarded_call
        channels = guarded_channels
        channel_results, channel_statuses = await run_search_channels(channels, budget)
        active_channel_names = [
            name for name, items in channel_results.items() if items
        ]
        all_results = [channel_results[name] for name in active_channel_names]

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
        if all_results:
            # Log pre-fusion per-channel top scores
            for i, channel_results in enumerate(all_results):
                if channel_results:
                    top_scores = [f"{r.score:.4f}" for r in channel_results[:3]]
                    _pipeline_log.info("rrf_pre_fusion_channel", channel=i, count=len(channel_results), top_scores=top_scores)
            weights = {}
            for item in settings.retrieval_channel_weights.split(","):
                name, _, value = item.partition(":")
                if name.strip() and value.strip():
                    try:
                        weights[name.strip()] = float(value)
                    except ValueError:
                        continue
            merged = rrf_fusion(
                *all_results,
                weights=weights,
                channel_names=active_channel_names,
            )
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

        # 6.5 Expand neighbors — pull ±window chunks from the same document
        if ctx.enable_neighbor_expansion:
            t_neighbor_start = datetime.now(timezone.utc).replace(tzinfo=None)
            neighbor_before = len(deduped)
            deduped = await self._expand_neighbors(deduped, kb_id=ctx.kb_id, window=2)
            neighbor_after = len(deduped)
            t_neighbor_end = datetime.now(timezone.utc).replace(tzinfo=None)
            neighbor_ms = int((t_neighbor_end - t_neighbor_start).total_seconds() * 1000)
            _pipeline_log.info(
                "neighbor_expansion",
                before=neighbor_before,
                after=neighbor_after,
                added=neighbor_after - neighbor_before,
                took_ms=neighbor_ms,
            )
            if self._trace:
                await self._trace.trace_node(
                    trace_id or "",
                    "postprocess",
                    "neighbor_expansion",
                    t_neighbor_start,
                    t_neighbor_end,
                    input_data={
                        "before_count": neighbor_before,
                        "window": 2,
                        "top_n_anchors": 10,
                    },
                    output_data={
                        "after_count": neighbor_after,
                        "neighbors_added": neighbor_after - neighbor_before,
                    },
                )

        post_process_scopes = self._resolved_scopes(
            ctx, ctx.collection_name or "default_store"
        )
        allowed_kb_ids = [scope.kb_id for scope in post_process_scopes]
        if allowed_kb_ids:
            principal = Principal(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                department_id=ctx.department_id,
                role=ctx.role,
            )
            async with async_session_factory() as session:
                if len(allowed_kb_ids) == 1:
                    deduped = await filter_authorized_results(
                        session, principal, deduped, kb_id=allowed_kb_ids[0]
                    )
                else:
                    deduped = await filter_authorized_results(
                        session, principal, deduped, kb_ids=allowed_kb_ids
                    )
        else:
            deduped = []
        recall_count = len(deduped)

        # 7. Rerank, relevance rejection, and context packing
        t_rerank = datetime.now(timezone.utc).replace(tzinfo=None)
        # Log pre-rerank order
        pre_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in deduped[:5]]
        _pipeline_log.info("rerank_pre", candidates=recall_count, top_ids=pre_rerank_ids)
        # When neighbor expansion is active, allow the full candidate pool
        # (original + neighbors) into the reranker so that cross-encoder scoring
        # can independently judge each chunk; select_context will still enforce
        # the final budget.
        rerank_top_n = (
            budget.max_candidates
            if ctx.enable_neighbor_expansion
            else min(budget.max_candidates, max(budget.final_top_k * 2, budget.final_top_k))
        )
        reranked = await self.reranker.rerank(
            search_question,
            deduped,
            top_n=rerank_top_n,
        )
        # Per-KB quota: guarantee each KB minimum representation in cross-KB mode
        kb_quota = (
            settings.retrieval_kb_min_quota
            if settings.retrieval_kb_quota_enabled and len(ctx.retrieval_scopes) > 1
            else None
        )
        reranked, retrieval_decision = select_context(
            reranked,
            budget,
            min_score=relevance_threshold(reranked, tenant_id=_tenant),
            kb_quota=kb_quota,
            fallback_pool=deduped if kb_quota else None,
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
        _kb_name_map = {s.kb_id: s.kb_name for s in ctx.retrieval_scopes if s.kb_name}
        chunks = [
            {
                "content": r.content,
                "chunk_id": r.chunk_id,
                "score": r.score,
                "fusionScore": r.metadata.get("fusionScore"),
                "rerankScore": r.metadata.get("rerank_score"),
                "channelScores": r.metadata.get("channelScores", {}),
                "matchedChannels": r.metadata.get("matchedChannels", []),
                "blockType": r.block_type,
                "pageStart": r.page_start,
                "pageEnd": r.page_end,
                "neighborOf": r.metadata.get("neighbor_of") or [],
                "kbId": r.metadata.get("kb_id") or "",
                "kbName": _kb_name_map.get(r.metadata.get("kb_id", ""), ""),
                "docName": r.doc_name or "",
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
                "fusionScore": r.metadata.get("fusionScore"),
                "rerankScore": r.metadata.get("rerank_score"),
                "channelScores": r.metadata.get("channelScores", {}),
                "matchedChannels": r.metadata.get("matchedChannels", []),
                "blockType": r.block_type,
                "pageStart": r.page_start,
                "pageEnd": r.page_end,
                "bboxes": r.bboxes,
                "assets": r.assets,
                "neighborOf": r.metadata.get("neighbor_of") or [],
                "fileType": r.file_type or "",
                "kbId": r.metadata.get("kb_id") or "",
                "kbName": _kb_name_map.get(r.metadata.get("kb_id", ""), ""),
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
            prompt_template=primary_intent.prompt_template if primary_intent else None,
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
            applied_mappings=rewrite_result.applied_mappings,
        )

    async def _run_parallel(
        self, ctx: RAGContext, budget: RetrievalBudget, t0: float, trace_id: str | None
    ) -> RAGResult:
        """TTFT-optimized pipeline: rewrite + intent + speculative search run concurrently."""
        t_parallel_start = datetime.now(timezone.utc).replace(tzinfo=None)

        # Launch rewrite and intent concurrently
        rewrite_task = asyncio.create_task(
            _rewrite_for_pipeline(ctx, budget)
        )
        # Intent runs on original query in parallel (doesn't need rewrite result)
        intent_task = asyncio.create_task(
            _intent_for_pipeline([ctx.question], ctx)
        )

        # Speculative vector search with original query (while LLM calls in flight)
        speculative_task: asyncio.Task | None = None
        collection_name = ctx.collection_name or "default_store"
        speculative_scopes = self._resolved_scopes(ctx, collection_name)
        speculative_scope = (
            speculative_scopes[0] if len(speculative_scopes) == 1 else None
        )
        speculative_collection_name = (
            speculative_scope.collection_name
            if speculative_scope
            else collection_name
        )
        if settings.ttft_speculative_search and speculative_scope:
            async def _speculative_vector():
                try:
                    return await self._search_vector(
                        ctx.question,
                        speculative_scope.collection_name,
                        top_k=budget.per_channel_top_k,
                        embedding_model=speculative_scope.embedding_model,
                    )
                except Exception:
                    return []
            speculative_task = asyncio.create_task(_speculative_vector())

        # HyDE: generate hypothetical document in parallel with rewrite + intent
        hyde_task: asyncio.Task | None = None
        t_hyde_start: datetime | None = None
        if ctx.enable_hyde and settings.hyde_enabled:
            from app.rag.hyde import generate_hypothetical_document
            t_hyde_start = datetime.now(timezone.utc).replace(tzinfo=None)
            hyde_task = asyncio.create_task(
                generate_hypothetical_document(
                    ctx.question, history=ctx.history
                )
            )

        # Await rewrite + intent (graceful degradation on timeout)
        try:
            rewrite_result, intent_resolution = await asyncio.wait_for(
                asyncio.gather(rewrite_task, intent_task),
                timeout=settings.query_understanding_timeout_sec,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            # Cancel lingering tasks to avoid leaked coroutines
            for task in (rewrite_task, intent_task):
                if not task.done():
                    task.cancel()
            _pipeline_log.warning(
                "parallel_rewrite_intent_timeout_fallback",
                error=type(exc).__name__,
                timeout_sec=settings.query_understanding_timeout_sec,
            )
            # Fallback: use original query with default intent
            from app.rag.rewrite import RewriteResult as _RR
            from app.rag.intent import IntentResolution as _IR, SubqueryIntent as _SI, IntentMatch as _IM

            rewrite_result = _RR(
                original_query=ctx.question,
                normalized_query=ctx.question.strip(),
                rewritten_query=ctx.question,
                subqueries=[ctx.question],
            )
            intent_resolution = _IR(
                subqueries=[
                    _SI(
                        query=ctx.question,
                        matches=[_IM(intent_code="general", name="general", score=0.5)],
                    )
                ]
            )

        t_parallel_end = datetime.now(timezone.utc).replace(tzinfo=None)
        parallel_ms = int((t_parallel_end - t_parallel_start).total_seconds() * 1000)

        rewritten = (
            rewrite_result.rewritten_query
            if rewrite_result.rewritten_query != ctx.question
            else None
        )
        intent = intent_resolution.to_dict()
        intent_name = intent.get("intent", "unknown") if intent else "unknown"
        _pipeline_log.info(
            "parallel_rewrite_intent",
            rewrite=(rewritten or "(unchanged)")[:60],
            intent=intent_name,
            took_ms=parallel_ms,
        )

        # Trace
        if self._trace:
            await self._trace.trace_node(
                trace_id or "", "rewrite", "query_rewrite_parallel",
                t_parallel_start, t_parallel_end,
                input_data={"query": ctx.question},
                output_data={
                    "normalized": rewrite_result.normalized_query,
                    "rewritten": rewrite_result.rewritten_query,
                    "subqueries": rewrite_result.subqueries,
                    "applied_mappings": rewrite_result.applied_mappings,
                },
            )
            await self._trace.trace_node(
                trace_id or "", "intent", "intent_recognition_parallel",
                t_parallel_start, t_parallel_end,
                input_data={"query": ctx.question},
                output_data=intent,
            )
            if hasattr(self._trace, "update_understanding"):
                await self._trace.update_understanding(
                    trace_id or "",
                    rewrite_query=rewrite_result.rewritten_query,
                    intent=intent_name,
                    metadata={
                        "normalizedQuery": rewrite_result.normalized_query,
                        "subqueries": rewrite_result.subqueries,
                        "appliedMappings": rewrite_result.applied_mappings,
                        "intentResolution": intent,
                        "parallel": True,
                    },
                )

        # Early-exit paths (same as sequential)
        if intent_resolution.needs_guidance:
            if speculative_task:
                speculative_task.cancel()
            if hyde_task:
                hyde_task.cancel()
            model_name, model_base_url, model_api_key = self.model_router.route(
                "general", deep_thinking=False
            )
            return RAGResult(
                question=ctx.question, rewrite=rewritten, intent=intent,
                context_chunks=[], sources=[],
                duration_ms=int((time.time() - t0) * 1000),
                trace_run_id=trace_id,
                model_name=model_name, model_base_url=model_base_url,
                model_api_key=model_api_key,
                subqueries=rewrite_result.subqueries,
                applied_mappings=rewrite_result.applied_mappings,
                response_mode="guidance",
                direct_response=intent_resolution.guidance_prompt,
            )
        if intent_resolution.system_only:
            if speculative_task:
                speculative_task.cancel()
            if hyde_task:
                hyde_task.cancel()
            model_name, model_base_url, model_api_key = self.model_router.route(
                "general", deep_thinking=ctx.deep_thinking
            )
            return RAGResult(
                question=ctx.question, rewrite=rewritten, intent=intent,
                context_chunks=[], sources=[],
                duration_ms=int((time.time() - t0) * 1000),
                trace_run_id=trace_id,
                model_name=model_name, model_base_url=model_base_url,
                model_api_key=model_api_key,
                subqueries=rewrite_result.subqueries,
                applied_mappings=rewrite_result.applied_mappings,
                response_mode="system",
            )

        # Resolve collection
        primary_intent = intent_resolution.primary
        collection_name = (
            ctx.collection_name
            or (primary_intent.collection_name if primary_intent else None)
            or (intent.get("collection_name") if intent else None)
            or "default_store"
        )
        scopes = self._resolved_scopes(ctx, collection_name)

        # Build subqueries from rewrite result
        search_question = rewritten or ctx.question
        subqueries = rewrite_result.subqueries or [search_question]

        # ─── Multi-channel search (reuse speculative results for vector) ───
        t_search = datetime.now(timezone.utc).replace(tzinfo=None)

        # Collect speculative results if available
        speculative_results: list[SearchResult] = []
        if speculative_task:
            try:
                speculative_results = await asyncio.wait_for(speculative_task, timeout=0.1)
            except (asyncio.TimeoutError, Exception):
                speculative_results = []

        # Await HyDE result (already running in parallel; this is just a join)
        # hyde.py already has its own timeout handling, so we just await directly.
        hyde_result = None
        if hyde_task:
            try:
                hyde_result = await hyde_task
            except Exception:
                hyde_result = None
            t_hyde_end = datetime.now(timezone.utc).replace(tzinfo=None)
            if self._trace and hyde_result:
                await self._trace.trace_node(
                    trace_id or "", "hyde", "hypothetical_doc_generation",
                    t_hyde_start or t_hyde_end, t_hyde_end,
                    input_data={"query": ctx.question},
                    output_data={
                        "doc_length": len(hyde_result.hypothetical_doc),
                        "model": hyde_result.model_used,
                        "timed_out": hyde_result.timed_out,
                        "duration_ms": hyde_result.duration_ms,
                    },
                )

        async def vector_search() -> list[SearchResult]:
            # If speculative results exist and query unchanged, reuse them
            if speculative_results and can_reuse_speculative_results(
                speculative_collection=speculative_collection_name,
                final_collection=collection_name,
                original_query=ctx.question,
                search_query=search_question,
            ):
                return speculative_results
            # Otherwise search with all subqueries
            results = await self._search_vector_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
            )
            # Merge speculative results if query was rewritten
            if speculative_results and rewritten:
                results = speculative_results + results
            return results

        async def keyword_search() -> list[SearchResult]:
            return await self._search_keyword_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
            )

        async def graph_search() -> list[SearchResult]:
            return await self._search_graph_scopes(
                subqueries,
                scopes,
                top_k=budget.per_channel_top_k,
                timeout_seconds=max(
                    0.5,
                    min(1.5, budget.channel_timeout_ms / 2000),
                ),
            )

        requested_channels = set(intent_resolution.search_channels)
        if "bm25" in requested_channels:
            requested_channels.add("keyword")
        channels = {}
        if not requested_channels or "vector" in requested_channels:
            channels["vector"] = vector_search
        if settings.es_enabled and (not requested_channels or "keyword" in requested_channels):
            channels["keyword"] = keyword_search
        graph_enabled = settings.graph_enabled if ctx.graph_rag is None else ctx.graph_rag
        global_scope = ctx.kb_id is None and bool(ctx.retrieval_scopes)
        if graph_enabled and (
            global_scope or not requested_channels or "graph" in requested_channels
        ):
            channels["graph"] = graph_search

        # HyDE vector channel — additional retrieval channel that searches with
        # the hypothetical document instead of the raw query.
        if hyde_result and hyde_result.hypothetical_doc:
            _hyde_doc = hyde_result.hypothetical_doc

            async def hyde_vector_search() -> list[SearchResult]:
                return await self._search_vector_scopes(
                    [_hyde_doc],
                    scopes,
                    top_k=budget.per_channel_top_k,
                )

            channels["hyde_vector"] = hyde_vector_search

        guarded_channels = {}
        for name, operation in channels.items():
            breaker = self._channel_breakers[name]

            async def guarded_call(op=operation, channel_breaker=breaker):
                return await channel_breaker.call(op)

            guarded_channels[name] = guarded_call
        channels = guarded_channels
        channel_results, channel_statuses = await run_search_channels(channels, budget)
        active_channel_names = [name for name, items in channel_results.items() if items]
        all_results = [channel_results[name] for name in active_channel_names]

        t_search_end = datetime.now(timezone.utc).replace(tzinfo=None)
        search_ms = int((t_search_end - t_search).total_seconds() * 1000)
        total_hits = sum(len(r) for r in all_results)
        _pipeline_log.info("multi_search_complete", channels=len(all_results), total_hits=total_hits, took_ms=search_ms)
        if self._trace and trace_id:
            await self._trace.trace_node(
                trace_id, "retrieval", "parallel_channels",
                t_search, t_search_end,
                input_data={"subquery_count": len(subqueries), "channels": list(channels)},
                output_data={
                    "total_hits": total_hits,
                    "statuses": {name: status.__dict__ for name, status in channel_statuses.items()},
                },
            )

        # ─── Post-processing: fusion → dedup → rerank (same as sequential) ───
        return await self._post_process(
            ctx, budget, t0, trace_id,
            rewrite_result=rewrite_result,
            rewritten=rewritten,
            intent=intent,
            intent_name=intent_name,
            search_question=search_question,
            subqueries=subqueries,
            all_results=all_results,
            active_channel_names=active_channel_names,
            channel_statuses=channel_statuses,
            search_ms=search_ms,
            total_hits=total_hits,
            parallel_ms=parallel_ms,
            prompt_template=primary_intent.prompt_template if primary_intent else None,
            hyde_result=hyde_result,
        )

    async def _post_process(
        self,
        ctx: RAGContext,
        budget: RetrievalBudget,
        t0: float,
        trace_id: str | None,
        *,
        rewrite_result,
        rewritten: str | None,
        intent: dict | None,
        intent_name: str,
        search_question: str,
        subqueries: list[str],
        all_results: list[list[SearchResult]],
        active_channel_names: list[str],
        channel_statuses: dict,
        search_ms: int,
        total_hits: int,
        parallel_ms: int = 0,
        prompt_template: str | None = None,
        hyde_result=None,
    ) -> RAGResult:
        """Shared post-processing: fusion → dedup → filter → rerank → build result."""
        # 5. RRF fusion
        t_fuse = datetime.now(timezone.utc).replace(tzinfo=None)
        if all_results:
            for i, ch_results in enumerate(all_results):
                if ch_results:
                    top_scores = [f"{r.score:.4f}" for r in ch_results[:3]]
                    _pipeline_log.info("rrf_pre_fusion_channel", channel=i, count=len(ch_results), top_scores=top_scores)
            weights = {}
            for item in settings.retrieval_channel_weights.split(","):
                name, _, value = item.partition(":")
                if name.strip() and value.strip():
                    try:
                        weights[name.strip()] = float(value)
                    except ValueError:
                        continue
            # Inject HyDE channel weight if it participated in retrieval
            if "hyde_vector" in active_channel_names and "hyde_vector" not in weights:
                weights["hyde_vector"] = settings.hyde_channel_weight
            merged = rrf_fusion(
                *all_results, weights=weights, channel_names=active_channel_names
            )
        else:
            merged = []
        t_fuse_end = datetime.now(timezone.utc).replace(tzinfo=None)
        fusion_ms = int((t_fuse_end - t_fuse).total_seconds() * 1000)
        _pipeline_log.info("rrf_fusion", input_channels=len(all_results), input_total=total_hits, output_count=len(merged), took_ms=fusion_ms)
        # Diagnostic: KB distribution after fusion
        _fuse_dist: dict[str, int] = {}
        for _r in merged:
            _kb = _r.metadata.get("kb_id", "")
            if _kb:
                _fuse_dist[_kb] = _fuse_dist.get(_kb, 0) + 1
        if _fuse_dist:
            _pipeline_log.info("kb_dist_after_fusion", dist=_fuse_dist, total=len(merged))
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
        # Diagnostic: KB distribution after dedup+filter
        _dedup_dist: dict[str, int] = {}
        for _r in deduped:
            _kb = _r.metadata.get("kb_id", "")
            if _kb:
                _dedup_dist[_kb] = _dedup_dist.get(_kb, 0) + 1
        if _dedup_dist:
            _pipeline_log.info("kb_dist_after_dedup_filter", dist=_dedup_dist, total=len(deduped))

        await self._resolve_metadata(deduped, kb_id=ctx.kb_id)

        # 6.5 Neighbor expansion
        if ctx.enable_neighbor_expansion:
            t_neighbor_start = datetime.now(timezone.utc).replace(tzinfo=None)
            neighbor_before = len(deduped)
            deduped = await self._expand_neighbors(deduped, kb_id=ctx.kb_id, window=2)
            neighbor_after = len(deduped)
            t_neighbor_end = datetime.now(timezone.utc).replace(tzinfo=None)
            neighbor_ms = int((t_neighbor_end - t_neighbor_start).total_seconds() * 1000)
            _pipeline_log.info("neighbor_expansion", before=neighbor_before, after=neighbor_after, added=neighbor_after - neighbor_before, took_ms=neighbor_ms)
            if self._trace:
                await self._trace.trace_node(
                    trace_id or "", "postprocess", "neighbor_expansion",
                    t_neighbor_start, t_neighbor_end,
                    input_data={"before_count": neighbor_before, "window": 2, "top_n_anchors": 10},
                    output_data={"after_count": neighbor_after, "neighbors_added": neighbor_after - neighbor_before},
                )

        post_process_scopes = self._resolved_scopes(
            ctx, ctx.collection_name or "default_store"
        )
        allowed_kb_ids = [scope.kb_id for scope in post_process_scopes]
        if allowed_kb_ids:
            principal = Principal(
                user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                department_id=ctx.department_id, role=ctx.role,
            )
            async with async_session_factory() as session:
                if len(allowed_kb_ids) == 1:
                    deduped = await filter_authorized_results(
                        session, principal, deduped, kb_id=allowed_kb_ids[0]
                    )
                else:
                    deduped = await filter_authorized_results(
                        session, principal, deduped, kb_ids=allowed_kb_ids
                    )
        else:
            deduped = []
        recall_count = len(deduped)
        # Diagnostic: KB distribution after ACL filter
        _acl_dist: dict[str, int] = {}
        for _r in deduped:
            _kb = _r.metadata.get("kb_id", "")
            if _kb:
                _acl_dist[_kb] = _acl_dist.get(_kb, 0) + 1
        if _acl_dist:
            _pipeline_log.info("kb_dist_after_acl", dist=_acl_dist, total=len(deduped))

        # 7. Rerank
        t_rerank = datetime.now(timezone.utc).replace(tzinfo=None)
        pre_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in deduped[:5]]
        _pipeline_log.info("rerank_pre", candidates=recall_count, top_ids=pre_rerank_ids)
        rerank_top_n = (
            budget.max_candidates
            if ctx.enable_neighbor_expansion
            else min(budget.max_candidates, max(budget.final_top_k * 2, budget.final_top_k))
        )
        reranked = await self.reranker.rerank(search_question, deduped, top_n=rerank_top_n)
        # Per-KB quota: guarantee each KB minimum representation in cross-KB mode
        kb_quota = (
            settings.retrieval_kb_min_quota
            if settings.retrieval_kb_quota_enabled and len(ctx.retrieval_scopes) > 1
            else None
        )
        # Diagnostic: KB distribution before select_context
        if kb_quota:
            _pre_dist: dict[str, int] = {}
            for _r in reranked:
                _kb = _r.metadata.get("kb_id", "")
                if _kb:
                    _pre_dist[_kb] = _pre_dist.get(_kb, 0) + 1
            _pipeline_log.info("kb_quota_pre_select", reranked_count=len(reranked), kb_dist=_pre_dist, kb_quota=kb_quota)
        reranked, retrieval_decision = select_context(
            reranked, budget, min_score=relevance_threshold(reranked, tenant_id=ctx.tenant_id or "default"),
            kb_quota=kb_quota,
            fallback_pool=deduped if kb_quota else None,
        )
        if kb_quota:
            _post_dist: dict[str, int] = {}
            for _r in reranked:
                _kb = _r.metadata.get("kb_id", "")
                if _kb:
                    _post_dist[_kb] = _post_dist.get(_kb, 0) + 1
            _pipeline_log.info("kb_quota_post_select", selected_count=len(reranked), kb_dist=_post_dist)
        t_rerank_end = datetime.now(timezone.utc).replace(tzinfo=None)
        rerank_ms = int((t_rerank_end - t_rerank).total_seconds() * 1000)
        final_count = len(reranked)
        post_rerank_ids = [(r.chunk_id or r.content[:20], f"{r.score:.4f}") for r in reranked]
        _pipeline_log.info("rerank_post", final_count=final_count, top_ids=post_rerank_ids, took_ms=rerank_ms)
        if self._trace:
            await self._trace.trace_node(trace_id or "", "rerank", "rerank",
                                         t_rerank, t_rerank_end,
                                         input_data={"candidate_count": recall_count},
                                         output_data={"reranked_count": final_count})

        # 8. Model routing
        if ctx.deep_thinking and not settings.reasoning_model:
            _pipeline_log.warning("deep_thinking_requested_but_no_reasoning_model")
        model_name, model_base_url, model_api_key = self.model_router.route(
            intent_name, deep_thinking=ctx.deep_thinking
        )

        # 9. Build chunks
        _kb_name_map = {s.kb_id: s.kb_name for s in ctx.retrieval_scopes if s.kb_name}
        chunks = [
            {
                "content": r.content, "chunk_id": r.chunk_id, "score": r.score,
                "fusionScore": r.metadata.get("fusionScore"),
                "rerankScore": r.metadata.get("rerank_score"),
                "channelScores": r.metadata.get("channelScores", {}),
                "matchedChannels": r.metadata.get("matchedChannels", []),
                "blockType": r.block_type, "pageStart": r.page_start, "pageEnd": r.page_end,
                "neighborOf": r.metadata.get("neighbor_of") or [],
                "kbId": r.metadata.get("kb_id") or "",
                "kbName": _kb_name_map.get(r.metadata.get("kb_id", ""), ""),
                "docName": r.doc_name or "",
            }
            for r in reranked
        ]
        sources = [
            {
                "documentId": r.doc_id, "chunkId": r.chunk_id,
                "docName": r.doc_name or "unknown", "chunkIndex": r.chunk_index,
                "content": r.content[:300], "score": r.score,
                "fusionScore": r.metadata.get("fusionScore"),
                "rerankScore": r.metadata.get("rerank_score"),
                "channelScores": r.metadata.get("channelScores", {}),
                "matchedChannels": r.metadata.get("matchedChannels", []),
                "blockType": r.block_type, "pageStart": r.page_start,
                "pageEnd": r.page_end, "bboxes": r.bboxes, "assets": r.assets,
                "neighborOf": r.metadata.get("neighbor_of") or [],
                "fileType": r.file_type or "",
                "kbId": r.metadata.get("kb_id") or "",
                "kbName": _kb_name_map.get(r.metadata.get("kb_id", ""), ""),
            }
            for r in reranked
        ]

        duration = int((time.time() - t0) * 1000)
        _pipeline_log.info(
            "rag_pipeline_complete",
            question=ctx.question[:60],
            parallel_phase=parallel_ms,
            search=search_ms, fusion=fusion_ms, dedup=dedup_ms, rerank=rerank_ms,
            total_ms=duration, recall_count=recall_count, final_count=final_count,
            answerable=retrieval_decision.answerable,
            rejection_reason=retrieval_decision.reason or None,
            channel_statuses={name: status.__dict__ for name, status in channel_statuses.items()},
            subqueries=subqueries,
        )

        return RAGResult(
            question=ctx.question, rewrite=rewritten, intent=intent,
            context_chunks=chunks, sources=sources,
            duration_ms=duration, trace_run_id=trace_id,
            model_name=model_name, model_base_url=model_base_url,
            model_api_key=model_api_key,
            answerable=retrieval_decision.answerable,
            rejection_reason=retrieval_decision.reason or None,
            channel_statuses={name: status.__dict__ for name, status in channel_statuses.items()},
            subqueries=subqueries,
            applied_mappings=rewrite_result.applied_mappings,
            prompt_template=prompt_template,
            hyde_doc=(hyde_result.hypothetical_doc if hyde_result and hyde_result.hypothetical_doc else None),
            hyde_meta={
                "model": hyde_result.model_used,
                "durationMs": hyde_result.duration_ms,
                "timedOut": hyde_result.timed_out,
            } if hyde_result else {},
        )

    async def _expand_neighbors(
        self,
        results: list[SearchResult],
        *,
        kb_id: str | None = None,
        window: int = 2,
    ) -> list[SearchResult]:
        """Pull ±*window* adjacent chunks from the same document for each recalled chunk.

        Neighbor chunks are flat-appended to the result list (方案A) so they
        participate in downstream reranking.  Deduplication by chunk_id ensures
        overlapping neighbours (from multiple recalled chunks in the same document)
        are only included once.
        """
        if not results or window < 1:
            return results

        import logging
        _log = logging.getLogger("flavorag.rag.pipeline")

        # Only expand from top-N scored results to control neighbor budget
        anchor_candidates = sorted(results, key=lambda r: r.score, reverse=True)
        anchors: list[tuple[str, int, float]] = []
        for r in anchor_candidates[:10]:
            if r.doc_id and r.chunk_index >= 0:
                anchors.append((r.doc_id, r.chunk_index, r.score))
        existing_ids: set[str] = {r.chunk_id for r in results if r.chunk_id}

        if not anchors:
            return results

        # Batch query: for each doc, fetch neighbours in one query
        doc_ranges: dict[str, list[tuple[int, int, float]]] = {}
        for doc_id, ci, score in anchors:
            doc_ranges.setdefault(doc_id, []).append((ci, score))

        try:
            async with async_session_factory() as session:

                all_neighbor_rows: list = []
                seen_neighbor_ids: set[str] = set(existing_ids)

                for doc_id, ranges in doc_ranges.items():
                    # Build OR conditions: chunk_index BETWEEN ci-window AND ci+window
                    conditions = []
                    for ci, _score in ranges:
                        lo = max(0, ci - window)
                        hi = ci + window
                        conditions.append(KnowledgeChunk.chunk_index.between(lo, hi))

                    if not conditions:
                        continue

                    rows = await session.execute(
                        select(
                            KnowledgeChunk.id,
                            KnowledgeChunk.chunk_index,
                            KnowledgeChunk.doc_id,
                            KnowledgeChunk.content,
                            KnowledgeChunk.embedding_content,
                            KnowledgeChunk.block_type,
                            KnowledgeChunk.page_start,
                            KnowledgeChunk.page_end,
                            KnowledgeChunk.bbox_json,
                            KnowledgeChunk.metadata_json,
                            KnowledgeDocument.doc_name,
                        )
                        .outerjoin(
                            KnowledgeDocument,
                            KnowledgeChunk.doc_id == KnowledgeDocument.id,
                        )
                        .where(
                            KnowledgeChunk.doc_id == doc_id,
                            KnowledgeChunk.deleted == 0,
                            KnowledgeChunk.enabled == 1,
                            or_(*conditions),
                            *([KnowledgeChunk.kb_id == kb_id] if kb_id else []),
                        )
                    )
                    for row in rows:
                        chunk_id = row.id
                        if chunk_id in seen_neighbor_ids:
                            continue
                        seen_neighbor_ids.add(chunk_id)
                        all_neighbor_rows.append(row)

                if not all_neighbor_rows:
                    return results

                # For each neighbor, find the closest parent score and attribution
                doc_anchors: dict[str, dict[int, float]] = {}
                chunk_id_by_doc_ci: dict[str, dict[int, str]] = {}
                for doc_id, ci, score in anchors:
                    doc_anchors.setdefault(doc_id, {})[ci] = score
                for r in results:
                    if r.doc_id and r.chunk_id:
                        chunk_id_by_doc_ci.setdefault(r.doc_id, {})[r.chunk_index] = r.chunk_id

                neighbors: list[SearchResult] = []
                for row in all_neighbor_rows:
                    parent_scores = doc_anchors.get(row.doc_id, {})
                    # Find closest parent: min distance → best score
                    best_score = 0.0
                    if parent_scores:
                        # Use the parent whose chunk_index is closest
                        closest = min(
                            parent_scores.items(),
                            key=lambda kv: abs(kv[0] - row.chunk_index),
                        )
                        best_score = closest[1] * 0.95

                    # Build neighbor attribution list
                    parent_chunk_ids: list[str] = []
                    parent_cis = doc_anchors.get(row.doc_id, {})
                    for p_ci in parent_cis:
                        if abs(p_ci - row.chunk_index) <= window and p_ci != row.chunk_index:
                            pid = chunk_id_by_doc_ci.get(row.doc_id, {}).get(p_ci)
                            if pid:
                                parent_chunk_ids.append(pid)

                    neighbors.append(
                        SearchResult(
                            chunk_id=row.id,
                            doc_id=row.doc_id,
                            content=row.embedding_content or row.content,
                            score=best_score,
                            chunk_index=row.chunk_index,
                            doc_name=row.doc_name or "unknown",
                            block_type=row.block_type or "",
                            page_start=row.page_start,
                            page_end=row.page_end,
                            bboxes=row.bbox_json or [],
                            metadata={
                                **(row.metadata_json or {}),
                                "neighbor_of": parent_chunk_ids,
                            },
                        )
                    )

                _log.info(
                    "neighbor_expansion_done",
                    original=len(results),
                    neighbors=len(neighbors),
                )
                return results + neighbors

        except Exception as exc:
            _log.warning("neighbor_expansion_failed: %s", exc)
            return results

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
                    ).where(
                        or_(*predicates),
                        KnowledgeChunk.index_status == "ACTIVE",
                    )
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
                        KnowledgeDocument.file_type,
                    )
                    .outerjoin(KnowledgeDocument, KnowledgeChunk.doc_id == KnowledgeDocument.id)
                    .where(
                        or_(*predicates),
                        KnowledgeChunk.deleted == 0,
                        KnowledgeChunk.index_status == "ACTIVE",
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
                        file_type,
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
                        # Keep request-scoped retrieval metadata (RRF attribution,
                        # fusion score, etc.) while enriching it with persisted
                        # chunk metadata. Retrieval values win if a stored key
                        # happens to use the same name.
                        r.metadata = {
                            **(metadata_json or {}),
                            **r.metadata,
                        }
                        r.doc_name = doc_name or "unknown"
                        r.file_type = file_type or ""

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
                            "assetId": row.id,
                            "storageUrl": row.storage_url,
                            "url": f"/api/assets/{row.id}",
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
                        # Replace raw S3 URLs in chunk content with proxy
                        # URLs so that LLM context (and any markdown image
                        # the LLM reproduces) points to the authenticated
                        # proxy endpoint instead of the private bucket.
                        for asset_id in result.metadata.get("asset_ids", []):
                            asset = assets_by_id.get(asset_id)
                            if asset and asset.get("storageUrl"):
                                result.content = result.content.replace(
                                    asset["storageUrl"], asset["url"]
                                )
        except Exception as exc:
            _log.warning("Failed to resolve chunk metadata from PG: %s", exc)
