"""Ports of the DWH publication."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.dwh_publish.domain.entities import (
    FailedItem,
    OutboxEvent,
    OutboxItem,
    TopicStatus,
)


class DwhUnavailable(Exception):
    """The warehouse cannot be reached (network, credentials, timeouts): retry later."""


class DwhRejected(Exception):
    """The warehouse refused the data itself (a constraint, a type): retrying alone won't fix it."""


class WarehouseWriter(Protocol):
    """A short transaction on the warehouse with the writer role."""

    def transaction(self) -> AbstractAsyncContextManager[Any]: ...

    async def close(self) -> None: ...


class Topic(Protocol):
    """How the events of one kind of record land in the warehouse."""

    name: str

    async def write(self, connection: Any, events: Sequence[OutboxItem]) -> None:
        """Upsert the events in the warehouse inside the open transaction ``connection``."""

    async def remote_versions(self, connection: Any) -> dict[UUID, int]:
        """``id -> version`` of the records the warehouse holds."""

    async def local_versions(self) -> dict[UUID, int]:
        """``id -> version`` of the records the platform holds."""

    async def snapshots(self, ids: Sequence[UUID]) -> list[OutboxEvent]:
        """The current events of these records, to publish them again."""


class OutboxRepository(Protocol):
    """The outbox table."""

    async def claim(self, limit: int, lease_seconds: float, now: datetime) -> list[OutboxItem]: ...

    async def mark_published(self, ids: Sequence[int], now: datetime) -> None: ...

    async def mark_retry(
        self, ids: Sequence[int], error: str, next_attempt_at: datetime, *, charge: bool
    ) -> None: ...

    async def mark_failed(self, ids: Sequence[int], error: str) -> None: ...

    async def release(self, ids: Sequence[int]) -> None: ...

    async def add(self, events: Sequence[OutboxEvent]) -> int: ...

    async def status(self) -> list[TopicStatus]: ...

    async def failed(self, limit: int) -> list[FailedItem]: ...

    async def retry_failed(self, ids: Sequence[int] | None, now: datetime) -> int: ...

    async def pending_versions(self, topic: str) -> dict[UUID, int]: ...
