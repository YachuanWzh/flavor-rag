"""Batch import service — multi-file upload with progress tracking and dedup.

Uses independent sessions per file so a long-running batch doesn't hold
the request-level session open, and each file's work is isolated.
"""

from __future__ import annotations

import os
import traceback
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
    KnowledgeChunk,
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
    ) -> BatchImportResult:
        """Execute a batch import. Each file gets its own short-lived session."""
        per_file: list[dict] = []
        success = 0
        failed = 0
        skipped = 0

        # Create the job record in a dedicated session
        async with async_session_factory() as job_db:
            job = BatchImportJob(
                id=gen_id(),
                tenant_id=user.tenant_id or "default",
                kb_id=kb_id,
                total_files=len(file_specs),
                completed_files=0,
                failed_files=0,
                skipped_duplicates=0,
                status="running",
                file_results=[],
                created_by=user.id,
            )
            job_db.add(job)
            await job_db.commit()
            job_id = job.id

        for spec in file_specs:
            filename = spec.filename
            file_path = spec.file_path
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
                        self._record_file_error(
                            error_db, job_id, filename, file_size, ext, error_msg,
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
            self._record_file(
                db, job_id,
                filename, file_size, ext,
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
            file_url=file_path,
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
            kb=kb, doc=doc, file_path=file_path,
            source_type="file", user=user, db=db, chunk_config=cfg,
        )

        self._record_file(
            db, job_id, filename, file_size, ext,
            status="success", doc_id=doc_id, chunk_count=chunk_count,
        )
        return {
            "fileName": filename,
            "status": "success",
            "docId": doc_id,
            "chunkCount": chunk_count,
        }

    @staticmethod
    def _record_file(
        db: AsyncSession,
        job_id: str,
        file_name: str,
        file_size: int,
        file_type: str,
        *,
        status: str,
        doc_id: str = "",
        chunk_count: int = 0,
    ) -> None:
        record = BatchImportFileRecord(
            id=gen_id(),
            job_id=job_id,
            file_name=file_name,
            file_size=file_size,
            file_type=file_type,
            status=status,
            doc_id=doc_id or None,
            chunk_count=chunk_count,
        )
        db.add(record)

    @staticmethod
    def _record_file_error(
        db: AsyncSession,
        job_id: str,
        file_name: str,
        file_size: int,
        file_type: str,
        error: str,
    ) -> None:
        record = BatchImportFileRecord(
            id=gen_id(),
            job_id=job_id,
            file_name=file_name,
            file_size=file_size or 0,
            file_type=file_type or "txt",
            status="error",
            error_message=error or None,
        )
        db.add(record)

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
