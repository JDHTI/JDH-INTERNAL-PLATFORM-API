"""Async Redis adapter used by application modules."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis, from_url


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

    async def close(self) -> None:
        await self.client.aclose()
