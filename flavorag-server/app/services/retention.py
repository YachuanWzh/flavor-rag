"""Retention worker for sensitive RAG traces and completed job payloads."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.models import (
    EvaluationRun,
    KnowledgeIndexGeneration,
    RagTraceNode,
    RagTraceRun,
)
from app.services.schedule.lock_manager import ScheduleLockManager

_log = get_logger("flavorag.retention")


class RetentionWorker:
    def __init__(self, poll_interval_sec: int = 3600):
        self.poll_interval_sec = poll_interval_sec
        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = ScheduleLockManager()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name="retention-worker"
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
                _log.warning("retention_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval_sec)

    async def run_once(self) -> int:
        if not await self._lock.acquire("retention-worker"):
            return 0
        try:
            from app.database.session import async_session_factory

            cutoff = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(days=max(1, settings.trace_retention_days))
            )
            async with async_session_factory() as session:
                trace_ids = list(
                    (
                        await session.execute(
                            select(RagTraceRun.id).where(
                                RagTraceRun.create_time < cutoff
                            )
                        )
                    ).scalars().all()
                )
                if trace_ids:
                    await session.execute(
                        delete(RagTraceNode).where(
                            RagTraceNode.trace_run_id.in_(trace_ids)
                        )
                    )
                    await session.execute(
                        delete(RagTraceRun).where(
                            RagTraceRun.id.in_(trace_ids)
                        )
                    )
                # Detailed answer/context payloads are operational artifacts,
                # not permanent business records. Preserve aggregate metrics.
                old_runs = list(
                    (
                        await session.execute(
                            select(EvaluationRun).where(
                                EvaluationRun.create_time < cutoff,
                                EvaluationRun.results_json.is_not(None),
                            )
                        )
                    ).scalars().all()
                )
                for run in old_runs:
                    run.results_json = None
                index_cutoff = (
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(
                        days=max(1, settings.index_retired_retention_days)
                    )
                )
                retired = list(
                    (
                        await session.execute(
                            select(KnowledgeIndexGeneration).where(
                                KnowledgeIndexGeneration.status.in_(
                                    ["RETIRED", "FAILED"]
                                ),
                                KnowledgeIndexGeneration.update_time
                                < index_cutoff,
                                KnowledgeIndexGeneration.deleted == 0,
                            )
                        )
                    ).scalars().all()
                )
                if retired:
                    from app.rag.search.vector import MilvusSearchChannel

                    channel = MilvusSearchChannel()
                    for generation in retired:
                        await asyncio.to_thread(
                            channel.drop_collection,
                            generation.collection_name,
                        )
                        generation.status = "DELETED"
                        generation.deleted = 1
                await session.commit()
                removed = len(trace_ids) + len(old_runs) + len(retired)
                if removed:
                    _log.info(
                        "retention_complete",
                        traces=len(trace_ids),
                        evaluation_payloads=len(old_runs),
                        index_generations=len(retired),
                    )
                return removed
        finally:
            await self._lock.release("retention-worker")
