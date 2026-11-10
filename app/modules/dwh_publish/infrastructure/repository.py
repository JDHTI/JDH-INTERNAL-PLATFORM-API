"""PostgreSQL repository of the DWH outbox."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update

from app.infrastructure.database import Database
from app.modules.dwh_publish.domain.entities import (
    FAILED,
    PENDING,
    PUBLISHED,
    FailedItem,
    OutboxEvent,
    OutboxItem,
    TopicStatus,
)
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel
from app.modules.dwh_publish.infrastructure.outbox import enqueue

Ob = DwhOutboxModel
_ERROR_LENGTH = 2000


class SqlAlchemyOutboxRepository:
    """The outbox, one short transaction per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def claim(self, limit: int, lease_seconds: float, now: datetime) -> list[OutboxItem]:
        """Take up to ``limit`` due events, oldest first, and lease them.

        The lease (``locked_until``) is committed before the warehouse is called, so no
        local transaction stays open while waiting for the network. If the process dies,
        the lease runs out and the events are taken again.
        """

        due = (
            select(Ob.id)
            .where(
                Ob.status == PENDING,
                Ob.next_attempt_at <= now,
                or_(Ob.locked_until.is_(None), Ob.locked_until < now),
            )
            .order_by(Ob.next_attempt_at, Ob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with self.database.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    update(Ob)
                    .where(Ob.id.in_(due.scalar_subquery()))
                    .values(locked_until=now + timedelta(seconds=lease_seconds))
                    .returning(Ob.id, Ob.topic, Ob.entity_id, Ob.version, Ob.payload, Ob.attempts)
                )
            ).all()
        items = [OutboxItem(*row) for row in rows]
        return sorted(items, key=lambda item: item.id)

    async def mark_published(self, ids: Sequence[int], now: datetime) -> None:
        """The events reached the warehouse."""

        if not ids:
            return
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(Ob)
                .where(Ob.id.in_(ids))
                .values(status=PUBLISHED, published_at=now, locked_until=None, last_error=None)
            )

    async def mark_retry(
        self, ids: Sequence[int], error: str, next_attempt_at: datetime, *, charge: bool
    ) -> None:
        """Try again later; ``charge`` counts the attempt (not when the DWH was just down)."""

        if not ids:
            return
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(Ob)
                .where(Ob.id.in_(ids))
                .values(
                    attempts=Ob.attempts + (1 if charge else 0),
                    next_attempt_at=next_attempt_at,
                    locked_until=None,
                    last_error=error[:_ERROR_LENGTH],
                )
            )

    async def mark_failed(self, ids: Sequence[int], error: str) -> None:
        """Out of attempts: kept aside until a person retries them."""

        if not ids:
            return
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(Ob)
                .where(Ob.id.in_(ids))
                .values(
                    status=FAILED,
                    attempts=Ob.attempts + 1,
                    locked_until=None,
                    last_error=error[:_ERROR_LENGTH],
                )
            )

    async def release(self, ids: Sequence[int]) -> None:
        """Give the lease back without counting anything (the worker is stopping)."""

        if not ids:
            return
        async with self.database.session_factory() as session, session.begin():
            await session.execute(update(Ob).where(Ob.id.in_(ids)).values(locked_until=None))

    async def add(self, events: Sequence[OutboxEvent]) -> int:
        """Queue events again outside any change (reconciliation); returns how many."""

        if not events:
            return 0
        async with self.database.session_factory() as session, session.begin():
            await enqueue(session, events, requeue=True)
        return len(events)

    async def status(self) -> list[TopicStatus]:
        """Counts and ages by topic."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Ob.topic,
                        func.count().filter(Ob.status == PENDING),
                        func.count().filter(Ob.status == FAILED),
                        func.count().filter(Ob.status == PUBLISHED),
                        func.min(Ob.created_at).filter(Ob.status == PENDING),
                        func.max(Ob.published_at),
                    )
                    .group_by(Ob.topic)
                    .order_by(Ob.topic)
                )
            ).all()
            errors = dict(
                (
                    await session.execute(
                        select(Ob.topic, Ob.last_error)
                        .where(Ob.last_error.is_not(None), Ob.status != PUBLISHED)
                        .distinct(Ob.topic)
                        .order_by(Ob.topic, Ob.id.desc())
                    )
                ).all()
            )
        return [
            TopicStatus(
                topic=topic,
                pending=int(pending),
                failed=int(failed),
                published=int(published),
                oldest_pending_at=oldest,
                last_published_at=last,
                last_error=errors.get(topic),
            )
            for topic, pending, failed, published, oldest, last in rows
        ]

    async def failed(self, limit: int) -> list[FailedItem]:
        """The events out of attempts, newest first."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Ob.id,
                        Ob.topic,
                        Ob.entity_id,
                        Ob.version,
                        Ob.attempts,
                        Ob.last_error,
                        Ob.created_at,
                    )
                    .where(Ob.status == FAILED)
                    .order_by(Ob.id.desc())
                    .limit(limit)
                )
            ).all()
        return [FailedItem(*row) for row in rows]

    async def retry_failed(self, ids: Sequence[int] | None, now: datetime) -> int:
        """Send failed events again from zero (all of them without ``ids``)."""

        condition = Ob.status == FAILED
        if ids is not None:
            condition = and_(condition, Ob.id.in_(ids))
        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                update(Ob)
                .where(condition)
                .values(status=PENDING, attempts=0, next_attempt_at=now, locked_until=None)
            )
        return int(result.rowcount or 0)

    async def pending_versions(self, topic: str) -> dict[UUID, int]:
        """Highest version already queued (not failed) per record of the topic."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(Ob.entity_id, func.max(Ob.version))
                    .where(Ob.topic == topic, Ob.status == PENDING)
                    .group_by(Ob.entity_id)
                )
            ).all()
        return {entity_id: int(version) for entity_id, version in rows}


__all__ = ["SqlAlchemyOutboxRepository"]
