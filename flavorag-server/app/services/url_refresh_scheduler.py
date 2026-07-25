"""URL document refresh scheduler — periodic ETag-based change detection.

Checks URL-sourced documents for changes using HTTP ETag / Last-Modified
headers. When a change is detected, re-downloads and re-ingests the document.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config.settings import settings
from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import KnowledgeDocument, KnowledgeBase, KnowledgeChunk, gen_id
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.chunker import ChunkConfig
from app.rag.search.vector import MilvusSearchChannel

_log = get_logger("flavorag.scheduler.url_refresh")


class URLRefreshScheduler:
    """Periodic URL document refresh with ETag-based change detection."""

    def __init__(self, poll_interval_sec: int = 3600):
        self.poll_interval = poll_interval_sec
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        _log.info("url_refresh_scheduler_started", interval_sec=self.poll_interval)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log.info("url_refresh_scheduler_stopped")

    async def _poll_loop(self):
        while self._running:
            try:
                await self._check_all_documents()
            except Exception as exc:
                _log.error("scheduler_poll_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval)

    async def _check_all_documents(self):
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.deleted == 0,
                        KnowledgeDocument.source_type == "url",
                        KnowledgeDocument.schedule_enabled == 1,
                    )
                )
                docs = result.scalars().all()

                if not docs:
                    return

                _log.info("scheduler_scan", document_count=len(docs))
                for doc in docs:
                    try:
                        await self._check_and_refresh(session, doc)
                    except Exception as exc:
                        _log.error("document_check_failed", doc_id=doc.id, error=str(exc))
        except Exception as exc:
            _log.error("scheduler_scan_failed", error=str(exc))

    async def _check_and_refresh(self, session, doc: KnowledgeDocument) -> bool:
        url = doc.source_location
        if not url:
            return False

        _log.info("checking_document", doc_id=doc.id, doc_name=doc.doc_name, url=url[:120])

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                head_resp = await client.head(url)
                head_resp.raise_for_status()

                etag = head_resp.headers.get("etag", "")
                last_modified = head_resp.headers.get("last-modified", "")

                content_hash = self._compute_content_hash(etag, last_modified, url)
                chunk_cfg = doc.chunk_config
                current_hash = chunk_cfg.get("_content_hash", "") if isinstance(chunk_cfg, dict) else ""

                if content_hash == current_hash and current_hash:
                    _log.info("document_unchanged", doc_id=doc.id)
                    return False

                _log.info("document_changed", doc_id=doc.id)

                get_resp = await client.get(url)
                get_resp.raise_for_status()
                content = get_resp.content
                ext = self._guess_extension(url, get_resp.headers.get("content-type", ""))

                tmp_path = os.path.join(tempfile.gettempdir(), f"rag_url_{gen_id()}.{ext}")
                with open(tmp_path, "wb") as f:
                    f.write(content)

                try:
                    # Get collection name
                    kb_result = await session.execute(
                        select(KnowledgeBase.collection_name).where(
                            KnowledgeBase.id == doc.kb_id,
                            KnowledgeBase.deleted == 0,
                        )
                    )
                    kb_row = kb_result.first()
                    collection_name = kb_row[0] if kb_row else "default_store"

                    # Soft-delete old chunks
                    await session.execute(
                        select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id)
                    )
                    chunks_result = await session.execute(
                        select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id)
                    )
                    for chunk in chunks_result.scalars().all():
                        chunk.deleted = 1

                    await session.flush()

                    # Re-ingest
                    chunk_size = chunk_cfg.get("chunkSize", 512) if isinstance(chunk_cfg, dict) else 512
                    overlap_size = chunk_cfg.get("overlapSize", 128) if isinstance(chunk_cfg, dict) else 128
                    chunk_config = ChunkConfig(
                        strategy=doc.chunk_strategy or "FIXED_WINDOW",
                        chunk_size=chunk_size,
                        overlap=overlap_size,
                    )
                    pipeline = IngestionPipeline()
                    chunk_count = await pipeline.run(
                        doc_id=doc.id,
                        kb_id=doc.kb_id,
                        file_path=tmp_path,
                        collection_name=collection_name,
                        db=session,
                        chunk_config=chunk_config,
                    )

                    # Store new content hash
                    if doc.chunk_config is None:
                        doc.chunk_config = {}
                    if isinstance(doc.chunk_config, dict):
                        doc.chunk_config["_content_hash"] = content_hash
                    doc.status = "success"
                    doc.chunk_count = chunk_count

                    await session.commit()
                    _log.info("document_refreshed", doc_id=doc.id, chunk_count=chunk_count)
                    return True

                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

        except httpx.HTTPStatusError as exc:
            _log.error("http_error", doc_id=doc.id, url=url[:120], status=exc.response.status_code)
            return False
        except Exception as exc:
            _log.error("refresh_failed", doc_id=doc.id, url=url[:120], error=str(exc))
            return False

    @staticmethod
    def _compute_content_hash(etag: str, last_modified: str, url: str) -> str:
        raw = f"{etag}|{last_modified}|{url}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _guess_extension(url: str, content_type: str) -> str:
        from urllib.parse import urlparse
        path = urlparse(url).path
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in ("txt", "md", "pdf", "docx", "html", "htm", "json", "csv"):
                return ext

        ct_map = {
            "text/plain": "txt",
            "text/markdown": "md",
            "application/pdf": "pdf",
            "text/html": "html",
            "application/json": "json",
            "text/csv": "csv",
        }
        for ct_prefix, ext in ct_map.items():
            if content_type.startswith(ct_prefix):
                return ext

        return "txt"
