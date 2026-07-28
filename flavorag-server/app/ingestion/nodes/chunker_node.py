"""Chunker node — splits parsed text into chunks."""

from __future__ import annotations

from app.config.logging_config import get_logger
from app.ingestion.nodes.base import IngestionContext, NodeResult
from app.ingestion.chunker import DocumentChunker, ChunkConfig, ChunkStrategy
from app.ingestion.cross_reference import inject_cross_references

_log = get_logger("flavorag.ingestion.chunker")


class ChunkerNode:
    """Split parsed text into chunks using configured strategy.

    Settings:
        strategy (str): "FIXED_WINDOW" | "SEMANTIC" | "BLOCK_AWARE"
        chunk_size (int): Target chunk size in characters (default 512)
        overlap_size (int): Overlap size in characters (default 128)
    """

    NODE_TYPE = "chunker"

    def __init__(self):
        self._chunker = DocumentChunker()

    async def __call__(self, ctx: IngestionContext) -> NodeResult:
        import time
        t0 = time.time()

        try:
            if not ctx.parsed_text:
                raise ValueError("No parsed text to chunk")

            if ctx.parsed_document is not None:
                from app.ingestion.pdf.chunker import StructuredPdfChunker
                from app.ingestion.pdf.models import StructuredPdfDocument
                from app.ingestion.structured import GenericStructuredChunker
                from app.config.settings import settings as app_settings

                chunker_class = (
                    StructuredPdfChunker
                    if isinstance(ctx.parsed_document, StructuredPdfDocument)
                    else GenericStructuredChunker
                )
                chunker = chunker_class(
                    target_chars=int(ctx.settings.get("chunk_size", 800)),
                    table_max_rows=int(
                        ctx.settings.get("table_max_rows", app_settings.pdf_table_max_rows)
                    ),
                )
                chunks = chunker.chunk(ctx.parsed_document)
                chunks = inject_cross_references(chunks)
                ctx.chunks = chunks
                duration_ms = int((time.time() - t0) * 1000)
                _log.info(
                    "chunker_done",
                    doc_id=ctx.doc_id,
                    strategy="MULTIMODAL_BLOCK",
                    chunk_count=len(chunks),
                    took_ms=duration_ms,
                )
                return NodeResult(
                    node_type=self.NODE_TYPE,
                    status="success",
                    duration_ms=duration_ms,
                    output={
                        "chunk_count": len(chunks),
                        "strategy": "MULTIMODAL_BLOCK",
                    },
                )

            config = self._build_config(ctx.settings)
            if config.resolve_strategy() == ChunkStrategy.SEMANTIC:
                from app.llm.embedding import get_embedding_client

                chunks = await self._chunker.chunk_semantic(
                    ctx.parsed_text,
                    config,
                    embedder=get_embedding_client(
                        model=ctx.settings.get("embedding_model") or None
                    ),
                )
            else:
                chunks = self._chunker.chunk(ctx.parsed_text, config)
            chunks = inject_cross_references(chunks)
            ctx.chunks = chunks

            duration_ms = int((time.time() - t0) * 1000)
            _log.info(
                "chunker_done",
                doc_id=ctx.doc_id,
                strategy=config.strategy.value,
                chunk_count=len(chunks),
                took_ms=duration_ms,
            )
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="success",
                duration_ms=duration_ms,
                output={"chunk_count": len(chunks)},
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            _log.error("chunker_failed", doc_id=ctx.doc_id, error=str(exc))
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="error",
                error_message=str(exc),
                duration_ms=duration_ms,
            )

    def _build_config(self, settings: dict) -> ChunkConfig:
        config = ChunkConfig()
        strategy_str = settings.get("strategy", "FIXED_WINDOW")
        try:
            config.strategy = ChunkStrategy.from_value(strategy_str)
        except ValueError:
            config.strategy = ChunkStrategy.FIXED_WINDOW
        if "chunk_size" in settings:
            config.chunk_size = int(settings["chunk_size"])
        if "overlap_size" in settings:
            config.overlap = int(settings["overlap_size"])
        return config
