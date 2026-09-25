"""Async Redis adapter used by application modules."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis, from_url

_SLIDING_WINDOW_SCRIPT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4] .. '-' .. redis.call('INCR', KEYS[2])
local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry = window
    if oldest[2] then
        retry = math.max(1, math.ceil(window - (now - tonumber(oldest[2]))))
    end
    redis.call('EXPIRE', KEYS[1], math.ceil(window / 1000) + 1)
    redis.call('EXPIRE', KEYS[2], math.ceil(window / 1000) + 1)
    return {0, count, retry}
end

redis.call('ZADD', KEYS[1], now, member)
redis.call('EXPIRE', KEYS[1], math.ceil(window / 1000) + 1)
redis.call('EXPIRE', KEYS[2], math.ceil(window / 1000) + 1)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of one atomic sliding-window admission check."""

    allowed: bool
    count: int
    retry_after: int = 0


class RedisClient:
    """Small infrastructure wrapper around redis-py asyncio."""

    def __init__(
        self,
        url: str,
        key_prefix: str = "app",
        default_ttl: int = 3600,
        connect_timeout: float = 2.0,
    ) -> None:
        self.key_prefix = key_prefix.strip(":")
        self.default_ttl = default_ttl
        self.client: Redis = from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=connect_timeout,
            socket_timeout=connect_timeout,
            health_check_interval=30,
        )

    def key(self, value: str) -> str:
        return f"{self.key_prefix}:{value}"

    async def check(self) -> None:
        """Verify connectivity with Redis PING."""

        await self.client.ping()

    async def get(self, key: str) -> Any:
        return await self.client.get(self.key(key))

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        return bool(await self.client.set(self.key(key), value, ex=ttl or self.default_ttl))

    async def delete(self, key: str) -> int:
        return int(await self.client.delete(self.key(key)))

    async def allow_sliding_window(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        """Atomically admit one request in a Redis-backed sliding window.

        Args:
            key (str): Logical bucket name without the global prefix.
            limit (int): Maximum requests in the window.
            window_seconds (int): Window duration in seconds.

        Returns:
            RateLimitDecision: Whether the request was admitted and its count.
        """

        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        bucket_key = self.key(f"rate-limit:{key}")
        sequence_key = self.key(f"rate-limit:{key}:sequence")
        result = await self.client.eval(
            _SLIDING_WINDOW_SCRIPT,
            2,
            bucket_key,
            sequence_key,
            now_ms,
            window_ms,
            limit,
            uuid4().hex,
        )
        allowed, count, retry_after_ms = (int(value) for value in result)
        return RateLimitDecision(
            allowed=bool(allowed), count=count, retry_after=retry_after_ms // 1000 or 1
        )

    async def close(self) -> None:
        await self.client.aclose()


__all__ = ["RateLimitDecision", "RedisClient"]
