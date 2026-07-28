"""URL document refresh scheduler — periodic ETag-based change detection.

Checks URL-sourced documents for changes using HTTP ETag / Last-Modified
headers. When a change is detected, re-downloads and re-ingests the document.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config.settings import settings
from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import KnowledgeDocument, KnowledgeBase, gen_id
from app.ingestion.chunker import ChunkConfig
from app.ingestion.url_fetcher import SafeURLFetcher

_log = get_logger("flavorag.scheduler.url_refresh")


class URLRefreshScheduler:
    """Periodic URL document refresh with ETag-based change detection."""

    def __init__(self, poll_interval_sec: int = 3600):
        self.poll_interval = poll_interval_sec
        self._task: asyncio.Task | None = None
        self._running = False
        from app.services.schedule.lock_manager import ScheduleLockManager

        self._leader_lock = ScheduleLockManager()
        self._leader_key = "url-refresh-scheduler"

    async def start(self):
        if self._running:
            return
        if not await self._leader_lock.acquire(self._leader_key):
            _log.info("url_refresh_scheduler_standby")
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
        await self._leader_lock.release(self._leader_key)
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

                content_hash = self._compute_content_hash(etag, last_modified, url)
                chunk_cfg = doc.chunk_config
                current_hash = chunk_cfg.get("_content_hash", "") if isinstance(chunk_cfg, dict) else ""

                if content_hash == current_hash and current_hash:
                    _log.info("document_unchanged", doc_id=doc.id)
                    return False

                _log.info("document_changed", doc_id=doc.id)

                fetched = await fetcher.fetch(url)
                content = fetched.content
                ext = self._guess_extension(fetched.final_url, fetched.content_type)

                tmp_path = os.path.join(tempfile.gettempdir(), f"rag_url_{gen_id()}.{ext}")
                await asyncio.to_thread(Path(tmp_path).write_bytes, content)

                try:
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
                    # Re-ingest into a pending generation; the active
                    # generation remains queryable if any channel fails.
                    chunk_size = chunk_cfg.get("chunkSize", 512) if isinstance(chunk_cfg, dict) else 512
                    overlap_size = chunk_cfg.get("overlapSize", 128) if isinstance(chunk_cfg, dict) else 128
                    chunk_config = ChunkConfig(
                        strategy=doc.chunk_strategy or "FIXED_WINDOW",
                        chunk_size=chunk_size,
                        overlap=overlap_size,
                    )
                    from app.ingestion.source_storage import persist_source
                    from app.services.ingestion_executor import execute_ingestion

                    durable_source = await persist_source(
                        tmp_path,
                        kb_id=kb.id,
                        doc_id=doc.id,
                        filename=doc.doc_name,
                    )
                    doc.file_url = durable_source
                    generation = f"g_{gen_id()}"
                    doc.pending_generation = generation
                    chunk_count = await execute_ingestion(
                        session,
                        kb=kb,
                        doc=doc,
                        file_path=durable_source,
                        source_type="url",
                        user_id=doc.updated_by or doc.created_by,
                        tenant_id=doc.tenant_id or "default",
                        chunk_config=chunk_config,
                        generation=generation,
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
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "text/html": "html",
            "application/json": "json",
            "text/csv": "csv",
        }
        for ct_prefix, ext in ct_map.items():
            if content_type.startswith(ct_prefix):
                return ext

        return "txt"
