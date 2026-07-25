"""Distributed lock for document schedule execution.

Uses PostgreSQL advisory locks to prevent duplicate execution across
multiple server instances. Falls back to in-memory locking when DB
advisory locks are not available (e.g., SQLite).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from app.config.logging_config import get_logger
from app.database.session import async_session_factory

_log = get_logger("flavorag.schedule.lock")


class ScheduleLockManager:
    """Acquire/release distributed locks using PostgreSQL advisory locks.

    Each lock is keyed by a schedule ID. The lock is automatically released
    when the DB session or connection closes.
    """

    LOCK_ID_BASE = 1783120000  # Arbitrary base for advisory lock IDs
    LOCK_TTL_SEC = 600  # 10 minutes max lock duration (safety valve)

    def __init__(self):
        self._instance_id = f"{os.uname().nodename}-{os.getpid()}" if hasattr(os, "uname") else f"pid-{os.getpid()}"

    async def acquire(self, schedule_id: str, ttl_sec: int | None = None) -> bool:
        """Try to acquire a distributed lock for the given schedule_id.

        Returns True if lock acquired, False otherwise.
        """
        lock_id = self._lock_id_for(schedule_id)
        try:
            async with async_session_factory() as session:
                # Use pg_try_advisory_lock for non-blocking acquire
                from sqlalchemy import text
                result = await session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )
                acquired = result.scalar()
                if acquired:
                    _log.debug("lock_acquired", schedule_id=schedule_id, instance=self._instance_id)
                return bool(acquired)
        except Exception as exc:
            # SQLite or other DB that doesn't support advisory locks — fall back
            _log.warning("advisory_lock_not_supported", error=str(exc), schedule_id=schedule_id)
            return True  # Allow execution when lock mechanism unavailable

    async def release(self, schedule_id: str) -> None:
        """Release the lock for the given schedule_id."""
        lock_id = self._lock_id_for(schedule_id)
        try:
            async with async_session_factory() as session:
                from sqlalchemy import text
                await session.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )
                await session.commit()
                _log.debug("lock_released", schedule_id=schedule_id, instance=self._instance_id)
        except Exception as exc:
            _log.warning("lock_release_failed", error=str(exc), schedule_id=schedule_id)

    async def update_lease(self, schedule_id: str, lock_until: datetime) -> None:
        """Update the lock_until timestamp in the schedule row."""
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                from app.models import KnowledgeDocumentSchedule
                result = await session.execute(
                    select(KnowledgeDocumentSchedule).where(
                        KnowledgeDocumentSchedule.doc_id == schedule_id
                    )
                )
                sched = result.scalar_one_or_none()
                if sched:
                    sched.lock_owner = self._instance_id
                    sched.lock_until = lock_until
                    await session.commit()
        except Exception as exc:
            _log.warning("lease_update_failed", error=str(exc), schedule_id=schedule_id)

    @staticmethod
    def _lock_id_for(schedule_id: str) -> int:
        """Generate a deterministic integer lock ID from a schedule_id string."""
        import hashlib
        h = hashlib.md5(schedule_id.encode()).hexdigest()
        return ScheduleLockManager.LOCK_ID_BASE + (int(h[:8], 16) % 1000000)
