"""Entities of the DWH publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

PENDING = "pendiente"
PUBLISHED = "publicado"
FAILED = "fallido"
STATUSES = (PENDING, PUBLISHED, FAILED)


@dataclass(frozen=True)
class OutboxEvent:
    """A version of a record to publish: the whole record, never a diff."""

    topic: str
    entity_id: UUID
    version: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class OutboxItem:
    """An event as stored in the outbox."""

    id: int
    topic: str
    entity_id: UUID
    version: int
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class TopicStatus:
    """How far the publication of one topic is."""

    topic: str
    pending: int
    failed: int
    published: int
    oldest_pending_at: datetime | None
    last_published_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class FailedItem:
    """An event that ran out of attempts."""

    id: int
    topic: str
    entity_id: UUID
    version: int
    attempts: int
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True)
class PublishResult:
    """What one pass of the publisher did."""

    claimed: int = 0
    published: int = 0
    retried: int = 0
    failed: int = 0
    # The warehouse could not be reached: nothing was charged to the events.
    unavailable: bool = False


@dataclass(frozen=True)
class ReconcileResult:
    """Records the warehouse lacked (or had at an older version), queued again."""

    topic: str
    local: int
    remote: int
    queued: int
