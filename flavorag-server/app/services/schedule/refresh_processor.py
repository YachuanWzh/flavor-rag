"""Document refresh processor — detects changes and re-ingests documents."""

from __future__ import annotations

import hashlib
import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import (
    KnowledgeDocument,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeAsset,
    KnowledgeDocumentSchedule,
    KnowledgeDocumentScheduleExec,
    gen_id,
)
from app.ingestion.url_fetcher import SafeURLFetcher

_log = get_logger("flavorag.schedule.refresh")


class RefreshProcessor:
    """Handles change detection and re-ingestion for scheduled documents."""

    async def process(self, sched: KnowledgeDocumentSchedule) -> bool:
        """Check a scheduled document for changes and refresh if needed.

        Returns True if a refresh was triggered, False if unchanged.
        """
        exec_id = gen_id()
        start_time = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            async with async_session_factory() as session:
                # Load document
                result = await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.id == sched.doc_id,
                        KnowledgeDocument.deleted == 0,
                    )
                )
                doc = result.scalar_one_or_none()
                if not doc:
                    await self._record_exec(
                        session, exec_id, sched.id, sched.doc_id, sched.kb_id,
                        "error", "Document not found", start_time,
                    )
                    return False

                _log.info("refresh_check", doc_id=doc.id, doc_name=doc.doc_name)

                # Check if document source has changed
                changed = await self._has_changed(session, doc, sched)
                if not changed:
                    sched.last_run_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    sched.last_status = "unchanged"
                    return False

                # Determine source: file or URL
                source_path = await self._get_source_path(doc)
                if not source_path:
                    await self._record_exec(
                        session, exec_id, sched.id, sched.doc_id, sched.kb_id,
                        "error", "Cannot resolve source path", start_time,
                    )
                    return False

                # Get collection name
                kb_result = await session.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.id == doc.kb_id,
                        KnowledgeBase.deleted == 0,
                    )
                )
                kb = kb_result.scalar_one_or_none()
                if kb is None:
                    raise RuntimeError("knowledge base no longer exists")
                # Re-ingest into a pending generation. The ingestion executor
                # activates it only after every required index succeeds.
                from app.ingestion.chunker import ChunkConfig, ChunkStrategy
                from app.ingestion.source_storage import persist_source
                from app.services.ingestion_executor import execute_ingestion

                chunk_config = ChunkConfig()
                if doc.chunk_strategy:
                    try:
                        chunk_config.strategy = ChunkStrategy.from_value(doc.chunk_strategy)
                    except ValueError:
                        pass
                if doc.chunk_config and isinstance(doc.chunk_config, dict):
                    cs = doc.chunk_config.get("chunkSize")
                    if cs:
                        chunk_config.chunk_size = int(cs)
                    ov = doc.chunk_config.get("overlapSize")
                    if ov:
                        chunk_config.overlap = int(ov)

                durable_source = await persist_source(
                    source_path,
                    kb_id=kb.id,
                    doc_id=doc.id,
                    filename=doc.doc_name,
                )
                doc.file_url = durable_source
                generation = f"g_{gen_id()}"
                doc.pending_generation = generation
                await execute_ingestion(
                    session,
                    kb=kb,
                    doc=doc,
                    file_path=durable_source,
                    source_type=doc.source_type or "file",
                    user_id=doc.updated_by or doc.created_by,
                    tenant_id=doc.tenant_id or "default",
                    chunk_config=chunk_config,
                    generation=generation,
                )

                # Update schedule state
                sched.last_success_time = datetime.now(timezone.utc).replace(tzinfo=None)
                sched.last_status = "success"
                sched.last_error = None

                await self._record_exec(
                    session, exec_id, sched.id, sched.doc_id, sched.kb_id,
                    "success", None, start_time,
                    file_name=doc.doc_name,
                    file_size=doc.file_size,
                )

                _log.info("refresh_complete", doc_id=doc.id, doc_name=doc.doc_name)
                return True

        except Exception as exc:
            _log.error("refresh_failed", doc_id=sched.doc_id, error=str(exc))
            try:
                async with async_session_factory() as session:
                    await self._record_exec(
                        session, exec_id, sched.id, sched.doc_id, sched.kb_id,
                        "error", str(exc)[:500], start_time,
                    )
            except Exception:
                pass
            return False

    async def _has_changed(
        self, session: AsyncSession, doc: KnowledgeDocument, sched: KnowledgeDocumentSchedule
    ) -> bool:
        """Detect if a document source has changed since last check."""
        if doc.source_type == "url" and doc.source_location:
            return await self._url_has_changed(doc, sched)
        # For file-type documents, always re-process (simplified)
        return True

    async def _url_has_changed(
        self, doc: KnowledgeDocument, sched: KnowledgeDocumentSchedule
    ) -> bool:
        """Check URL document for changes via ETag/Last-Modified."""
        url = doc.source_location
        if not url:
            return False
        try:
            fetcher = SafeURLFetcher(
                max_bytes=settings.url_ingestion_max_bytes,
                timeout_sec=settings.url_ingestion_timeout_sec,
                max_redirects=settings.url_ingestion_max_redirects,
                allow_private_networks=settings.url_allow_private_networks,
            )
            await fetcher.validate_url(url)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                head_resp = await client.head(url)
                head_resp.raise_for_status()
                etag = head_resp.headers.get("etag", "")
                last_modified = head_resp.headers.get("last-modified", "")
                content_hash = hashlib.sha256(
                    f"{etag}|{last_modified}|{url}".encode()
                ).hexdigest()[:32]

                changed = content_hash != (sched.last_content_hash or "")
                if changed:
                    sched.last_etag = etag
                    sched.last_modified = last_modified
                    sched.last_content_hash = content_hash
                return changed
        except Exception as exc:
            _log.warning("url_change_check_failed", doc_id=doc.id, error=str(exc))
            return False

    async def _get_source_path(self, doc: KnowledgeDocument) -> str | None:
        """Resolve document source to a local file path."""
        # File documents: use file_url directly
        if doc.source_type == "file" or not doc.source_type:
            from app.ingestion.source_storage import is_object_source

            if doc.file_url and (
                is_object_source(doc.file_url)
                or os.path.exists(doc.file_url)
            ):
                return doc.file_url

        # URL documents: download to temp
        if doc.source_type == "url" and doc.source_location:
            try:
                fetched = await SafeURLFetcher(
                    max_bytes=settings.url_ingestion_max_bytes,
                    timeout_sec=settings.url_ingestion_timeout_sec,
                    max_redirects=settings.url_ingestion_max_redirects,
                    allow_private_networks=settings.url_allow_private_networks,
                ).fetch(doc.source_location)
                ext = doc.file_type or "tmp"
                tmp_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..", "uploads",
                    f"{doc.id}.{ext}"
                )
                os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                await asyncio.to_thread(
                    Path(tmp_path).write_bytes, fetched.content
                )
                return tmp_path
            except Exception as exc:
                _log.error("url_download_failed", doc_id=doc.id, error=str(exc))
                return None

        return None

    async def _cleanup_old_chunks(
        self, session: AsyncSession, doc_id: str, kb: KnowledgeBase
    ) -> None:
        """Soft-delete old chunks and remove from Milvus."""
        result = await session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.doc_id == doc_id,
                KnowledgeChunk.deleted == 0,
            )
        )
        old_chunks = result.scalars().all()
        assets_result = await session.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.doc_id == doc_id,
                KnowledgeAsset.deleted == 0,
            )
        )
        old_assets = list(assets_result.scalars().all())
        for chunk in old_chunks:
            chunk.deleted = 1
        for asset in old_assets:
            asset.deleted = 1
        if old_chunks or old_assets:
            from app.services.index_sync import IndexSyncService

            await IndexSyncService().delete_document(
                session,
                kb=kb,
                doc_id=doc_id,
                chunks=list(old_chunks),
                assets=old_assets,
            )

    async def _record_exec(
        self,
        session: AsyncSession,
        exec_id: str,
        schedule_id: str,
        doc_id: str,
        kb_id: str,
        status: str,
        message: str | None,
        start_time: datetime,
        file_name: str | None = None,
        file_size: int | None = None,
    ) -> None:
        """Record a schedule execution result."""
        end_time = datetime.now(timezone.utc).replace(tzinfo=None)
        exec_record = KnowledgeDocumentScheduleExec(
            id=exec_id,
            schedule_id=schedule_id,
            doc_id=doc_id,
            kb_id=kb_id,
            status=status,
            message=message,
            start_time=start_time,
            end_time=end_time,
            file_name=file_name,
            file_size=file_size,
        )
        session.add(exec_record)
        await session.flush()
