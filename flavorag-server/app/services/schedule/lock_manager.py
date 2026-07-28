"""Session-correct distributed lock for scheduled document work."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from app.config.logging_config import get_logger
from app.database.session import async_session_factory

_log = get_logger("flavorag.schedule.lock")


def lock_failure_allows_execution() -> bool:
    """Lock subsystem failures fail closed to prevent duplicate execution."""
    return False


class ScheduleLockManager:
    """Retain the PostgreSQL session that owns each advisory lock."""

    LOCK_ID_BASE = 1783120000
    LOCK_TTL_SEC = 600

    def __init__(self):
        host = os.uname().nodename if hasattr(os, "uname") else "windows"
        self._instance_id = f"{host}-{os.getpid()}"
        self._sessions: dict[str, Any] = {}
        self._local_locks: set[str] = set()

    async def acquire(self, schedule_id: str, ttl_sec: int | None = None) -> bool:
        del ttl_sec  # lease expiry is maintained in the schedule row
        lock_id = self._lock_id_for(schedule_id)
        session = None
        try:
            session = async_session_factory()
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "sqlite":
                if schedule_id in self._local_locks:
                    await session.close()
                    return False
                self._local_locks.add(schedule_id)
                await session.close()
                return True

            from sqlalchemy import text

            result = await session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            acquired = bool(result.scalar())
            if acquired:
                self._sessions[schedule_id] = session
                _log.debug(
                    "lock_acquired",
                    schedule_id=schedule_id,
                    instance=self._instance_id,
                )
            else:
                await session.close()
            return acquired
        except Exception as exc:
            if session is not None:
                await session.close()
            _log.warning(
                "advisory_lock_failed",
                error=str(exc),
                schedule_id=schedule_id,
            )
            return lock_failure_allows_execution()

    async def release(self, schedule_id: str) -> None:
        if schedule_id in self._local_locks:
            self._local_locks.discard(schedule_id)
            return
        session = self._sessions.pop(schedule_id, None)
        if session is None:
            return
        try:
            from sqlalchemy import text

            await session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": self._lock_id_for(schedule_id)},
            )
            _log.debug(
                "lock_released",
                schedule_id=schedule_id,
                instance=self._instance_id,
            )
        except Exception as exc:
            _log.warning(
                "lock_release_failed",
                error=str(exc),
                schedule_id=schedule_id,
            )
        finally:
            await session.close()

    async def update_lease(
        self, schedule_id: str, lock_until: datetime
    ) -> None:
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select

                from app.models import KnowledgeDocumentSchedule

                result = await session.execute(
                    select(KnowledgeDocumentSchedule).where(
                        KnowledgeDocumentSchedule.doc_id == schedule_id
                    )
                )
                schedule = result.scalar_one_or_none()
                if schedule:
                    schedule.lock_owner = self._instance_id
                    schedule.lock_until = lock_until
                    await session.commit()
        except Exception as exc:
            _log.warning(
                "lease_update_failed",
                error=str(exc),
                schedule_id=schedule_id,
            )

    @staticmethod
    def _lock_id_for(schedule_id: str) -> int:
        digest = hashlib.sha256(schedule_id.encode("utf-8")).hexdigest()
        return ScheduleLockManager.LOCK_ID_BASE + (
            int(digest[:8], 16) % 1_000_000
        )
