"""Document ingestion pipeline — Parse → Chunk → Embed → Index → PG save."""
from __future__ import annotations

import hashlib
import asyncio
import os
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.session import async_session_factory
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

    def __init__(self, *, embedder=None):
        self.parser = DocumentParser()
        self.chunker = DocumentChunker()
        self.embedder = embedder or get_embedding_client()
        self.milvus = MilvusSearchChannel()

    async def run(
        self,
        doc_id: str,
        kb_id: str,
        file_path: str,
        collection_name: str,
        db: AsyncSession,
        chunk_config: ChunkConfig | None = None,
        generation: str = "v1",
    ) -> int:
        """Run the full ingestion pipeline.

        Returns the number of chunks created.
        """
        from app.models import KnowledgeAsset, KnowledgeChunk, KnowledgeDocument

        t0 = time.time()
        _ingest_log.info("ingestion_start", doc_id=doc_id, kb_id=kb_id, file_path=file_path)

        # 1. Parse
        t_parse = time.time()
        parsed = await self.parser.parse_document(
            file_path,
            document_id=doc_id,
            source_file=os.path.basename(file_path),
        )
        structured_document = parsed if hasattr(parsed, "blocks") else None
        parsed_text = parsed.to_markdown() if structured_document else parsed
        parse_ms = int((time.time() - t_parse) * 1000)
        _ingest_log.info("parse", doc_id=doc_id, text_len=len(parsed_text), took_ms=parse_ms)

        # 2. Chunk
        t_chunk = time.time()
        if structured_document is not None:
            from app.ingestion.pdf.chunker import StructuredPdfChunker
            from app.ingestion.pdf.models import StructuredPdfDocument
            from app.ingestion.structured import GenericStructuredChunker

            chunker_class = (
                StructuredPdfChunker
                if isinstance(structured_document, StructuredPdfDocument)
                else GenericStructuredChunker
            )
            chunks = chunker_class(
                target_chars=chunk_config.chunk_size if chunk_config else 800,
                table_max_rows=settings.pdf_table_max_rows,
            ).chunk(structured_document)
        else:
            if chunk_config is None:
                chunk_config = ChunkConfig()
            if chunk_config.resolve_strategy().name == "SEMANTIC":
                chunks = await self.chunker.chunk_semantic(
                    parsed_text,
                    chunk_config,
                    embedder=self.embedder,
                )
            else:
                chunks = self.chunker.chunk(parsed_text, chunk_config)
        chunk_ms = int((time.time() - t_chunk) * 1000)
        _ingest_log.info(
            "chunk",
            doc_id=doc_id,
            strategy="MULTIMODAL_BLOCK" if structured_document else chunk_config.strategy,
            chunk_count=len(chunks),
            chunk_size=chunk_config.chunk_size if chunk_config else 800,
            took_ms=chunk_ms,
        )

        # 2.5 Cross-element reference injection
        from app.ingestion.cross_reference import inject_cross_references
        chunks = inject_cross_references(chunks)

        if not chunks:
            _ingest_log.warning("ingestion_no_chunks", doc_id=doc_id)
            return 0

        # 3. Embed
        t_embed = time.time()
        texts = [c.get("embedding_content") or c["content"] for c in chunks]
        vectors = await self.embedder.embed_documents(texts)
        embed_ms = int((time.time() - t_embed) * 1000)
        _ingest_log.info("embed", doc_id=doc_id, vector_count=len(vectors), dim=len(vectors[0]) if vectors else 0, took_ms=embed_ms)

        # 4. Save chunk metadata to PostgreSQL
        scope_result = await db.execute(
            select(
                KnowledgeDocument.tenant_id,
                KnowledgeDocument.department_id,
            ).where(KnowledgeDocument.id == doc_id)
        )
        scope = scope_result.first()
        tenant_id = scope.tenant_id if scope else "default"
        department_id = scope.department_id if scope else None

        if structured_document and structured_document.assets:
            from app.ingestion.pdf.asset_storage import (
                materialize_asset_urls,
                persist_pdf_assets,
            )
            try:
                asset_urls = await persist_pdf_assets(
                    structured_document.assets,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    created_by="system",
                    session=db,
                    generation=generation,
                )
                materialize_asset_urls(chunks, asset_urls)
            except Exception:
                if settings.pdf_asset_storage_required:
                    raise
                _ingest_log.warning("pdf_asset_persistence_skipped", doc_id=doc_id)

        chunk_records: list[KnowledgeChunk] = []
        for c in chunks:
            content = c["content"]
            chunk_records.append(KnowledgeChunk(
                kb_id=kb_id,
                doc_id=doc_id,
                tenant_id=tenant_id,
                department_id=department_id,
                chunk_index=c["chunk_index"],
                content=content,
                embedding_content=c.get("embedding_content"),
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                char_count=c["char_count"],
                block_type=c.get("block_type"),
                page_start=c.get("page_start"),
                page_end=c.get("page_end"),
                bbox_json=c.get("bbox_json"),
                metadata_json={
                    **(c.get("metadata_json") or {}),
                    "asset_ids": c.get("asset_ids", []),
                },
                generation=generation,
                index_status="PENDING",
                created_by="system",
            ))

        for cr in chunk_records:
            db.add(cr)
        await db.flush()

        # 5. Insert vectors into Milvus
        chunk_ids = [cr.id for cr in chunk_records]
        doc_ids = [doc_id] * len(chunk_records)
        contents = [cr.content for cr in chunk_records]

        await asyncio.to_thread(
            self.milvus.insert,
            collection_name,
            chunk_ids,
            doc_ids,
            contents,
            vectors,
        )

        # 6. Update document status
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.chunk_count = len(chunks)
            doc.status = "success"

        # 7. Optional: ES keyword index
        es_ok = True
        if settings.es_enabled:
            es_ok = await self._index_to_es(kb_id, chunk_records)
            if not es_ok and settings.es_required:
                raise RuntimeError("required Elasticsearch indexing failed")

        # 8. Optional: LightRAG graph sync
        graph_ok = True
        if settings.graph_enabled:
            graph_ok = await self._sync_to_lightrag(kb_id, chunk_records)
            if not graph_ok and settings.graph_required:
                raise RuntimeError("required graph indexing failed")

        # 8.5 Activate the new generation only after required indexes succeed.
        previous = list(
            (
                await db.execute(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.doc_id == doc_id,
                        KnowledgeChunk.generation != generation,
                        KnowledgeChunk.index_status == "ACTIVE",
                        KnowledgeChunk.deleted == 0,
                    )
                )
            ).scalars().all()
        )
        for old in previous:
            old.index_status = "SUPERSEDED"
            old.deleted = 1
        for current in chunk_records:
            current.index_status = "ACTIVE"
        previous_assets = list(
            (
                await db.execute(
                    select(KnowledgeAsset).where(
                        KnowledgeAsset.doc_id == doc_id,
                        KnowledgeAsset.generation != generation,
                        KnowledgeAsset.index_status == "ACTIVE",
                        KnowledgeAsset.deleted == 0,
                    )
                )
            ).scalars().all()
        )
        for old_asset in previous_assets:
            old_asset.index_status = "SUPERSEDED"
            old_asset.deleted = 1
        current_assets = list(
            (
                await db.execute(
                    select(KnowledgeAsset).where(
                        KnowledgeAsset.doc_id == doc_id,
                        KnowledgeAsset.generation == generation,
                        KnowledgeAsset.index_status == "PENDING",
                        KnowledgeAsset.deleted == 0,
                    )
                )
            ).scalars().all()
        )
        for current_asset in current_assets:
            current_asset.index_status = "ACTIVE"
        if doc:
            doc.active_generation = generation
            doc.pending_generation = None

        if not es_ok or not graph_ok:
            from app.models import IndexRepairJob

            for channel, ok in (("elasticsearch", es_ok), ("graph", graph_ok)):
                if not ok:
                    db.add(
                        IndexRepairJob(
                            kb_id=kb_id,
                            doc_id=doc_id,
                            generation=generation,
                            channel=channel,
                            operation="UPSERT",
                            status="QUEUED",
                        )
                    )
        await db.flush()

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
                chunk_strategy=(
                    getattr(chunk_config.strategy, "value", chunk_config.strategy)
                    if chunk_config
                    else ""
                ),
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

    async def _index_to_es(self, kb_id: str, chunks: list) -> bool:
        """Index chunk content to Elasticsearch for BM25 keyword search.

        Best-effort: failures are logged (never silently swallowed) and do
        not fail the ingestion — chunks can be re-indexed later.
        """
        from app.rag.search.keyword import ESKeywordSearchChannel

        try:
            payload = [
                {
                    "id": c.id,
                    "tenant_id": c.tenant_id,
                    "department_id": c.department_id,
                    "doc_id": c.doc_id,
                    "content": c.content,
                    "embedding_content": c.embedding_content,
                    "chunk_index": c.chunk_index,
                    "block_type": c.block_type,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                }
                for c in chunks
            ]
            await ESKeywordSearchChannel().insert(payload, kb_id)
            return True
        except Exception as exc:
            _ingest_log.warning(
                "es_index_failed",
                kb_id=kb_id,
                chunk_count=len(chunks),
                error=str(exc),
            )
            return False

    async def _sync_to_lightrag(self, kb_id: str, chunks: list) -> bool:
        """Sync the reliable Neo4j graph, then enqueue LightRAG enrichment."""
        try:
            from app.models import KnowledgeBase
            from app.rag.graph.lightrag_client import LightRAGClient
            from app.rag.graph.neo4j_store import Neo4jGraphStore

            collection_name = ""
            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(KnowledgeBase.collection_name).where(
                            KnowledgeBase.id == kb_id,
                            KnowledgeBase.deleted == 0,
                        )
                    )
                ).first()
                if row:
                    collection_name = row[0]
            payload = [
                {
                    "doc_id": chunk.doc_id,
                    "chunk_id": chunk.id,
                    "tenant_id": chunk.tenant_id,
                    "content": chunk.content,
                }
                for chunk in chunks
            ]
            native_result = await Neo4jGraphStore().upsert_chunks(
                kb_id=kb_id,
                collection_name=collection_name,
                chunks=payload,
            )
            _ingest_log.info(
                "native_graph_sync_complete",
                kb_id=kb_id,
                **native_result,
            )
            try:
                await LightRAGClient().insert_documents_batch(
                    kb_id,
                    payload,
                    collection_name=collection_name,
                )
            except Exception as exc:
                _ingest_log.warning(
                    "lightrag_enrichment_enqueue_failed",
                    kb_id=kb_id,
                    error=str(exc),
                )
            return True
        except Exception as exc:
            _ingest_log.warning(
                "graph_sync_failed",
                kb_id=kb_id,
                error=str(exc),
            )
            return False


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
