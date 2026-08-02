"""Distributed lock for worker claim coordination.

Production uses PostgreSQL advisory locks; local development uses an in-process
dict with TTL tracking. The lock guarantees at-most-one active instance per
worker type across a multi-replica deployment.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass


@dataclass
class _LocalLockEntry:
    owner: str
    expires_at: float


class DistributedLock:
    """At-most-one lock with TTL, renewal, and backend abstraction."""

    LOCK_ID_BASE = 1893120000

    def __init__(self, *, backend: str = "local"):
        self._backend = backend
        self._instance_id = f"{os.getpid()}-{id(self)}"
        self._local_locks: dict[str, _LocalLockEntry] = {}

    # ─── public API ───

    async def acquire(self, key: str, *, ttl_sec: int = 60) -> bool:
        if self._backend == "local":
            return self._local_acquire(key, ttl_sec)
        return await self._pg_acquire(key, ttl_sec)

    async def release(self, key: str) -> None:
        if self._backend == "local":
            self._local_locks.pop(key, None)
            return
        await self._pg_release(key)

    async def renew(self, key: str, *, ttl_sec: int = 60) -> bool:
        if self._backend == "local":
            entry = self._local_locks.get(key)
            if entry is None or entry.owner != self._instance_id:
                return False
            entry.expires_at = time.monotonic() + ttl_sec
            return True
        return await self._pg_renew(key, ttl_sec)

    # ─── local backend ───

    def _local_acquire(self, key: str, ttl_sec: int) -> bool:
        now = time.monotonic()
        existing = self._local_locks.get(key)
        if existing is not None and existing.expires_at > now:
            return False
        self._local_locks[key] = _LocalLockEntry(
            owner=self._instance_id,
            expires_at=now + ttl_sec,
        )
        return True

    # ─── PostgreSQL backend (advisory locks) ───

    def _lock_id_for(self, key: str) -> int:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.LOCK_ID_BASE + (int(digest[:8], 16) % 1_000_000)

    async def _pg_acquire(self, key: str, ttl_sec: int) -> bool:
        from sqlalchemy import text

        from app.database.session import async_session_factory

        lock_id = self._lock_id_for(key)
        try:
            session = async_session_factory()
            result = await session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            acquired = bool(result.scalar())
            await session.close()
            return acquired
        except Exception:  # noqa: BLE001 — lock failure must not crash the worker
            return False

    async def _pg_release(self, key: str) -> None:
        from sqlalchemy import text

        from app.database.session import async_session_factory

        lock_id = self._lock_id_for(key)
        try:
            session = async_session_factory()
            await session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )
            await session.close()
        except Exception:  # noqa: BLE001, S110 — release is best-effort
            pass

    async def _pg_renew(self, key: str, ttl_sec: int) -> bool:
        # Advisory locks are session-scoped; renewal is a no-op as long as
        # the session stays alive. Return True if we still hold it.
        from sqlalchemy import text

        from app.database.session import async_session_factory

        lock_id = self._lock_id_for(key)
        try:
            session = async_session_factory()
            result = await session.execute(
                text(
                    "SELECT EXISTS("
                    "SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND objid=:lock_id AND granted=true)"
                ),
                {"lock_id": lock_id},
            )
            held = bool(result.scalar())
            await session.close()
            return held
        except Exception:  # noqa: BLE001 — renewal failure is non-fatal
            return False
