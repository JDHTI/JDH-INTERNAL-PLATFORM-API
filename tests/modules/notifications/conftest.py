"""In-memory adapters for the notifications tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.notifications.application.service import NotificationService
from app.modules.notifications.domain.entities import FeedItem, PushSubscription, Status

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


class FakeDirectory:
    def __init__(self) -> None:
        self.by_user: dict[uuid.UUID, uuid.UUID] = {}
        self.sites: dict[str, list[uuid.UUID]] = {}
        self.areas: dict[str, list[uuid.UUID]] = {}
        self.everyone: list[uuid.UUID] = []

    def employee(self, user_id=None, site=None, area=None) -> uuid.UUID:
        employee_id = uuid.uuid4()
        self.everyone.append(employee_id)
        if user_id is not None:
            self.by_user[user_id] = employee_id
        if site:
            self.sites.setdefault(site, []).append(employee_id)
        if area:
            self.areas.setdefault(area.lower(), []).append(employee_id)
        return employee_id

    async def employee_id_of(self, principal):
        return self.by_user.get(principal.user_id)

    async def audience(self, kind, values):
        if kind == "TODOS":
            return list(self.everyone)
        if kind == "SEDE":
            return [e for v in values for e in self.sites.get(v, [])]
        if kind == "AREA":
            return [e for v in values for e in self.areas.get(v.lower(), [])]
        if kind == "EMPLEADOS":
            try:
                ids = [uuid.UUID(v) for v in values]
            except ValueError as exc:
                raise InvalidInputError(
                    "audiencia.valores tiene identificadores inválidos"
                ) from exc
            return [e for e in ids if e in self.everyone]
        raise InvalidInputError(kind)

    async def audience_catalog(self):
        site = SimpleNamespace(id=uuid.uuid4(), name="Bodega", employee_count=2)
        return [site], [("BI", 3)]


class FakePush:
    def __init__(self, key: str | None = "public-key") -> None:
        self.key = key
        self.sent: list[tuple[str, dict]] = []
        self.answers: dict[str, int] = {}

    @property
    def public_key(self):
        return self.key

    async def send(self, subscription, payload):
        self.sent.append((subscription.endpoint, payload))
        return self.answers.get(subscription.endpoint)


class FakeRepository:
    def __init__(self) -> None:
        self.notifications: dict[uuid.UUID, object] = {}
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], datetime | None] = {}
        self.received_at: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
        self.subs: dict[str, PushSubscription] = {}
        self.dropped: list[uuid.UUID] = []

    async def find_by_key(self, source, source_id, idempotency_key):
        return next(
            (
                n
                for n in self.notifications.values()
                if (n.source.value, n.source_id, n.idempotency_key)
                == (source, source_id, idempotency_key)
            ),
            None,
        )

    async def create(self, notification):
        if await self.find_by_key(
            notification.source.value, notification.source_id, notification.idempotency_key
        ):
            raise ConflictError("La notificación ya existe")
        stored = replace(notification, created_at=NOW)
        self.notifications[stored.id] = stored
        return stored

    async def add_recipients(self, notification_id, employee_ids):
        added = 0
        for index, employee_id in enumerate(employee_ids):
            key = (notification_id, employee_id)
            if key not in self.rows:
                self.rows[key] = None
                self.received_at[key] = NOW.replace(microsecond=len(self.received_at) + index)
                added += 1
        return added

    async def recipients(self, notification_id):
        return [e for (n, e) in self.rows if n == notification_id]

    async def due(self, now, limit):
        rows = [
            n
            for n in self.notifications.values()
            if n.status is Status.PROGRAMADA and n.scheduled_for <= now
        ]
        return sorted(rows, key=lambda n: n.scheduled_for)[:limit]

    async def claim(self, notification_id, now):
        current = self.notifications[notification_id]
        if current.status is not Status.PROGRAMADA:
            return False
        self.notifications[notification_id] = replace(current, status=Status.ENVIADA, sent_at=now)
        return True

    async def set_status(self, notification_id, status, reason=None):
        current = self.notifications[notification_id]
        self.notifications[notification_id] = replace(current, status=status, failure_reason=reason)

    async def feed(self, employee_id, unread_only, offset, limit):
        mine = [
            (n, read)
            for (n, e), read in self.rows.items()
            if e == employee_id and (not unread_only or read is None)
        ]
        mine.sort(key=lambda pair: self.received_at[(pair[0], employee_id)], reverse=True)
        items = [
            FeedItem(self.notifications[n], read is not None, self.received_at[(n, employee_id)])
            for n, read in mine[offset : offset + limit]
        ]
        unread = sum(1 for (n, e), read in self.rows.items() if e == employee_id and read is None)
        return items, len(mine), unread

    async def mark_read(self, employee_id, notification_id, now):
        for key, read in list(self.rows.items()):
            n, e = key
            if e == employee_id and read is None and notification_id in (None, n):
                self.rows[key] = now

    async def save_subscription(self, employee_id, endpoint, p256dh, auth, expires_at, user_agent):
        current = self.subs.get(endpoint)
        self.subs[endpoint] = PushSubscription(
            current.id if current else uuid.uuid4(), employee_id, endpoint, p256dh, auth
        )

    async def delete_subscription(self, employee_id, endpoint):
        sub = self.subs.get(endpoint)
        if sub and sub.employee_id == employee_id:
            del self.subs[endpoint]

    async def subscriptions(self, employee_ids):
        return [s for s in self.subs.values() if s.employee_id in employee_ids]

    async def drop_subscription(self, subscription_id):
        self.dropped.append(subscription_id)
        for endpoint, sub in list(self.subs.items()):
            if sub.id == subscription_id:
                del self.subs[endpoint]


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.repository = FakeRepository()
        self.push = FakePush()
        self.now = NOW
        self.service = NotificationService(
            self.repository, self.directory, self.push, clock=lambda: self.now
        )


@pytest.fixture
def world() -> World:
    return World()
