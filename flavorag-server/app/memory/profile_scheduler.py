"""Profile daily scheduler — periodic full profile rebuild for all users.

When profile_update_mode == "daily", this scheduler runs at the configured
cron time and rebuilds profiles for all active users.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, distinct

from app.config.logging_config import get_logger
from app.config.settings import settings
from app.database.session import async_session_factory
from app.models import User, Message
from app.memory.profile_builder import build_or_update_profile

_log = get_logger("flavorag.memory.scheduler")


class ProfileDailyScheduler:
    """Periodically rebuild user profiles for all active users."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        # Default: check every hour if it's time to run
        self._poll_interval_sec = 3600

    async def start(self):
        if self._running:
            return
        if settings.profile_update_mode != "daily":
            _log.info("profile_scheduler_disabled", mode=settings.profile_update_mode)
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        _log.info("profile_scheduler_started", cron=settings.profile_daily_cron)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log.info("profile_scheduler_stopped")

    async def _poll_loop(self):
        """Poll loop: checks if current time matches the cron schedule."""
        while self._running:
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=True)
                # Parse simple "H M * * *" cron (hour minute)
                parts = settings.profile_daily_cron.split()
                if len(parts) >= 2:
                    target_hour = int(parts[0])
                    target_minute = int(parts[1])
                    # Check if we're within the target hour and past the target minute
                    if now.hour == target_hour and now.minute >= target_minute:
                        await self._rebuild_all()
                        # Sleep until next hour to avoid re-running
                        await asyncio.sleep(self._poll_interval_sec)
            except Exception as exc:
                _log.error("profile_poll_failed", error=str(exc)[:200])

            await asyncio.sleep(self._poll_interval_sec)

    async def _rebuild_all(self):
        """Rebuild profiles for all users who have at least one query."""
        _log.info("profile_daily_rebuild_start")
        try:
            async with async_session_factory() as session:
                # Get all users who have messages
                result = await session.execute(
                    select(distinct(Message.user_id)).where(
                        Message.role == "user", Message.deleted == 0
                    )
                )
                user_ids = [row[0] for row in result if row[0]]

            _log.info("profile_daily_rebuild_users", count=len(user_ids))

            for user_id in user_ids:
                try:
                    async with async_session_factory() as session:
                        # Get tenant_id from User
                        user_result = await session.execute(
                            select(User.tenant_id).where(User.id == user_id)
                        )
                        tenant_id = user_result.scalar() or "default"
                        await build_or_update_profile(session, user_id, tenant_id)
                        await session.commit()
                except Exception as exc:
                    _log.error(
                        "profile_rebuild_user_failed",
                        user_id=user_id,
                        error=str(exc)[:200],
                    )

            _log.info("profile_daily_rebuild_complete", count=len(user_ids))
        except Exception as exc:
            _log.error("profile_daily_rebuild_failed", error=str(exc)[:200])
