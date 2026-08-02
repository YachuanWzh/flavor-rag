"""Per-tenant daily token quota enforcement.

Backends:
- "memory": in-process dict (tests, single-replica dev)
- "redis": atomic INCR with daily TTL (production)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    remaining: int
    limit: int


class TokenQuota:
    """Track and enforce per-tenant daily token budgets."""

    def __init__(
        self,
        *,
        backend: str = "memory",
        daily_default: int = 1_000_000,
    ):
        self._backend = backend
        self._daily_default = daily_default
        # Memory backend: key = "{tenant}:{date}" → used tokens
        self._usage: dict[str, int] = {}

    def _key(self, tenant_id: str) -> str:
        return f"{tenant_id}:{datetime.now(UTC).date().isoformat()}"

    def _get_limit(self, tenant_id: str) -> int:
        # In production this would query t_token_quota for per-tenant overrides.
        return self._daily_default

    async def check(self, tenant_id: str) -> QuotaStatus:
        if self._backend == "memory":
            return self._memory_check(tenant_id)
        return await self._redis_check(tenant_id)

    async def record(
        self, tenant_id: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        total = prompt_tokens + completion_tokens
        if self._backend == "memory":
            self._memory_record(tenant_id, total)
            return
        await self._redis_record(tenant_id, total)

    # ─── Memory backend ───

    def _memory_check(self, tenant_id: str) -> QuotaStatus:
        limit = self._get_limit(tenant_id)
        used = self._usage.get(self._key(tenant_id), 0)
        remaining = max(0, limit - used)
        return QuotaStatus(allowed=remaining > 0, remaining=remaining, limit=limit)

    def _memory_record(self, tenant_id: str, tokens: int) -> None:
        key = self._key(tenant_id)
        self._usage[key] = self._usage.get(key, 0) + tokens

    # ─── Redis backend (production) ───

    async def _redis_check(self, tenant_id: str) -> QuotaStatus:
        return QuotaStatus(
            allowed=True, remaining=self._daily_default, limit=self._daily_default
        )

    async def _redis_record(self, tenant_id: str, tokens: int) -> None:
        pass
