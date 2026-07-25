"""Enhancer node — rewrites chunks for better retrieval quality.

Uses LLM to rewrite chunks into self-contained, retrieval-friendly format
(e.g., adding context from surrounding chunks, normalizing formatting).
"""

from __future__ import annotations

from app.config.logging_config import get_logger
from app.ingestion.nodes.base import IngestionContext, NodeResult

_log = get_logger("flavorag.ingestion.enhancer")


class EnhancerNode:
    """Enhance chunk content for retrieval quality via LLM rewriting.

    Settings:
        use_llm (bool): Use LLM for enhancement (default False).
        add_context (bool): Prepend document context/summary to each chunk.
    """

    NODE_TYPE = "enhancer"

    async def __call__(self, ctx: IngestionContext) -> NodeResult:
        import time
        t0 = time.time()

        try:
            if not ctx.chunks:
                return NodeResult(
                    node_type=self.NODE_TYPE,
                    status="skipped",
                    message="No chunks to enhance",
                )

            use_llm = ctx.settings.get("use_llm", False)
            add_context = ctx.settings.get("add_context", False)

            if use_llm:
                await self._enhance_with_llm(ctx)
            elif add_context:
                self._add_document_context(ctx)

            duration_ms = int((time.time() - t0) * 1000)
            _log.info("enhancer_done", doc_id=ctx.doc_id, chunk_count=len(ctx.chunks), took_ms=duration_ms)
            return NodeResult(
                node_type=self.NODE_TYPE, status="success", duration_ms=duration_ms,
                output={"chunk_count": len(ctx.chunks)},
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            _log.error("enhancer_failed", doc_id=ctx.doc_id, error=str(exc))
            return NodeResult(
                node_type=self.NODE_TYPE, status="error", error_message=str(exc), duration_ms=duration_ms,
            )

    async def _enhance_with_llm(self, ctx: IngestionContext):
        from app.llm.client import LLMClient
        from app.config.settings import settings

        client = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.bailian_api_key or settings.siliconflow_api_key or "",
            model=settings.llm_model or "qwen-plus-latest",
        )
        for chunk in ctx.chunks:
            content = chunk.get("content", "")
            if not content.strip() or len(content) < 50:
                continue
            prompt = (
                "Rewrite the following text chunk to be self-contained and clearer "
                "for semantic search. Preserve all facts and details. "
                "Output only the rewritten text.\n\nText:\n" + content[:2000]
            )
            try:
                rewritten = await client.chat_completion(messages=[{"role": "user", "content": prompt}])
                if rewritten.strip():
                    chunk["content"] = rewritten
            except Exception as exc:
                _log.warning("enhancer_llm_failed", chunk_index=chunk.get("chunk_index"), error=str(exc))

    def _add_document_context(self, ctx: IngestionContext):
        """Prepend document-level context (first 200 chars as summary) to each chunk."""
        if not ctx.parsed_text:
            return
        doc_context = ctx.parsed_text[:200].strip()
        for chunk in ctx.chunks:
            chunk["content"] = f"[Document context: {doc_context}]\n\n{chunk.get('content', '')}"
