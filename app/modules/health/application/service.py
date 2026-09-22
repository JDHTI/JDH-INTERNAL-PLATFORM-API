"""Health and readiness use case."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.logging import logger
from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage


@dataclass(frozen=True)
class ComponentResult:
    """Result of one dependency probe."""

    status: str
    latency_ms: float


class HealthService:
    """Application service that coordinates dependency checks."""

    def __init__(self, database: Database, redis: RedisClient, storage: S3Storage) -> None:
        self.database = database
        self.redis = redis
        self.storage = storage

    async def readiness(self) -> tuple[bool, dict[str, ComponentResult]]:
        checks: dict[str, Callable[[], Awaitable[None]]] = {
            "postgres": self.database.check,
            "redis": self.redis.check,
            "s3": self.storage.check,
        }
        results = await asyncio.gather(
            *(self._probe(name, probe) for name, probe in checks.items())
        )
        components = {name: result for name, result in results}
        return all(result.status == "ok" for result in components.values()), components

    async def _probe(
        self, name: str, probe: Callable[[], Awaitable[None]]
    ) -> tuple[str, ComponentResult]:
        started = time.perf_counter()
        try:
            await probe()
        except Exception:  # noqa: BLE001 - readiness must report all dependencies together
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("Readiness probe failed for %s", name)
            return name, ComponentResult(status="unavailable", latency_ms=round(latency_ms, 2))
        latency_ms = (time.perf_counter() - started) * 1000
        return name, ComponentResult(status="ok", latency_ms=round(latency_ms, 2))
