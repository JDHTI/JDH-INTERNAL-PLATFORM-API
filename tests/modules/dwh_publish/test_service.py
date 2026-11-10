"""The publisher's rules over in-memory ports: batches, retries, failures, breaker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.dwh_publish.application.service import (
    DwhPublishService,
    PublishSettings,
    PublishState,
    backoff,
)
from app.modules.dwh_publish.domain.entities import OutboxItem
from app.modules.dwh_publish.domain.ports import DwhRejected, DwhUnavailable

NOW = datetime(2026, 11, 10, 15, tzinfo=UTC)


class FakeOutbox:
    def __init__(self, items):
        self.items = {item.id: item for item in items}
        self.published, self.failed_ids, self.retries = [], [], []

    async def claim(self, limit, lease_seconds, now):
        pending = [
            item
            for item in self.items.values()
            if item.id not in self.published and item.id not in self.failed_ids
        ]
        return pending[:limit]

    async def mark_published(self, ids, now):
        self.published.extend(ids)

    async def mark_retry(self, ids, error, next_attempt_at, *, charge):
        self.retries.append((list(ids), charge, next_attempt_at))
        for item_id in ids:
            self.items.pop(item_id)

    async def mark_failed(self, ids, error):
        self.failed_ids.extend(ids)


class FakeWriter:
    def __init__(self, error=None):
        self.error = error

    @asynccontextmanager
    async def transaction(self):
        if isinstance(self.error, DwhUnavailable):
            raise self.error
        yield self


class FakeTopic:
    name = "supplier_support"

    def __init__(self, bad=()):
        self.bad = set(bad)
        self.written = []

    async def write(self, connection, events):
        if any(event.entity_id in self.bad for event in events):
            raise DwhRejected("check constraint")
        self.written.extend(events)


def item(number, attempts=0, entity=None):
    return OutboxItem(number, "supplier_support", entity or uuid4(), 1, {}, attempts)


def service(outbox, writer, topic, state=None, **settings):
    return DwhPublishService(
        repository=outbox,
        writer=writer,
        topics={topic.name: topic},
        settings=PublishSettings(enabled=True, **settings),
        state=state or PublishState(),
        configured=True,
        clock=lambda: NOW,
        rand=lambda: 1.0,
    )


async def test_a_bad_event_is_tried_alone_and_the_rest_lands() -> None:
    bad = uuid4()
    outbox = FakeOutbox([item(1), item(2, entity=bad), item(3)])
    topic = FakeTopic(bad=[bad])

    result = await service(outbox, FakeWriter(), topic).publish_once()

    assert (result.published, result.retried, result.failed) == (2, 1, 0)
    assert sorted(outbox.published) == [1, 3]
    ids, charged, when = outbox.retries[0]
    assert ids == [2] and charged and when == NOW + timedelta(seconds=30)


async def test_out_of_attempts_goes_aside() -> None:
    bad = uuid4()
    outbox = FakeOutbox([item(1, attempts=11, entity=bad)])

    result = await service(
        outbox, FakeWriter(), FakeTopic(bad=[bad]), max_attempts=12
    ).publish_once()

    assert result.failed == 1 and outbox.failed_ids == [1]


async def test_an_unreachable_warehouse_charges_nothing_and_pauses() -> None:
    state = PublishState()
    for _ in range(3):
        outbox = FakeOutbox([item(1)])
        result = await service(
            outbox, FakeWriter(DwhUnavailable("timeout")), FakeTopic(), state=state
        ).publish_once()
        assert result.unavailable and outbox.retries[0][1] is False

    assert state.paused_until == NOW + timedelta(seconds=300)
    healthy = service(FakeOutbox([item(9)]), FakeWriter(), FakeTopic(), state=state)
    assert healthy.paused()


@pytest.mark.parametrize(("attempts", "seconds"), [(0, 30), (1, 60), (5, 960), (20, 3600)])
def test_backoff_doubles_up_to_the_cap(attempts, seconds) -> None:
    assert backoff(attempts, 30, 3600, lambda: 1.0) == seconds
    assert backoff(attempts, 30, 3600, lambda: 0.0) == seconds / 2
