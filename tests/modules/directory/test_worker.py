"""The background loop of the directory sync."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import ConflictError
from app.modules.directory.domain.entities import RunStatus, RunTrigger, SyncRun
from app.modules.directory.infrastructure.worker import DirectorySyncWorker, SyncRequest

NOW = datetime(2026, 11, 4, 15, tzinfo=UTC)


class FakeSyncService:
    def __init__(self, catch_up=False, busy=False) -> None:
        self.catch_up = catch_up
        self.busy = busy
        self.calls: list[str] = []
        self.executed: list = []

    async def recover_stale_runs(self):
        self.calls.append("recover")
        return 0

    async def needs_catch_up(self):
        return self.catch_up

    async def start(self, trigger, triggered_by):
        if self.busy:
            raise ConflictError("busy")
        self.calls.append(f"start:{trigger.value}")
        return SyncRun(uuid4(), trigger, RunStatus.RUNNING, NOW)

    async def execute(self, run_id):
        self.executed.append(run_id)
        if run_id == "boom":
            raise RuntimeError("boom")
        return SyncRun(run_id, RunTrigger.MANUAL, RunStatus.SUCCESS, NOW)


def worker(service, trigger, at=time(23, 59), clock=lambda: NOW) -> DirectorySyncWorker:
    return DirectorySyncWorker(
        lambda: service, trigger, at=at, zone=ZoneInfo("America/Bogota"), clock=clock
    )


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_requests_wait_for_their_run() -> None:
    trigger = SyncRequest()
    run_id = uuid4()
    trigger.set(run_id)

    assert await trigger.wait(10) == run_id
    assert await trigger.wait(0.01) is None


@pytest.mark.asyncio
async def test_start_up_recovers_and_catches_up_once() -> None:
    service = FakeSyncService(catch_up=True)
    loop = worker(service, SyncRequest())
    loop.start()
    await settle()
    await loop.stop()

    assert service.calls == ["recover", "start:scheduled"]
    assert len(service.executed) == 1


@pytest.mark.asyncio
async def test_a_requested_run_is_executed_and_errors_do_not_stop_the_loop() -> None:
    service = FakeSyncService()
    trigger = SyncRequest()
    loop = worker(service, trigger)
    loop.start()
    await settle()

    trigger.set("boom")
    await settle()
    second = uuid4()
    trigger.set(second)
    await settle()
    await loop.stop()

    assert service.calls == ["recover"]
    assert service.executed == ["boom", second]


@pytest.mark.asyncio
async def test_the_scheduled_time_starts_a_run_and_a_busy_one_is_skipped() -> None:
    # The clock reads one millisecond before 23:59 in Bogotá.
    almost = datetime(2026, 11, 5, 4, 58, 59, 999000, tzinfo=UTC)
    service = FakeSyncService()
    loop = worker(service, SyncRequest(), clock=lambda: almost)
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert service.calls == ["recover", "start:scheduled"]

    busy = FakeSyncService(busy=True)
    loop = worker(busy, SyncRequest(), clock=lambda: almost)
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert busy.calls == ["recover"] and busy.executed == []
