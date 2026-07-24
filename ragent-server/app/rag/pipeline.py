"""RAG Pipeline — orchestrates query → rewrite → search → fusion → context build."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.rag.search.vector import MilvusSearchChannel
from app.rag.postprocess.fusion import rrf_fusion, deduplicate
from app.rag.search.base import SearchResult
from app.rag.rewrite import rewrite_query
from app.rag.intent import recognize_intent


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


class RAGPipeline:
    """Full RAG pipeline: rewrite → intent → search → fusion → return context."""

    def __init__(self):
        self.milvus = MilvusSearchChannel()

    async def run(self, ctx: RAGContext) -> RAGResult:
        t0 = time.time()

        # 1. Rewrite
        rewritten = await rewrite_query(ctx.question, ctx.history)

        # 2. Intent
        intent = await recognize_intent(rewritten or ctx.question)

        # 3. Resolve collection
        collection_name = (
            ctx.collection_name
            or (intent.get("collection_name") if intent else None)
            or "rag_default_store"
        )

        # 4. Vector search
        search_question = rewritten or ctx.question
        vector_results = await self.milvus.search(
            query=search_question,
            collection_name=collection_name,
            top_k=10,
        )

        # 5. Dedup
        merged = deduplicate(vector_results)

        # 6. Build chunks + sources
        chunks = [
            {
                "content": r.content,
                "chunk_id": r.chunk_id,
                "score": r.score,
            }
            for r in merged
        ]

        sources = [
            {
                "docName": r.doc_name or "unknown",
                "chunkIndex": r.chunk_index,
                "content": r.content[:200],
                "score": r.score,
            }
            for r in merged
        ]

        duration = int((time.time() - t0) * 1000)

        return RAGResult(
            question=ctx.question,
            rewrite=rewritten,
            intent=intent,
            context_chunks=chunks,
            sources=sources,
            duration_ms=duration,
        )
