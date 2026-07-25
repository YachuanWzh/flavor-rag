"""Document ingestion pipeline — Parse → Chunk → Embed → Index → PG save."""
from __future__ import annotations

import hashlib
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.ingestion.parser import DocumentParser
from app.ingestion.chunker import DocumentChunker, ChunkConfig
from app.llm.embedding import get_embedding_client
from app.rag.search.vector import MilvusSearchChannel
from app.config.logging_config import get_logger

_ingest_log = get_logger("flavorag.ingestion.pipeline")


class IngestionPipeline:
    """Orchestrates the full document ingestion workflow.

    Flow: Parse → Chunk → Embed → Index(Milvus) → Save(PG)
    """

    def __init__(self):
        self.parser = DocumentParser()
        self.chunker = DocumentChunker()
        self.embedder = get_embedding_client()
        self.milvus = MilvusSearchChannel()

    async def run(
        self,
        doc_id: str,
        kb_id: str,
        file_path: str,
        collection_name: str,
        db: AsyncSession,
        chunk_config: ChunkConfig | None = None,
    ) -> int:
        """Run the full ingestion pipeline.

        Returns the number of chunks created.
        """
        from app.models import KnowledgeChunk, KnowledgeDocument

        t0 = time.time()
        _ingest_log.info("ingestion_start", doc_id=doc_id, kb_id=kb_id, file_path=file_path)

        # 1. Parse
        t_parse = time.time()
        parsed_text = await self.parser.parse(file_path)
        parse_ms = int((time.time() - t_parse) * 1000)
        _ingest_log.info("parse", doc_id=doc_id, text_len=len(parsed_text), took_ms=parse_ms)

        # 2. Chunk
        t_chunk = time.time()
        if chunk_config is None:
            chunk_config = ChunkConfig()
        chunks = self.chunker.chunk(parsed_text, chunk_config)
        chunk_ms = int((time.time() - t_chunk) * 1000)
        _ingest_log.info("chunk", doc_id=doc_id, strategy=chunk_config.strategy, chunk_count=len(chunks), chunk_size=chunk_config.chunk_size, took_ms=chunk_ms)

        if not chunks:
            _ingest_log.warning("ingestion_no_chunks", doc_id=doc_id)
            return 0

        # 3. Embed
        t_embed = time.time()
        texts = [c["content"] for c in chunks]
        vectors = await self.embedder.embed_documents(texts)
        embed_ms = int((time.time() - t_embed) * 1000)
        _ingest_log.info("embed", doc_id=doc_id, vector_count=len(vectors), dim=len(vectors[0]) if vectors else 0, took_ms=embed_ms)

        # 4. Save chunk metadata to PostgreSQL
        chunk_records: list[KnowledgeChunk] = []
        for c in chunks:
            content = c["content"]
            chunk_records.append(KnowledgeChunk(
                kb_id=kb_id,
                doc_id=doc_id,
                chunk_index=c["chunk_index"],
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                char_count=c["char_count"],
                created_by="system",
            ))

        for cr in chunk_records:
            db.add(cr)
        await db.flush()

        # 5. Insert vectors into Milvus
        chunk_ids = [cr.id for cr in chunk_records]
        doc_ids = [doc_id] * len(chunk_records)
        contents = [cr.content for cr in chunk_records]

        self.milvus.insert(
            collection_name=collection_name,
            chunk_ids=chunk_ids,
            doc_ids=doc_ids,
            contents=contents,
            vectors=vectors,
        )

        # 6. Update document status
        from sqlalchemy import select
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.chunk_count = len(chunks)
            doc.status = "success"

        # 7. Optional: ES keyword index
        if settings.es_enabled:
            await self._index_to_es(kb_id, chunk_records)

        # 8. Optional: LightRAG graph sync
        if settings.graph_enabled:
            await self._sync_to_lightrag(kb_id, chunk_records)

        total_ms = int((time.time() - t0) * 1000)
        _ingest_log.info(
            "ingestion_complete",
            doc_id=doc_id,
            kb_id=kb_id,
            chunk_count=len(chunks),
            parse_ms=parse_ms,
            chunk_ms=chunk_ms,
            embed_ms=embed_ms,
            total_ms=total_ms,
        )

        # 9. Log chunk processing metrics
        try:
            await _log_chunk_processing(
                doc_id=doc_id,
                status="success",
                chunk_strategy=chunk_config.strategy.value if chunk_config else "",
                extract_duration=parse_ms,
                chunk_duration=chunk_ms,
                embed_duration=embed_ms,
                total_duration=total_ms,
                chunk_count=len(chunks),
                db=db,
            )
        except Exception:
            pass  # Non-critical: skip if log table doesn't exist

        return len(chunks)

    async def _index_to_es(self, kb_id: str, chunks: list):
        """Index chunk content to Elasticsearch for BM25 keyword search."""
        try:
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(settings.es_uris)
            for c in chunks:
                await es.index(
                    index="rag_keyword_store",
                    id=c.id,
                    body={
                        "kb_id": kb_id,
                        "content": c.content,
                        "chunk_index": c.chunk_index,
                    },
                )
        except Exception:
            pass  # ES is optional; silently skip on failure

    async def _sync_to_lightrag(self, kb_id: str, chunks: list):
        """Sync chunks to LightRAG knowledge graph."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                for c in chunks:
                    await client.post(
                        f"{settings.lightrag_base_url}/documents",
                        json={"kb_id": kb_id, "content": c.content},
                    )
        except Exception:
            pass  # LightRAG is optional; silently skip


async def _log_chunk_processing(
    doc_id: str,
    status: str,
    *,
    chunk_strategy: str = "",
    pipeline_id: str = "",
    extract_duration: int = 0,
    chunk_duration: int = 0,
    embed_duration: int = 0,
    persist_duration: int = 0,
    total_duration: int = 0,
    chunk_count: int = 0,
    error_message: str = "",
    db=None,
) -> None:
    """Log chunk processing performance metrics to t_knowledge_document_chunk_log."""
    from datetime import datetime, timezone
    from app.models import KnowledgeDocumentChunkLog, gen_id

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry = KnowledgeDocumentChunkLog(
        id=gen_id(),
        doc_id=doc_id,
        status=status,
        process_mode="chunk",
        chunk_strategy=chunk_strategy,
        pipeline_id=pipeline_id,
        extract_duration=extract_duration,
        chunk_duration=chunk_duration,
        embed_duration=embed_duration,
        persist_duration=persist_duration,
        total_duration=total_duration,
        chunk_count=chunk_count,
        error_message=error_message,
        start_time=now,
        end_time=now,
    )
    if db is not None:
        db.add(entry)
        await db.flush()
    else:
        from app.database.session import async_session_factory
        async with async_session_factory() as session:
            session.add(entry)
            await session.commit()
