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
from app.rag.postprocess.fusion import rrf_fusion, deduplicate
from app.rag.search.base import SearchResult

router = APIRouter(prefix="/api/rag/v3", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    kb_id: str | None = None
    collection_name: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)
    channels: list[str] = Field(default=["vector"])


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
    # Resolve collection_name from kb_id if needed
    collection_name = req.collection_name
    if not collection_name and req.kb_id:
        result = await db.execute(
            select(KnowledgeBase.collection_name).where(
                KnowledgeBase.id == req.kb_id,
                KnowledgeBase.deleted == 0,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="知识库不存在")
        collection_name = row

    if not collection_name:
        collection_name = "rag_default_store"

    # Execute search channels
    all_results: list[list[SearchResult]] = []
    milvus = MilvusSearchChannel()

    if "vector" in req.channels:
        vector_results = await milvus.search(
            query=req.query,
            collection_name=collection_name,
            top_k=req.top_k,
        )
        all_results.append(vector_results)

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
        },
    )
