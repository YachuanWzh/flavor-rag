"""Asynchronous ingestion via an outbox job table (t_ingestion_job).

API endpoints enqueue an ``IngestionJob`` inside the request transaction
(outbox pattern), and ``IngestionJobWorker`` claims jobs with
``FOR UPDATE SKIP LOCKED`` (ignored on SQLite) and executes them through
the shared :func:`app.services.ingestion_executor.execute_ingestion`.

Job state machine: QUEUED -> RUNNING -> SUCCESS | RETRY (backoff) | DEAD.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.ingestion.chunker import ChunkConfig
from app.models import (
    IngestionJob,
    KnowledgeBase,
    KnowledgeDocument,
    gen_id,
)
from app.observability.metrics import (
    INGESTION_JOB_LATENCY,
    INGESTION_JOBS,
    INGESTION_QUEUE_DEPTH,
    INDEX_LAST_SUCCESS_TIMESTAMP,
)

_log = get_logger("flavorag.ingestion_jobs")

_QUEUE_DEPTH_STATUSES = ("QUEUED", "RETRY", "RUNNING", "DEAD")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def enqueue_ingestion_job(
    session,
    *,
    kb: KnowledgeBase,
    doc: KnowledgeDocument,
    file_path: str,
    source_type: str,
    chunk_config: ChunkConfig,
    user_id: str,
    tenant_id: str,
    pipeline_id: str | None = None,
    operation: str = "INGEST",
) -> IngestionJob:
    """Persist an outbox job in the caller's transaction; the doc becomes ``queued``."""
    config_fingerprint = (
        f"{chunk_config.strategy}:{chunk_config.chunk_size}:{chunk_config.overlap}"
    )
    raw_key = "|".join(
        [
            tenant_id or "default",
            kb.id,
            doc.id,
            operation,
            doc.content_hash or file_path,
            pipeline_id or "",
            config_fingerprint,
        ]
    )
    idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    existing = (
        await session.execute(
            select(IngestionJob).where(
                IngestionJob.idempotency_key == idempotency_key,
                IngestionJob.deleted == 0,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        doc.status = (
            "success" if existing.status == "SUCCESS" else "queued"
        )
        return existing

    from app.ingestion.source_storage import persist_source

    durable_path = await persist_source(
        file_path,
        kb_id=kb.id,
        doc_id=doc.id,
        filename=doc.doc_name,
    )
    doc.file_url = durable_path

    generation = f"g_{gen_id()}"
    job = IngestionJob(
        id=gen_id(),
        idempotency_key=idempotency_key,
        tenant_id=tenant_id or "default",
        kb_id=kb.id,
        doc_id=doc.id,
        pipeline_id=pipeline_id,
        source_type=source_type,
        file_path=durable_path,
        chunk_strategy=chunk_config.strategy,
        chunk_config_json={
            "chunkSize": chunk_config.chunk_size,
            "overlapSize": chunk_config.overlap,
        },
        operation=operation,
        generation=generation,
        status="QUEUED",
        attempts=0,
        max_attempts=settings.ingestion_job_max_attempts,
        created_by=user_id,
    )
    session.add(job)
    doc.pending_generation = generation
    doc.status = "queued"
    await session.flush()
    return job


class IngestionJobWorker:
    """Polls t_ingestion_job, claims due jobs and runs ingestion with retries."""

    def __init__(
        self,
        *,
        concurrency: int | None = None,
        poll_interval_sec: int | None = None,
    ):
        self.concurrency = concurrency or settings.ingestion_worker_concurrency
        self.poll_interval_sec = (
            poll_interval_sec or settings.ingestion_worker_poll_interval_sec
        )
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        from app.database.session import async_session_factory

        while self._running:
            try:
                processed = await self.run_once(async_session_factory)
            except Exception as exc:
                processed = 0
                _log.warning("ingestion_worker_poll_failed", error=str(exc))
            # Drain the queue quickly when there is backlog.
            if processed == 0:
                await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self, session_factory) -> int:
        """Claim and execute one batch of jobs; returns the number processed."""
        job_ids = await self._claim_jobs(session_factory)
        if job_ids:
            await asyncio.gather(
                *(self._process_job(session_factory, job_id) for job_id in job_ids)
            )
        await self._export_queue_depth(session_factory)
        return len(job_ids)

    async def _claim_jobs(self, session_factory) -> list[str]:
        now = _utcnow()
        stale_before = now - timedelta(seconds=settings.ingestion_job_claim_timeout_sec)
        async with session_factory() as session:
            stmt = (
                select(IngestionJob)
                .where(
                    IngestionJob.deleted == 0,
                    or_(
                        IngestionJob.status == "QUEUED",
                        (IngestionJob.status == "RETRY")
                        & (
                            IngestionJob.next_retry_time.is_(None)
                            | (IngestionJob.next_retry_time <= now)
                        ),
                        # Reclaim jobs orphaned by a crashed worker.
                        (IngestionJob.status == "RUNNING")
                        & (IngestionJob.claimed_at <= stale_before),
                    ),
                )
                .order_by(IngestionJob.create_time)
                .limit(self.concurrency)
                .with_for_update(skip_locked=True)
            )
            jobs = list((await session.execute(stmt)).scalars().all())
            for job in jobs:
                job.status = "RUNNING"
                job.claimed_by = self.worker_id
                job.claimed_at = now
                job.started_at = now
                job.attempts = (job.attempts or 0) + 1
            await session.commit()
            return [job.id for job in jobs]

    async def _process_job(self, session_factory, job_id: str) -> None:
        from app.services.ingestion_executor import execute_ingestion

        started = time.monotonic()
        async with session_factory() as session:
            job = (
                await session.execute(
                    select(IngestionJob).where(IngestionJob.id == job_id)
                )
            ).scalar_one_or_none()
            if job is None:
                return
            try:
                kb = (
                    await session.execute(
                        select(KnowledgeBase).where(
                            KnowledgeBase.id == job.kb_id,
                            KnowledgeBase.deleted == 0,
                        )
                    )
                ).scalar_one_or_none()
                doc = (
                    await session.execute(
                        select(KnowledgeDocument).where(
                            KnowledgeDocument.id == job.doc_id,
                            KnowledgeDocument.deleted == 0,
                        )
                    )
                ).scalar_one_or_none()
                if kb is None or doc is None:
                    raise _PermanentJobError("knowledge base or document missing")
                from app.ingestion.source_storage import is_object_source

                if (
                    not is_object_source(job.file_path)
                    and not os.path.exists(job.file_path)
                ):
                    raise _PermanentJobError(f"source file missing: {job.file_path}")

                doc.status = "running"
                config = job.chunk_config_json or {}
                chunk_count = await execute_ingestion(
                    session,
                    kb=kb,
                    doc=doc,
                    file_path=job.file_path,
                    source_type=job.source_type or "file",
                    user_id=job.created_by,
                    tenant_id=job.tenant_id or "default",
                    chunk_config=ChunkConfig(
                        strategy=job.chunk_strategy or "FIXED_WINDOW",
                        chunk_size=int(config.get("chunkSize") or 512),
                        overlap=int(config.get("overlapSize") or 128),
                    ),
                    pipeline_id=job.pipeline_id,
                    generation=job.generation,
                )
            except Exception as exc:
                await session.rollback()
                await self._mark_failure(session, job_id, exc)
                await session.commit()
                return

            elapsed = time.monotonic() - started
            job.status = "SUCCESS"
            job.completed_at = _utcnow()
            job.duration_ms = int(elapsed * 1000)
            job.chunk_count = chunk_count
            job.error_message = None
            await session.commit()
            INGESTION_JOBS.labels(result="success").inc()
            INGESTION_JOB_LATENCY.observe(elapsed)
            INDEX_LAST_SUCCESS_TIMESTAMP.set(time.time())
            _log.info(
                "ingestion_job_success",
                job_id=job_id,
                doc_id=job.doc_id,
                chunk_count=chunk_count,
                duration_ms=job.duration_ms,
            )

    async def _mark_failure(self, session, job_id: str, exc: Exception) -> None:
        """Move the job to RETRY (with backoff) or DEAD after a rollback."""
        job = (
            await session.execute(
                select(IngestionJob).where(IngestionJob.id == job_id)
            )
        ).scalar_one_or_none()
        if job is None:
            return
        job.error_message = str(exc)[:2000]
        job.completed_at = _utcnow()
        permanent = isinstance(exc, _PermanentJobError)
        exhausted = (job.attempts or 0) >= (job.max_attempts or 1)
        if permanent or exhausted:
            job.status = "DEAD"
            job.next_retry_time = None
            result = "dead"
        else:
            job.status = "RETRY"
            delay_minutes = min(30, 2 ** min(job.attempts or 1, 5))
            job.next_retry_time = _utcnow() + timedelta(minutes=delay_minutes)
            result = "retry"
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == job.doc_id)
            )
        ).scalar_one_or_none()
        if doc is not None:
            doc.status = "failed" if job.status == "DEAD" else "queued"
        INGESTION_JOBS.labels(result=result).inc()
        _log.warning(
            "ingestion_job_failed",
            job_id=job_id,
            doc_id=job.doc_id,
            attempts=job.attempts,
            status=job.status,
            error=str(exc),
        )

    async def _export_queue_depth(self, session_factory) -> None:
        try:
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        select(IngestionJob.status, func.count(IngestionJob.id))
                        .where(IngestionJob.deleted == 0)
                        .group_by(IngestionJob.status)
                    )
                ).all()
            counts = {status: count for status, count in rows}
            for status in _QUEUE_DEPTH_STATUSES:
                INGESTION_QUEUE_DEPTH.labels(status=status).set(
                    counts.get(status, 0)
                )
        except Exception as exc:
            _log.debug("ingestion_queue_depth_failed", error=str(exc))


class _PermanentJobError(RuntimeError):
    """Non-retryable job failure (missing KB/document/source file)."""
