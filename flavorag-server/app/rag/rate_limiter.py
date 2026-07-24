"""Redis sliding-window rate limiter. Degrades gracefully when Redis is down."""
from __future__ import annotations

import time
from app.config.settings import settings


class RateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets.

    Args:
        user_limit: Max requests per window for a user (default 60).
        ip_limit: Max requests per window for an IP (default 600).
        window_sec: Sliding window size in seconds (default 60).
    """

    def __init__(
        self,
        user_limit: int = 60,
        ip_limit: int = 600,
        window_sec: int = 60,
    ):
        self.user_limit = user_limit
        self.ip_limit = ip_limit
        self.window_sec = window_sec
        self._redis = None

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            await self._redis.ping()
        except Exception:
            self._redis = None
        return self._redis

    async def check_user(self, user_id: str) -> bool:
        """Return True if allowed, False if rate-limited."""
        return await self._check(f"rl:user:{user_id}", self.user_limit)

    async def check_ip(self, ip: str) -> bool:
        """Return True if allowed, False if rate-limited."""
        return await self._check(f"rl:ip:{ip}", self.ip_limit)

    async def _check(self, key: str, limit: int) -> bool:
        r = await self._get_redis()
        if r is None:
            return True  # Degrade: allow all when Redis unavailable

        now = time.time()
        window_start = now - self.window_sec

        try:
            await r.zremrangebyscore(key, 0, window_start)
            count = await r.zcard(key)
            if count >= limit:
                return False
            await r.zadd(key, {str(now): now})
            await r.expire(key, self.window_sec * 2)
            return True
        except Exception:
            return True  # Degrade on error
