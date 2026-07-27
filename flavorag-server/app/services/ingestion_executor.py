"""Shared ingestion execution — used by the API sync path and the job worker.

Runs the KB-bound DAG pipeline when available, otherwise the legacy
``IngestionPipeline``. Callers own the transaction.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunker import ChunkConfig
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.pipeline_engine import IngestionEngine
from app.models import KnowledgeBase, KnowledgeDocument


async def execute_ingestion(
    db: AsyncSession,
    *,
    kb: KnowledgeBase,
    doc: KnowledgeDocument,
    file_path: str,
    source_type: str,
    user_id: str,
    tenant_id: str,
    chunk_config: ChunkConfig,
    pipeline_id: str | None = None,
) -> int:
    """Run ingestion for one document and update its status; returns chunk count."""
    effective_pipeline_id = pipeline_id or kb.pipeline_id
    if effective_pipeline_id:
        engine = IngestionEngine()
        result = await engine.execute_pipeline(
            pipeline_id=effective_pipeline_id,
            source_type=source_type,
            source_location=file_path,
            source_file_name=doc.doc_name,
            kb_id=kb.id,
            doc_id=doc.id,
            user_id=user_id,
            tenant_id=tenant_id,
            db=db,
        )
        if result.status == "error":
            doc.status = "failed"
            raise RuntimeError(result.error_message or "Pipeline execution failed")
        doc.status = "success"
        doc.chunk_count = result.chunk_count
        await db.flush()
        return result.chunk_count

    # Legacy: use old IngestionPipeline
    pipeline = IngestionPipeline()
    chunk_count = await pipeline.run(
        doc_id=doc.id,
        kb_id=kb.id,
        file_path=file_path,
        collection_name=kb.collection_name,
        db=db,
        chunk_config=chunk_config,
    )
    doc.status = "success"
    doc.chunk_count = chunk_count
    await db.flush()
    return chunk_count
