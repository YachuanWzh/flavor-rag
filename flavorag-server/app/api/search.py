"""RAG search API — sematic search endpoint for external clients (e.g., flavor-code)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import get_db
from app.auth.dependencies import get_current_user
from app.models import User, KnowledgeBase
from app.rag.search.vector import MilvusSearchChannel
from app.rag.search.keyword import ESKeywordSearchChannel
from app.rag.postprocess.fusion import rrf_fusion, deduplicate
from app.rag.search.base import SearchResult
from app.config.settings import settings
from app.rag.governance import RetrievalBudget, run_search_channels, select_context
from app.rag.pipeline import RAGPipeline
from app.rag.postprocess.reranker import Reranker
from app.security.access import Permission
from app.security.service import (
    filter_authorized_results,
    kb_access_predicate,
    principal_from_user,
)

router = APIRouter(prefix="/api/rag/v3", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    kb_id: str | None = None
    collection_name: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)
    channels: list[str] = Field(default=["vector", "keyword"])


class ChunkResult(BaseModel):
    chunk_id: str
    content: str
    score: float
    doc_name: str = ""


class SearchResponse(BaseModel):
    code: str = "0"
    message: str = "success"
    data: dict


@router.post("/search")
async def rag_search(
    req: SearchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SearchResponse:
    """Semantic search endpoint.

    Called by flavor-code's RagSearch tool to find relevant code/docs.
    Returns top-K chunks sorted by relevance.
    """
    principal = principal_from_user(user)
    kb_stmt = select(KnowledgeBase).where(
        kb_access_predicate(principal, Permission.READ)
    )
    if req.kb_id:
        kb_stmt = kb_stmt.where(KnowledgeBase.id == req.kb_id)
    elif req.collection_name:
        kb_stmt = kb_stmt.where(KnowledgeBase.collection_name == req.collection_name)
    else:
        kb_stmt = kb_stmt.limit(1)
    kb = (await db.execute(kb_stmt)).scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问")
    collection_name = kb.collection_name

    budget = RetrievalBudget(
        per_channel_top_k=max(req.top_k * 2, req.top_k),
        max_candidates=settings.retrieval_max_candidates,
        final_top_k=req.top_k,
        channel_timeout_ms=settings.retrieval_channel_timeout_ms,
        context_max_chars=settings.retrieval_context_max_chars,
    )
    milvus = MilvusSearchChannel()
    channels = {}
    if "vector" in req.channels:
        channels["vector"] = lambda: milvus.search(
            query=req.query,
            collection_name=collection_name,
            top_k=budget.per_channel_top_k,
        )
    if "keyword" in req.channels and settings.es_enabled:
        es = ESKeywordSearchChannel()
        channels["keyword"] = lambda: es.search(
            query=req.query,
            collection_name=kb.id,
            top_k=budget.per_channel_top_k,
        )
    channel_results, channel_statuses = await run_search_channels(channels, budget)
    all_results = [items for items in channel_results.values() if items]

    # Fuse multi-channel results
    if len(all_results) > 1:
        merged = rrf_fusion(*all_results)
    elif all_results:
        merged = all_results[0]
    else:
        return SearchResponse(
            data={"chunks": [], "total": 0},
        )

    # Deduplicate
    deduped = deduplicate(merged)
    pipeline = RAGPipeline()
    await pipeline._resolve_metadata(deduped, kb_id=kb.id)
    deduped = await filter_authorized_results(
        db, principal, deduped, kb_id=kb.id
    )
    reranked = await Reranker().rerank(
        req.query, deduped, top_n=max(req.top_k * 2, req.top_k)
    )
    deduped, decision = select_context(
        reranked,
        budget,
        min_score=settings.retrieval_min_relevance_score,
    )

    chunks = [
        ChunkResult(
            chunk_id=r.chunk_id,
            content=r.content,
            score=r.score,
            doc_name=r.doc_name,
        )
        for r in deduped
    ]

    return SearchResponse(
        data={
            "chunks": [c.model_dump() for c in chunks],
            "total": len(chunks),
            "answerable": decision.answerable,
            "rejectionReason": decision.reason or None,
            "channels": {
                name: status.__dict__ for name, status in channel_statuses.items()
            },
        },
    )
