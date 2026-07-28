"""Batch import service — multi-file upload with progress tracking and dedup.

Uses independent sessions per file so a long-running batch doesn't hold
the request-level session open, and each file's work is isolated.
"""

from __future__ import annotations

import asyncio
import os
import socket
import traceback
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.ingestion.dedup import DuplicateDetector, compute_content_hash
from app.models import (
    BatchImportJob,
    BatchImportFileRecord,
    KnowledgeBase,
    KnowledgeDocument,
    User,
    gen_id,
)

_log = get_logger("flavorag.ingestion.batch")


@dataclass
class BatchFileSpec:
    filename: str
    file_path: str
    file_size: int = 0


@dataclass
class BatchImportResult:
    job_id: str = ""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped_duplicates: int = 0
    status: str = "pending"
    per_file: list[dict] = field(default_factory=list)


class BatchImportHandler:
    """Orchestrates batch document imports with progress tracking."""

    def __init__(self, upload_dir: str):
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)

    async def run_batch(
        self,
        kb_id: str,
        file_specs: list[BatchFileSpec],
        user: User,
        *,
        chunk_strategy: str = "FIXED_WINDOW",
        chunk_size: int = 512,
        overlap: int = 128,
        job_id: str | None = None,
    ) -> BatchImportResult:
        """Execute a batch import. Each file gets its own short-lived session."""
        per_file: list[dict] = []
        success = 0
        failed = 0
        skipped = 0

        if job_id is None:
            job_id = await self.create_job(kb_id, file_specs, user)
        async with async_session_factory() as job_db:
            job = (
                await job_db.execute(
                    select(BatchImportJob).where(BatchImportJob.id == job_id)
                )
            ).scalar_one()
            job.status = "running"
            await job_db.commit()

        for spec in file_specs:
            filename = spec.filename
            file_size = spec.file_size
            ext = os.path.splitext(filename)[1].lower().lstrip(".")
            if not ext:
                ext = "txt"

            try:
                async with async_session_factory() as db:
                    file_result = await self._process_one(
                        db=db,
                        kb_id=kb_id,
                        job_id=job_id,
                        spec=spec,
                        ext=ext,
                        user=user,
                        chunk_strategy=chunk_strategy,
                        chunk_size=chunk_size,
                        overlap=overlap,
                    )
                    await db.commit()

                if file_result["status"] == "duplicate":
                    skipped += 1
                elif file_result["status"] == "success":
                    success += 1
                per_file.append(file_result)

            except Exception as exc:
                failed += 1
                traceback.print_exc()
                error_msg = f"{type(exc).__name__}: {exc}"
                per_file.append({
                    "fileName": filename,
                    "status": "error",
                    "error": error_msg,
                })
                _log.error(
                    "batch_file_failed",
                    job_id=job_id,
                    file_name=filename,
                    error=error_msg,
                )

                # Record error in a fresh session
                try:
                    async with async_session_factory() as error_db:
                        await self._record_file_error(
                            error_db,
                            job_id,
                            filename,
                            file_size,
                            ext,
                            spec.file_path,
                            error_msg,
                        )
                        await error_db.commit()
                except Exception:
                    pass

            # Update job progress
            try:
                async with async_session_factory() as prog_db:
                    await self._update_job(
                        prog_db, job_id,
                        completed=success, failed=failed, skipped=skipped,
                    )
                    await prog_db.commit()
            except Exception:
                pass

        # Final status
        final_status = (
            "success" if failed == 0
            else ("partial" if success > 0 else "error")
        )
        try:
            async with async_session_factory() as final_db:
                await self._update_job(
                    final_db, job_id, status=final_status,
                )
                await final_db.commit()
        except Exception:
            pass

        return BatchImportResult(
            job_id=job_id,
            total=len(file_specs),
            success=success,
            failed=failed,
            skipped_duplicates=skipped,
            status=final_status,
            per_file=per_file,
        )

    async def create_job(
        self,
        kb_id: str,
        file_specs: list[BatchFileSpec],
        user: User,
        *,
        chunk_strategy: str = "FIXED_WINDOW",
        chunk_size: int = 512,
        overlap: int = 128,
    ) -> str:
        """Persist progress state before launching background processing."""
        async with async_session_factory() as job_db:
            job = BatchImportJob(
                id=gen_id(),
                tenant_id=user.tenant_id or "default",
                kb_id=kb_id,
                total_files=len(file_specs),
                completed_files=0,
                failed_files=0,
                skipped_duplicates=0,
                status="queued",
                file_results=[],
                config_json={
                    "chunk_strategy": chunk_strategy,
                    "chunk_size": chunk_size,
                    "overlap": overlap,
                },
                created_by=user.id,
            )
            job_db.add(job)
            for spec in file_specs:
                job_db.add(
                    BatchImportFileRecord(
                        id=gen_id(),
                        job_id=job.id,
                        file_name=spec.filename,
                        file_size=spec.file_size,
                        file_type=(
                            os.path.splitext(spec.filename)[1]
                            .lower()
                            .lstrip(".")
                            or "txt"
                        ),
                        status="queued",
                        source_location=spec.file_path,
                    )
                )
            await job_db.commit()
            return job.id

    async def _process_one(
        self,
        db: AsyncSession,
        kb_id: str,
        job_id: str,
        spec: BatchFileSpec,
        ext: str,
        user: User,
        *,
        chunk_strategy: str,
        chunk_size: int,
        overlap: int,
    ) -> dict:
        from app.ingestion.source_storage import materialize_source

        async with materialize_source(spec.file_path) as local_path:
            local_spec = BatchFileSpec(
                filename=spec.filename,
                file_path=local_path,
                file_size=spec.file_size,
            )
            return await self._process_one_local(
                db=db,
                kb_id=kb_id,
                job_id=job_id,
                spec=local_spec,
                ext=ext,
                user=user,
                chunk_strategy=chunk_strategy,
                chunk_size=chunk_size,
                overlap=overlap,
                durable_location=spec.file_path,
            )

    async def _process_one_local(
        self,
        db: AsyncSession,
        kb_id: str,
        job_id: str,
        spec: BatchFileSpec,
        ext: str,
        user: User,
        *,
        chunk_strategy: str,
        chunk_size: int,
        overlap: int,
        durable_location: str,
    ) -> dict:
        """Process a single file in its own session."""
        filename = spec.filename
        file_path = spec.file_path
        file_size = spec.file_size

        # 1. Load KB
        kb_result = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.deleted == 0,
            )
        )
        kb = kb_result.scalar_one_or_none()
        if not kb:
            return {"fileName": filename, "status": "error", "error": "知识库不存在"}

        # 2. Dedup check
        dedup = DuplicateDetector()
        dup_result = await dedup.check_file(
            file_path, kb_id, db, tenant_id=user.tenant_id or "default"
        )
        if dup_result.is_duplicate:
            await self._record_file(
                db,
                job_id,
                filename,
                file_size,
                ext,
                durable_location,
                status="duplicate", doc_id=dup_result.existing_doc_id,
            )
            _log.info("batch_skipped_duplicate", file_name=filename)
            return {
                "fileName": filename,
                "status": "duplicate",
                "existingDocId": dup_result.existing_doc_id,
            }

        # 3. Create document record
        doc_id = gen_id()
        content_hash = compute_content_hash(file_path)

        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=kb_id,
            tenant_id=kb.tenant_id,
            department_id=kb.department_id,
            doc_name=filename,
            file_url=durable_location,
            file_type=ext,
            file_size=file_size,
            content_hash=content_hash,
            chunk_strategy=chunk_strategy,
            chunk_config={"chunkSize": chunk_size, "overlapSize": overlap},
            status="running",
            created_by=user.id,
        )
        db.add(doc)
        await db.flush()

        # 4. Run ingestion
        from app.ingestion.chunker import ChunkConfig, ChunkStrategy
        from app.api.knowledge import run_ingestion_for_doc

        strategy = ChunkStrategy.from_value(chunk_strategy).name
        cfg = ChunkConfig(
            strategy=strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        chunk_count = await run_ingestion_for_doc(
            kb=kb, doc=doc, file_path=durable_location,
            source_type="file", user=user, db=db, chunk_config=cfg,
        )

        await self._record_file(
            db, job_id, filename, file_size, ext,
            durable_location,
            status="success", doc_id=doc_id, chunk_count=chunk_count,
        )
        return {
            "fileName": filename,
            "status": "success",
            "docId": doc_id,
            "chunkCount": chunk_count,
        }

    @staticmethod
    async def _record_file(
        db: AsyncSession,
        job_id: str,
        file_name: str,
        file_size: int,
        file_type: str,
        source_location: str,
        *,
        status: str,
        doc_id: str = "",
        chunk_count: int = 0,
    ) -> None:
        record = (
            await db.execute(
                select(BatchImportFileRecord).where(
                    BatchImportFileRecord.job_id == job_id,
                    BatchImportFileRecord.source_location == source_location,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            record = BatchImportFileRecord(
                id=gen_id(),
                job_id=job_id,
                file_name=file_name,
                source_location=source_location,
            )
            db.add(record)
        record.file_size = file_size
        record.file_type = file_type
        record.status = status
        record.doc_id = doc_id or None
        record.chunk_count = chunk_count
        record.error_message = None

    @staticmethod
    async def _record_file_error(
        db: AsyncSession,
        job_id: str,
        file_name: str,
        file_size: int,
        file_type: str,
        source_location: str,
        error: str,
    ) -> None:
        record = (
            await db.execute(
                select(BatchImportFileRecord).where(
                    BatchImportFileRecord.job_id == job_id,
                    BatchImportFileRecord.source_location == source_location,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            record = BatchImportFileRecord(
                id=gen_id(),
                job_id=job_id,
                file_name=file_name,
                source_location=source_location,
            )
            db.add(record)
        record.file_size = file_size or 0
        record.file_type = file_type or "txt"
        record.status = "error"
        record.error_message = error or None

    @staticmethod
    async def _update_job(
        db: AsyncSession,
        job_id: str,
        *,
        completed: int | None = None,
        failed: int | None = None,
        skipped: int | None = None,
        status: str | None = None,
    ) -> None:
        result = await db.execute(
            select(BatchImportJob).where(BatchImportJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if not job:
            return
        if completed is not None:
            job.completed_files = completed
        if failed is not None:
            job.failed_files = failed
        if skipped is not None:
            job.skipped_duplicates = skipped
        if status is not None:
            job.status = status
            if status in {"success", "partial", "error"}:
                job.claimed_by = None
                job.claimed_at = None
                job.next_retry_time = None


class BatchImportWorker:
    """Claim persisted batch jobs so request-process loss is recoverable."""

    def __init__(self, poll_interval_sec: int = 5):
        self.poll_interval_sec = poll_interval_sec
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="batch-import-worker"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                processed = await self.run_once()
            except Exception as exc:
                processed = 0
                _log.warning("batch_worker_poll_failed", error=str(exc))
            if not processed:
                await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self) -> int:
        from sqlalchemy import or_

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale = now - timedelta(minutes=15)
        async with async_session_factory() as session:
            job = (
                await session.execute(
                    select(BatchImportJob)
                    .where(
                        BatchImportJob.deleted == 0,
                        or_(
                            BatchImportJob.status == "queued",
                            (
                                (BatchImportJob.status == "retry")
                                & (
                                    BatchImportJob.next_retry_time.is_(None)
                                    | (
                                        BatchImportJob.next_retry_time
                                        <= now
                                    )
                                )
                            ),
                            (
                                (BatchImportJob.status == "running")
                                & (BatchImportJob.claimed_at <= stale)
                            ),
                        ),
                    )
                    .order_by(BatchImportJob.create_time)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return 0
            job.status = "running"
            job.claimed_by = self.worker_id
            job.claimed_at = now
            job.attempts = (job.attempts or 0) + 1
            job_id = job.id
            await session.commit()

        try:
            async with async_session_factory() as session:
                job = await session.get(BatchImportJob, job_id)
                user = await session.get(User, job.created_by)
                rows = list(
                    (
                        await session.execute(
                            select(BatchImportFileRecord).where(
                                BatchImportFileRecord.job_id == job_id
                            )
                        )
                    ).scalars().all()
                )
                if user is None:
                    raise RuntimeError("batch owner is missing")
                config = dict(job.config_json or {})
                specs = [
                    BatchFileSpec(
                        filename=row.file_name,
                        file_path=row.source_location or "",
                        file_size=row.file_size or 0,
                    )
                    for row in rows
                    if row.source_location
                ]
                kb_id = job.kb_id

            await BatchImportHandler(
                os.path.abspath("uploads")
            ).run_batch(
                kb_id=kb_id,
                file_specs=specs,
                user=user,
                chunk_strategy=str(
                    config.get("chunk_strategy", "FIXED_WINDOW")
                ),
                chunk_size=int(config.get("chunk_size", 512)),
                overlap=int(config.get("overlap", 128)),
                job_id=job_id,
            )
        except Exception as exc:
            async with async_session_factory() as session:
                job = await session.get(BatchImportJob, job_id)
                if job is not None:
                    job.status = "retry" if job.attempts < 3 else "error"
                    job.error_message = (
                        f"{type(exc).__name__}: {exc}"[:2000]
                    )
                    if job.status == "retry":
                        job.next_retry_time = now + timedelta(
                            seconds=min(300, 2 ** job.attempts)
                        )
                    await session.commit()
            _log.exception(
                "batch_worker_failed", job_id=job_id, error=str(exc)
            )
        return 1
