"""In-memory adapters for the jobs module."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest

from app.core.errors import ConflictError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.jobs.application.service import JobService, JobsSettings, PollStatus
from app.modules.jobs.domain.entities import JobRun, SequenceState
from app.modules.jobs.domain.registry import DONE_STATUSES, JOBS, OPEN_STATUSES, RunStatus

# Thursday 2026-11-05, 10:00 in Bogotá.
NOW = datetime(2026, 11, 5, 15, tzinfo=UTC)
READY = datetime(2026, 11, 5, 8, 42, tzinfo=UTC)


class FakeRuns:
    def __init__(self) -> None:
        self.items: dict = {}

    async def queue(
        self, job, trigger, now, *, triggered_by=None, corrida_id=None, datos_listos=None
    ):
        if any(r.job == job and r.status in OPEN_STATUSES for r in self.items.values()):
            raise ConflictError("open")
        run = JobRun(
            uuid4(),
            job,
            trigger,
            RunStatus.QUEUED,
            now,
            dwh_corrida_id=corrida_id,
            dwh_datos_listos=datos_listos,
            triggered_by=triggered_by,
        )
        self.items[run.id] = run
        return run

    async def mark_running(self, run_id, now):
        self.items[run_id] = replace(self.items[run_id], status=RunStatus.RUNNING, started_at=now)
        return self.items[run_id]

    async def finish(self, run_id, status, detail, now, error=None):
        self.items[run_id] = replace(
            self.items[run_id], status=status, detail=dict(detail), finished_at=now, error=error
        )
        return self.items[run_id]

    async def get(self, run_id):
        return self.items.get(run_id)

    def _sorted(self, job=None):
        runs = [r for r in self.items.values() if job is None or r.job == job]
        return sorted(runs, key=lambda r: r.queued_at, reverse=True)

    async def latest(self, job):
        runs = self._sorted(job)
        return runs[0] if runs else None

    async def last_success(self, job):
        runs = [r for r in self._sorted(job) if r.status in DONE_STATUSES]
        return runs[0] if runs else None

    async def history(self, job, limit):
        return self._sorted(job)[:limit]

    async def fail_open(self, error, now):
        open_ids = [i for i, r in self.items.items() if r.status in OPEN_STATUSES]
        for run_id in open_ids:
            self.items[run_id] = replace(
                self.items[run_id], status=RunStatus.FAILED, error=error, finished_at=now
            )
        return len(open_ids)

    def by_job(self, job):
        return [r for r in self.items.values() if r.job == job]


class FakeWatermarks:
    def __init__(self) -> None:
        self.states: dict[str, SequenceState] = {}

    async def all(self):
        return dict(self.states)

    async def save(self, states):
        for state in states:
            self.states[state.secuencia] = state


class FakeSequences:
    def __init__(self, *states: SequenceState) -> None:
        self.rows = {state.secuencia: state for state in states}
        self.down = False
        self.calls = 0

    async def states(self, names):
        self.calls += 1
        if self.down:
            raise DataProviderUnavailable("Data provider query failed")
        return [self.rows[name] for name in names if name in self.rows]


def sequence(name: str, ready: datetime | None = READY, **fields) -> SequenceState:
    return SequenceState(
        secuencia=name,
        ultima_corrida_id=uuid4(),
        ultima_estado=fields.pop("estado", "success"),
        ultima_datos_listos=ready,
        en_curso=fields.pop("en_curso", False),
        **fields,
    )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results: dict = {}

    def handler(self, key: str):
        async def run():
            self.calls.append(key)
            result = self.results.get(key, {"ok": True})
            if isinstance(result, Exception):
                raise result
            return result

        return run


@pytest.fixture
def runs() -> FakeRuns:
    return FakeRuns()


@pytest.fixture
def watermarks() -> FakeWatermarks:
    return FakeWatermarks()


@pytest.fixture
def source() -> FakeSequences:
    # Alphabetical, as the warehouse returns them.
    return FakeSequences(
        sequence("abastecimiento"),
        sequence("identidad"),
        sequence("reporte_ventas"),
    )


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def requested() -> list:
    return []


@pytest.fixture
def make_service(runs, watermarks, source, recorder, requested):
    def build(enabled: bool = True, clock=lambda: NOW, sleeps: list | None = None):
        async def sleep(seconds):
            if sleeps is not None:
                sleeps.append(seconds)

        return JobService(
            runs=runs,
            watermarks=watermarks,
            sequences=source,
            handlers={job.key: recorder.handler(job.key) for job in JOBS},
            status=PollStatus(),
            settings=JobsSettings(enabled, 60, time(10), "America/Bogota"),
            request_run=requested.append,
            wait_for_dwh_seconds=120,
            clock=clock,
            sleep=sleep,
        )

    return build
