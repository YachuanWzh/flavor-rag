"""Reconcile active PostgreSQL chunk generations against Milvus."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.config.logging_config import get_logger
from app.models import (
    IndexRepairJob,
    KnowledgeBase,
    KnowledgeChunk,
)
from app.observability.metrics import INDEX_DRIFT, INDEX_ORPHANS
from app.services.schedule.lock_manager import ScheduleLockManager

_log = get_logger("flavorag.index_reconciliation")


class IndexReconciliationWorker:
    def __init__(self, poll_interval_sec: int = 1800):
        self.poll_interval_sec = poll_interval_sec
        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = ScheduleLockManager()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="index-reconciliation-worker"
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
                await self.run_once()
            except Exception as exc:
                _log.warning("index_reconciliation_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self) -> int:
        if not await self._lock.acquire("index-reconciliation"):
            return 0
        try:
            from app.database.session import async_session_factory
            from app.rag.search.vector import MilvusSearchChannel

            queued = 0
            async with async_session_factory() as session:
                bases = list(
                    (
                        await session.execute(
                            select(KnowledgeBase).where(
                                KnowledgeBase.deleted == 0
                            )
                        )
                    ).scalars().all()
                )
                channel = MilvusSearchChannel()
                for kb in bases:
                    chunks = list(
                        (
                            await session.execute(
                                select(KnowledgeChunk).where(
                                    KnowledgeChunk.kb_id == kb.id,
                                    KnowledgeChunk.index_status == "ACTIVE",
                                    KnowledgeChunk.deleted == 0,
                                )
                            )
                        ).scalars().all()
                    )
                    ids = [chunk.id for chunk in chunks]
                    existing = await asyncio.to_thread(
                        channel.existing_chunk_ids,
                        kb.active_collection_name or kb.collection_name,
                        ids,
                    )
                    missing = set(ids) - existing
                    physical_ids = await asyncio.to_thread(
                        channel.all_chunk_ids,
                        kb.active_collection_name or kb.collection_name,
                    )
                    orphans = physical_ids - set(ids)
                    INDEX_DRIFT.labels(kb_id=kb.id).set(len(missing))
                    INDEX_ORPHANS.labels(kb_id=kb.id).set(len(orphans))
                    if orphans:
                        await asyncio.to_thread(
                            channel.delete_by_ids,
                            kb.active_collection_name
                            or kb.collection_name,
                            sorted(orphans),
                        )
                        _log.info(
                            "index_orphans_removed",
                            kb_id=kb.id,
                            orphan_chunks=len(orphans),
                        )
                    if not missing:
                        continue
                    docs: dict[tuple[str, str], list[str]] = defaultdict(list)
                    for chunk in chunks:
                        if chunk.id in missing:
                            docs[(chunk.doc_id, chunk.generation)].append(
                                chunk.id
                            )
                    for (doc_id, generation), missing_ids in docs.items():
                        pending = (
                            await session.execute(
                                select(IndexRepairJob.id).where(
                                    IndexRepairJob.doc_id == doc_id,
                                    IndexRepairJob.generation == generation,
                                    IndexRepairJob.channel == "milvus",
                                    IndexRepairJob.status.in_(
                                        ["QUEUED", "RUNNING", "RETRY"]
                                    ),
                                    IndexRepairJob.deleted == 0,
                                )
                            )
                        ).scalar_one_or_none()
                        if pending is None:
                            session.add(
                                IndexRepairJob(
                                    kb_id=kb.id,
                                    doc_id=doc_id,
                                    generation=generation,
                                    channel="milvus",
                                    operation="REPLACE_DOCUMENT",
                                )
                            )
                            queued += 1
                            _log.warning(
                                "index_drift_detected",
                                kb_id=kb.id,
                                doc_id=doc_id,
                                missing_chunks=len(missing_ids),
                            )
                await session.commit()
            return queued
        finally:
            await self._lock.release("index-reconciliation")
