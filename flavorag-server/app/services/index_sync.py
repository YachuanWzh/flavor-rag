from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.config.logging_config import get_logger
from app.ingestion.pdf.asset_storage import S3PdfAssetStorage
from sqlalchemy import or_, select

from app.models import IndexSyncJob, KnowledgeAsset, KnowledgeBase, KnowledgeChunk, gen_id
from app.rag.graph.lightrag_client import LightRAGClient
from app.rag.graph.neo4j_store import Neo4jGraphStore
from app.rag.search.keyword import ESKeywordSearchChannel
from app.rag.search.vector import MilvusSearchChannel

_log = get_logger("flavorag.index_sync")


class IndexSyncService:
    """Idempotent external-index cleanup with a relational retry record."""

    async def delete_document(
        self,
        session,
        *,
        kb: KnowledgeBase,
        doc_id: str,
        chunks: list[KnowledgeChunk],
        assets: list[KnowledgeAsset],
        source_location: str | None = None,
    ) -> IndexSyncJob:
        chunk_ids = sorted({chunk.id for chunk in chunks})
        storage_keys = sorted({asset.storage_key for asset in assets if asset.storage_key})
        job = IndexSyncJob(
            id=gen_id(),
            tenant_id=kb.tenant_id or "default",
            kb_id=kb.id,
            doc_id=doc_id,
            operation="DELETE_DOCUMENT",
            payload_json={
                "chunk_ids": chunk_ids,
                "storage_keys": storage_keys,
                "source_location": source_location,
            },
            channel_status_json={},
            status="RUNNING",
            attempts=1,
        )
        session.add(job)
        await session.flush()

        statuses: dict[str, dict] = {}

        async def execute(name: str, operation) -> None:
            try:
                await operation()
                statuses[name] = {"status": "success"}
            except Exception as exc:
                statuses[name] = {
                    "status": "error",
                    "error": type(exc).__name__,
                }
                _log.warning(
                    "index_cleanup_failed",
                    channel=name,
                    doc_id=doc_id,
                    error=str(exc),
                )

        async def delete_milvus():
            await asyncio.to_thread(
                MilvusSearchChannel().delete_by_ids,
                kb.active_collection_name or kb.collection_name,
                chunk_ids,
            )

        async def delete_graph():
            await Neo4jGraphStore().delete_document(kb_id=kb.id, doc_id=doc_id)
            try:
                await LightRAGClient().delete_document(kb.id, doc_id)
            except Exception as exc:
                _log.warning(
                    "lightrag_delete_failed",
                    doc_id=doc_id,
                    error=str(exc),
                )

        operations = [
            execute("milvus", delete_milvus),
            execute("elasticsearch", lambda: ESKeywordSearchChannel().delete_by_ids(chunk_ids)),
            execute("graph", delete_graph),
        ]
        if storage_keys:
            operations.append(
                execute(
                    "object_storage",
                    lambda: S3PdfAssetStorage().delete_keys(storage_keys),
                )
            )
        if source_location:
            from app.ingestion.source_storage import delete_source

            operations.append(
                execute(
                    "source_storage",
                    lambda: delete_source(source_location),
                )
            )
        await asyncio.gather(*operations)

        failed = [name for name, value in statuses.items() if value["status"] == "error"]
        job.channel_status_json = statuses
        if failed:
            job.status = "RETRY"
            job.last_error = ",".join(failed)
            job.next_retry_time = (
                datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
            )
        else:
            job.status = "SUCCESS"
            job.last_error = None
            job.next_retry_time = None
        return job

    async def retry_pending(self, session, *, limit: int = 20) -> int:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        jobs = list(
            (
                await session.execute(
                    select(IndexSyncJob)
                    .where(
                        IndexSyncJob.deleted == 0,
                        IndexSyncJob.status.in_(["PENDING", "RETRY"]),
                        or_(
                            IndexSyncJob.next_retry_time.is_(None),
                            IndexSyncJob.next_retry_time <= now,
                        ),
                    )
                    .order_by(IndexSyncJob.create_time)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        completed = 0
        for job in jobs:
            kb = (
                await session.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.id == job.kb_id,
                        KnowledgeBase.tenant_id == job.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if kb is None:
                job.status = "FAILED"
                job.last_error = "knowledge_base_missing"
                continue
            await self._retry_job(job, kb)
            if job.status == "SUCCESS":
                completed += 1
        return completed

    async def _retry_job(self, job: IndexSyncJob, kb: KnowledgeBase) -> None:
        payload = job.payload_json or {}
        chunk_ids = list(payload.get("chunk_ids", []))
        storage_keys = list(payload.get("storage_keys", []))
        source_location = payload.get("source_location")
        statuses: dict[str, dict] = {}
        job.status = "RUNNING"
        job.attempts = (job.attempts or 0) + 1

        async def execute(name: str, operation) -> None:
            try:
                await operation()
                statuses[name] = {"status": "success"}
            except Exception as exc:
                statuses[name] = {
                    "status": "error",
                    "error": type(exc).__name__,
                }

        async def delete_milvus():
            await asyncio.to_thread(
                MilvusSearchChannel().delete_by_ids,
                kb.active_collection_name or kb.collection_name,
                chunk_ids,
            )

        async def delete_graph():
            await Neo4jGraphStore().delete_document(
                kb_id=kb.id,
                doc_id=job.doc_id,
            )
            try:
                await LightRAGClient().delete_document(kb.id, job.doc_id)
            except Exception as exc:
                _log.warning(
                    "lightrag_delete_failed",
                    doc_id=job.doc_id,
                    error=str(exc),
                )

        operations = [
            execute("milvus", delete_milvus),
            execute(
                "elasticsearch",
                lambda: ESKeywordSearchChannel().delete_by_ids(chunk_ids),
            ),
            execute(
                "graph",
                delete_graph,
            ),
        ]
        if storage_keys:
            operations.append(
                execute(
                    "object_storage",
                    lambda: S3PdfAssetStorage().delete_keys(storage_keys),
                )
            )
        if source_location:
            from app.ingestion.source_storage import delete_source

            operations.append(
                execute(
                    "source_storage",
                    lambda: delete_source(source_location),
                )
            )
        await asyncio.gather(*operations)
        failed = [name for name, value in statuses.items() if value["status"] == "error"]
        job.channel_status_json = statuses
        if failed:
            job.status = "RETRY"
            job.last_error = ",".join(failed)
            delay_minutes = min(60, 2 ** min(job.attempts, 6))
            job.next_retry_time = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(minutes=delay_minutes)
            )
        else:
            job.status = "SUCCESS"
            job.last_error = None
            job.next_retry_time = None


class IndexSyncRetryScheduler:
    def __init__(self, poll_interval_sec: int = 60):
        self.poll_interval_sec = poll_interval_sec
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
                async with async_session_factory() as session:
                    await IndexSyncService().retry_pending(session)
                    await session.commit()
            except Exception as exc:
                _log.warning("index_sync_retry_poll_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval_sec)
