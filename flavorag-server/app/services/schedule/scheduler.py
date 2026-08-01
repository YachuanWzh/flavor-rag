"""Document schedule scheduler — periodic poll loop for scheduled document refresh.

Integrates with FastAPI lifespan. Polls t_knowledge_document_schedule
for due jobs and processes them with distributed locking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.session import async_session_factory
from app.models import KnowledgeDocumentSchedule
from app.services.schedule.lock_manager import ScheduleLockManager
from app.services.schedule.refresh_processor import RefreshProcessor

_log = get_logger("flavorag.schedule.engine")


def calculate_next_run(
    cron_expr: str,
    *,
    now_utc: datetime | None = None,
) -> datetime:
    """Calculate the next fire time and return it as naive UTC for storage."""
    aware_now = now_utc or datetime.now(timezone.utc)
    if aware_now.tzinfo is None:
        aware_now = aware_now.replace(tzinfo=timezone.utc)
    else:
        aware_now = aware_now.astimezone(timezone.utc)

    try:
        interval_sec = int(cron_expr)
        return (aware_now + timedelta(seconds=interval_sec)).replace(
            tzinfo=None
        )
    except (ValueError, TypeError):
        pass

    if not cron_expr:
        raise ValueError("cron expression is required")
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger.from_crontab(
        cron_expr,
        timezone=ZoneInfo(settings.app_timezone),
    )
    next_fire = trigger.get_next_fire_time(None, aware_now)
    if next_fire is None:
        raise ValueError(f"cron expression has no next fire time: {cron_expr}")
    return next_fire.astimezone(timezone.utc).replace(tzinfo=None)


class DocumentScheduleScheduler:
    """Periodically scans for due document schedules and triggers refresh.

    Lifecycle: start() creates an asyncio Task; stop() cancels it.
    """

    def __init__(self, poll_interval_sec: int = 60):
        self.poll_interval = poll_interval_sec
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock_manager = ScheduleLockManager()
        self._processor = RefreshProcessor()

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        _log.info(
            "schedule_scheduler_started",
            poll_interval_sec=self.poll_interval,
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log.info("schedule_scheduler_stopped")

    async def _poll_loop(self):
        while self._running:
            try:
                await self._poll_once()
            except Exception as exc:
                _log.error("schedule_poll_failed", error=str(exc))
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self):
        """Scan for due schedules and process them one at a time."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(KnowledgeDocumentSchedule).where(
                        KnowledgeDocumentSchedule.enabled == 1,
                        KnowledgeDocumentSchedule.next_run_time <= now,
                    ).order_by(KnowledgeDocumentSchedule.next_run_time)
                )
                due_schedules = result.scalars().all()

                if not due_schedules:
                    return

                _log.info("schedule_poll", due_count=len(due_schedules))

                for sched in due_schedules:
                    await self._process_schedule(sched, session)
        except Exception as exc:
            _log.error("schedule_scan_failed", error=str(exc))

    async def _process_schedule(
        self, sched: KnowledgeDocumentSchedule, session
    ) -> None:
        """Acquire lock and process a single schedule."""
        # Try to acquire distributed lock
        acquired = await self._lock_manager.acquire(sched.doc_id)
        if not acquired:
            _log.debug("schedule_lock_busy", doc_id=sched.doc_id)
            return

        try:
            # Refresh the schedule row within this session
            from sqlalchemy import select as sel
            result = await session.execute(
                sel(KnowledgeDocumentSchedule).where(
                    KnowledgeDocumentSchedule.doc_id == sched.doc_id
                )
            )
            sched = result.scalar_one_or_none()
            if not sched:
                return

            sched.last_run_time = datetime.now(timezone.utc).replace(tzinfo=None)

            # Process the refresh
            await self._processor.process(sched)

            # Compute next run time from cron expression (simple interval-based)
            self._update_next_run(sched)

            await session.flush()
        except Exception as exc:
            _log.error("schedule_process_failed", doc_id=sched.doc_id, error=str(exc))
            sched.last_status = "error"
            sched.last_error = str(exc)[:500]
        finally:
            await self._lock_manager.release(sched.doc_id)

    def _update_next_run(self, sched: KnowledgeDocumentSchedule) -> None:
        """Update next_run_time based on cron expression.

        Supports simple interval-in-seconds format (e.g., "3600" = 1 hour)
        in addition to standard cron expressions.
        """
        sched.next_run_time = calculate_next_run(sched.cron_expr or "")
