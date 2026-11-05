"""The background loop of the jobs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.modules.jobs.infrastructure.worker import JobsWorker, RunRequest

NOW = datetime(2026, 11, 5, 15, tzinfo=UTC)


class FakeJobService:
    def __init__(self, poll_ids=(), catch_up_ids=(), fallback_ids=()) -> None:
        self.calls: list[str] = []
        self.executed: list = []
        self.poll_ids = list(poll_ids)
        self.catch_up_ids = list(catch_up_ids)
        self.fallback_ids = list(fallback_ids)

    async def recover_stale_runs(self):
        self.calls.append("recover")
        return 1

    async def poll_dwh(self):
        self.calls.append("poll")
        ids, self.poll_ids = self.poll_ids, []
        return ids

    async def catch_up(self):
        self.calls.append("catch_up")
        return self.catch_up_ids

    async def fallback(self):
        self.calls.append("fallback")
        return self.fallback_ids

    async def execute(self, run_id):
        self.executed.append(run_id)
        if run_id == "boom":
            raise RuntimeError("boom")


def worker(service, trigger, poll=3600.0, at=time(10), clock=lambda: NOW) -> JobsWorker:
    return JobsWorker(lambda: service, trigger, poll, at, ZoneInfo("America/Bogota"), clock=clock)


async def settle() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


async def test_requests_wait_for_their_run() -> None:
    trigger = RunRequest()
    run_id = uuid4()
    trigger.set(run_id)

    assert await trigger.wait(10) == run_id
    assert await trigger.wait(0.01) is None


async def test_start_up_recovers_polls_catches_up_and_runs_what_was_queued() -> None:
    first, second = uuid4(), uuid4()
    service = FakeJobService(poll_ids=[first], catch_up_ids=[second])
    loop = worker(service, RunRequest())
    loop.start()
    await settle()
    await loop.stop()

    assert service.calls == ["recover", "poll", "catch_up"]
    assert service.executed == [first, second]


async def test_a_failing_run_does_not_stop_the_loop() -> None:
    service = FakeJobService()
    trigger = RunRequest()
    loop = worker(service, trigger)
    loop.start()
    await settle()
    trigger.set("boom")
    await settle()
    later = uuid4()
    trigger.set(later)
    await settle()
    await loop.stop()

    assert service.executed == ["boom", later]


async def test_the_poll_repeats_every_interval() -> None:
    service = FakeJobService()
    loop = worker(service, RunRequest(), poll=0.01, clock=lambda: datetime.now(UTC))
    loop.start()
    await asyncio.sleep(0.1)
    await loop.stop()

    assert service.calls[:3] == ["recover", "poll", "catch_up"]
    assert service.calls.count("poll") >= 3


async def test_the_fallback_fires_at_its_local_time() -> None:
    # One millisecond before 10:00 in Bogotá.
    almost = datetime(2026, 11, 5, 14, 59, 59, 999000, tzinfo=UTC)
    queued = uuid4()
    service = FakeJobService(fallback_ids=[queued])
    loop = worker(service, RunRequest(), clock=lambda: almost)
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()

    assert "fallback" in service.calls and service.executed == [queued]
