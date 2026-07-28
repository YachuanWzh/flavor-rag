"""Indexer node — embeds chunks and indexes into Milvus + PostgreSQL."""

from __future__ import annotations

import hashlib

from app.config.logging_config import get_logger
from app.ingestion.nodes.base import IngestionContext, NodeResult

_log = get_logger("flavorag.ingestion.indexer")


class IndexerNode:
    """Embed chunks and store vectors in Milvus + metadata in PostgreSQL.

    Settings:
        collection_name (str): Target Milvus collection (overrides kb-derived name).
        enable_es (bool): Also index to Elasticsearch (default True).
        enable_graph (bool): Also sync to LightRAG/Neo4j (default False).
    """

    NODE_TYPE = "indexer"

    async def __call__(self, ctx: IngestionContext) -> NodeResult:
        import asyncio
        import time
        from contextlib import asynccontextmanager
        from app.database.session import async_session_factory

        t0 = time.time()

        try:
            @asynccontextmanager
            async def session_scope():
                if ctx.db is not None:
                    yield ctx.db
                else:
                    async with async_session_factory() as fallback_session:
                        yield fallback_session

            if not ctx.chunks:
                return NodeResult(
                    node_type=self.NODE_TYPE,
                    status="skipped",
                    message="No chunks to index",
                )

            # 1. Embed
            t_embed = time.time()
            from app.llm.embedding import get_embedding_client
            embedder = get_embedding_client(
                model=ctx.settings.get("embedding_model") or None
            )
            texts = [c.get("embedding_content") or c["content"] for c in ctx.chunks]
            vectors = await embedder.embed_documents(texts)
            ctx.vectors = vectors
            embed_ms = int((time.time() - t_embed) * 1000)
            _log.info("indexer_embed", doc_id=ctx.doc_id, vector_count=len(vectors), took_ms=embed_ms)

            # 2. Persist chunk metadata to PG
            t_pg = time.time()
            async with session_scope() as session:
                from app.models import KnowledgeChunk, KnowledgeDocument, gen_id
                from sqlalchemy import select

                scope_result = await session.execute(
                    select(
                        KnowledgeDocument.tenant_id,
                        KnowledgeDocument.department_id,
                    ).where(KnowledgeDocument.id == ctx.doc_id)
                )
                scope = scope_result.first()
                tenant_id = scope.tenant_id if scope else "default"
                department_id = scope.department_id if scope else None

                if ctx.assets:
                    from app.config.settings import settings
                    from app.ingestion.pdf.asset_storage import (
                        materialize_asset_urls,
                        persist_pdf_assets,
                    )
                    try:
                        asset_urls = await persist_pdf_assets(
                            ctx.assets,
                            kb_id=ctx.kb_id,
                            doc_id=ctx.doc_id,
                            created_by="pipeline",
                            session=session,
                            generation=ctx.generation,
                        )
                        materialize_asset_urls(ctx.chunks, asset_urls)
                    except Exception:
                        if settings.pdf_asset_storage_required:
                            raise
                        _log.warning(
                            "pdf_asset_persistence_skipped",
                            doc_id=ctx.doc_id,
                        )

                chunk_records = []
                for c in ctx.chunks:
                    content = c["content"]
                    record = KnowledgeChunk(
                        id=gen_id(),
                        kb_id=ctx.kb_id,
                        doc_id=ctx.doc_id,
                        tenant_id=tenant_id,
                        department_id=department_id,
                        chunk_index=c["chunk_index"],
                        content=content,
                        embedding_content=c.get("embedding_content"),
                        content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                        char_count=c.get("char_count", len(content)),
                        block_type=c.get("block_type"),
                        page_start=c.get("page_start"),
                        page_end=c.get("page_end"),
                        bbox_json=c.get("bbox_json"),
                        metadata_json={
                            **(c.get("metadata_json") or {}),
                            "asset_ids": c.get("asset_ids", []),
                        },
                        generation=ctx.generation,
                        index_status="PENDING",
                        created_by="pipeline",
                    )
                    session.add(record)
                    chunk_records.append(record)

                await session.flush()

                # Refresh IDs after commit
                chunk_ids = [r.id for r in chunk_records]
                ctx.chunk_records = chunk_records
            pg_ms = int((time.time() - t_pg) * 1000)
            _log.info("indexer_pg", doc_id=ctx.doc_id, chunk_count=len(chunk_records), took_ms=pg_ms)

            # 3. Insert vectors into Milvus
            t_milvus = time.time()
            from app.rag.search.vector import MilvusSearchChannel
            milvus = MilvusSearchChannel()
            collection_name = ctx.settings.get("collection_name") or "default_store"

            # Derive collection from KB if possible
            if not ctx.settings.get("collection_name"):
                async with session_scope() as session:
                    from sqlalchemy import select
                    from app.models import KnowledgeBase
                    result = await session.execute(
                        select(
                            KnowledgeBase.collection_name,
                            KnowledgeBase.active_collection_name,
                        ).where(
                            KnowledgeBase.id == ctx.kb_id,
                            KnowledgeBase.deleted == 0,
                        )
                    )
                    kb_row = result.first()
                    if kb_row:
                        collection_name = kb_row[1] or kb_row[0]

            doc_ids = [ctx.doc_id] * len(chunk_records)
            contents = [r.content for r in chunk_records]
            await asyncio.to_thread(
                milvus.insert,
                collection_name,
                chunk_ids,
                doc_ids,
                contents,
                vectors,
            )
            milvus_ms = int((time.time() - t_milvus) * 1000)
            _log.info("indexer_milvus", doc_id=ctx.doc_id, collection=collection_name, took_ms=milvus_ms)

            # 4. ES keyword index (optional)
            es_ok = True
            if ctx.settings.get("enable_es", True):
                try:
                    from app.config.settings import settings
                    if settings.es_enabled:
                        await self._index_to_es(ctx.kb_id, chunk_records)
                except Exception as exc:
                    es_ok = False
                    _log.warning("indexer_es_failed", doc_id=ctx.doc_id, error=str(exc))

            graph_ok = True
            if ctx.settings.get("enable_graph", False):
                try:
                    from app.config.settings import settings
                    if settings.graph_enabled:
                        from app.rag.graph.lightrag_client import LightRAGClient
                        from app.rag.graph.neo4j_store import Neo4jGraphStore

                        graph_payload = [
                            {
                                "doc_id": record.doc_id,
                                "chunk_id": record.id,
                                "tenant_id": record.tenant_id,
                                "content": record.content,
                            }
                            for record in chunk_records
                        ]
                        await Neo4jGraphStore().upsert_chunks(
                            kb_id=ctx.kb_id,
                            collection_name=collection_name,
                            chunks=graph_payload,
                        )
                        try:
                            await LightRAGClient().insert_documents_batch(
                                ctx.kb_id,
                                graph_payload,
                                collection_name=collection_name,
                            )
                        except Exception as exc:
                            _log.warning(
                                "indexer_lightrag_enrichment_failed",
                                doc_id=ctx.doc_id,
                                error=str(exc),
                            )
                except Exception as exc:
                    graph_ok = False
                    _log.warning(
                        "indexer_graph_failed",
                        doc_id=ctx.doc_id,
                        error=str(exc),
                    )

            from app.config.settings import settings
            if not es_ok and settings.es_required:
                raise RuntimeError("required Elasticsearch indexing failed")
            if not graph_ok and settings.graph_required:
                raise RuntimeError("required graph indexing failed")

            # A single database transaction activates the new generation and
            # retires the old one only after required external indexes succeed.
            async with session_scope() as session:
                from app.models import IndexRepairJob, KnowledgeAsset

                old_chunks = list(
                    (
                        await session.execute(
                            select(KnowledgeChunk).where(
                                KnowledgeChunk.doc_id == ctx.doc_id,
                                KnowledgeChunk.generation != ctx.generation,
                                KnowledgeChunk.index_status == "ACTIVE",
                                KnowledgeChunk.deleted == 0,
                            )
                        )
                    ).scalars().all()
                )
                for old in old_chunks:
                    old.index_status = "SUPERSEDED"
                    old.deleted = 1
                for current in chunk_records:
                    current.index_status = "ACTIVE"
                old_assets = list(
                    (
                        await session.execute(
                            select(KnowledgeAsset).where(
                                KnowledgeAsset.doc_id == ctx.doc_id,
                                KnowledgeAsset.generation != ctx.generation,
                                KnowledgeAsset.index_status == "ACTIVE",
                                KnowledgeAsset.deleted == 0,
                            )
                        )
                    ).scalars().all()
                )
                for old_asset in old_assets:
                    old_asset.index_status = "SUPERSEDED"
                    old_asset.deleted = 1
                current_assets = list(
                    (
                        await session.execute(
                            select(KnowledgeAsset).where(
                                KnowledgeAsset.doc_id == ctx.doc_id,
                                KnowledgeAsset.generation == ctx.generation,
                                KnowledgeAsset.index_status == "PENDING",
                                KnowledgeAsset.deleted == 0,
                            )
                        )
                    ).scalars().all()
                )
                for current_asset in current_assets:
                    current_asset.index_status = "ACTIVE"
                document = (
                    await session.execute(
                        select(KnowledgeDocument).where(
                            KnowledgeDocument.id == ctx.doc_id
                        )
                    )
                ).scalar_one_or_none()
                if document is not None:
                    document.active_generation = ctx.generation
                    document.pending_generation = None
                for channel, ok in (("elasticsearch", es_ok), ("graph", graph_ok)):
                    if not ok:
                        session.add(
                            IndexRepairJob(
                                kb_id=ctx.kb_id,
                                doc_id=ctx.doc_id,
                                generation=ctx.generation,
                                channel=channel,
                            )
                        )
                await session.flush()

            duration_ms = int((time.time() - t0) * 1000)
            return NodeResult(
                node_type=self.NODE_TYPE,
                status="success",
                duration_ms=duration_ms,
                output={
                    "chunk_count": len(chunk_records),
                    "embed_ms": embed_ms,
                    "pg_ms": pg_ms,
                    "milvus_ms": milvus_ms,
                },
            )
        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            _log.error("indexer_failed", doc_id=ctx.doc_id, error=str(exc))
            return NodeResult(
                node_type=self.NODE_TYPE, status="error", error_message=str(exc), duration_ms=duration_ms,
            )

    async def _index_to_es(self, kb_id: str, chunks: list):
        """Index chunks to ES via the shared keyword channel (explicit
        mapping + singleton client). Raises on failure; the call site
        degrades with a warning log."""
        from app.rag.search.keyword import ESKeywordSearchChannel

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
