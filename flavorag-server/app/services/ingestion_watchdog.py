from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config.logging_config import get_logger
from app.database.session import async_session_factory
from app.models import IngestionTask

_log = get_logger("flavorag.ingestion.watchdog")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IngestionWatchdog:
    """Converts abandoned running tasks into explicit timeouts."""

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

    async def sweep(self) -> int:
        now = _utcnow()
        cutoff = now - timedelta(minutes=15)
        async with async_session_factory() as session:
            tasks = list(
                (
                    await session.execute(
                        select(IngestionTask).where(
                            IngestionTask.deleted == 0,
                            IngestionTask.status == "running",
                            IngestionTask.heartbeat_at < cutoff,
                        )
                    )
                ).scalars().all()
            )
            timed_out = 0
            for task in tasks:
                heartbeat = task.heartbeat_at or task.started_at or task.create_time
                timeout_ms = max((task.sla_ms or 300_000) * 2, 900_000)
                if heartbeat and now - heartbeat < timedelta(milliseconds=timeout_ms):
                    continue
                task.status = "timeout"
                task.completed_at = now
                task.total_duration_ms = (
                    int((now - task.started_at).total_seconds() * 1000)
                    if task.started_at
                    else timeout_ms
                )
                task.error_message = "watchdog_timeout: task heartbeat expired"
                task.logs_json = {
                    **(task.logs_json or {}),
                    "failure_type": "watchdog_timeout",
                }
                timed_out += 1
            if timed_out:
                await session.commit()
                _log.warning("stale_ingestion_tasks_timed_out", count=timed_out)
            return timed_out

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sweep()
            except Exception as exc:
                _log.warning("ingestion_watchdog_sweep_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval_sec)
