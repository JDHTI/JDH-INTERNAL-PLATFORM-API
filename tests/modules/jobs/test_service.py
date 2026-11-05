"""When the jobs run: the DWH poll, the start-up catch-up, the fallback and a person."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.jobs.domain.registry import RunStatus, Trigger
from tests.modules.jobs.conftest import NOW, READY, sequence

IDENTITY_JOBS = {"masters-sync", "commercial-sync-identity"}
ALL_JOBS = IDENTITY_JOBS | {
    "masters-sync-products",
    "commercial-sync-products",
    "commercial-sync-supply",
}


def _jobs(runs) -> set[str]:
    return {run.job for run in runs.items.values()}


async def test_the_first_poll_dispatches_every_mapped_job_once(make_service, runs, watermarks):
    service = make_service()

    queued = await service.poll_dwh()

    assert [runs.items[run_id].job for run_id in queued] == [
        "masters-sync",
        "commercial-sync-identity",
        "masters-sync-products",
        "commercial-sync-products",
        "commercial-sync-supply",
    ]
    assert all(run.trigger is Trigger.DWH for run in runs.items.values())
    assert watermarks.states["identidad"].dispatched_datos_listos == READY
    assert service.status.last_poll_at == NOW and service.status.error is None


async def test_a_restart_with_saved_watermarks_dispatches_nothing(make_service, runs):
    await make_service().poll_dwh()
    for run_id in list(runs.items):
        await runs.finish(run_id, RunStatus.SUCCESS, {}, NOW)

    assert await make_service().poll_dwh() == []


async def test_new_fresh_data_queues_only_that_sequences_jobs(make_service, runs, source):
    await make_service().poll_dwh()
    for run_id in list(runs.items):
        await runs.finish(run_id, RunStatus.SUCCESS, {}, NOW)
    tomorrow = READY + timedelta(days=1)
    source.rows["identidad"] = sequence("identidad", tomorrow)

    queued = await make_service().poll_dwh()

    new = [runs.items[run_id] for run_id in queued]
    assert {run.job for run in new} == IDENTITY_JOBS
    assert all(run.dwh_datos_listos == tomorrow for run in new)
    assert new[0].dwh_corrida_id == source.rows["identidad"].ultima_corrida_id


async def test_reloading_or_failed_sequences_are_saved_but_not_dispatched(
    make_service, runs, source, watermarks
):
    source.rows["identidad"] = sequence("identidad", en_curso=True)
    source.rows["reporte_ventas"] = sequence("reporte_ventas", estado="failed")

    await make_service().poll_dwh()

    assert _jobs(runs) == {"commercial-sync-supply"}
    assert watermarks.states["identidad"].en_curso is True
    assert watermarks.states["identidad"].dispatched_datos_listos is None


async def test_an_open_run_is_skipped_and_the_watermark_still_advances(
    make_service, runs, watermarks
):
    await runs.queue("masters-sync", Trigger.MANUAL, NOW)

    await make_service().poll_dwh()

    assert len(runs.by_job("masters-sync")) == 1
    assert watermarks.states["identidad"].dispatched_datos_listos == READY


async def test_the_warehouse_down_keeps_the_watermarks(make_service, source, watermarks):
    source.down = True
    service = make_service()

    assert await service.poll_dwh() == []
    assert watermarks.states == {} and "query failed" in service.status.error


async def test_start_up_runs_what_is_older_than_the_fresh_data(make_service, runs, watermarks):
    await make_service().poll_dwh()
    for run_id in list(runs.items):
        await runs.mark_running(run_id, READY + timedelta(minutes=5))
        await runs.finish(run_id, RunStatus.SUCCESS, {}, NOW)
    stale = runs.by_job("masters-sync")[0]
    runs.items[stale.id] = replace(stale, started_at=READY - timedelta(hours=1))

    queued = await make_service().catch_up()

    assert [runs.items[i].job for i in queued] == ["masters-sync"]
    assert runs.items[queued[0]].trigger is Trigger.STARTUP


async def test_the_fallback_runs_today_what_the_dwh_did_not(make_service, runs):
    # Saturday in Bogotá: abastecimiento does not run, so its job is not due.
    saturday = datetime(2026, 11, 7, 15, tzinfo=UTC)
    await runs.queue("masters-sync", Trigger.DWH, saturday - timedelta(hours=2))
    done = runs.by_job("masters-sync")[0]
    await runs.mark_running(done.id, saturday - timedelta(hours=2))
    await runs.finish(done.id, RunStatus.SUCCESS, {}, saturday)

    queued = await make_service(clock=lambda: saturday).fallback()

    jobs = {runs.items[i].job for i in queued}
    assert jobs == {"commercial-sync-identity", "masters-sync-products", "commercial-sync-products"}
    assert all(runs.items[i].trigger is Trigger.SCHEDULED for i in queued)


async def test_execute_records_success_partial_and_failure(make_service, runs, recorder):
    service = make_service()
    ok = await runs.queue("masters-sync", Trigger.MANUAL, NOW)
    partial = await runs.queue("masters-sync-products", Trigger.MANUAL, NOW)
    broken = await runs.queue("commercial-sync-supply", Trigger.MANUAL, NOW)
    recorder.results["masters-sync-products"] = {"errors": ["productos: permiso denegado"]}
    recorder.results["commercial-sync-supply"] = RuntimeError("se cayó el DWH")

    assert (await service.execute(ok.id)).status is RunStatus.SUCCESS
    assert (await service.execute(partial.id)).status is RunStatus.PARTIAL
    failed = await service.execute(broken.id)

    assert failed.status is RunStatus.FAILED and failed.error == "se cayó el DWH"
    assert runs.items[ok.id].started_at == NOW and runs.items[ok.id].detail == {"ok": True}
    assert recorder.calls == ["masters-sync", "masters-sync-products", "commercial-sync-supply"]


async def test_execute_waits_while_the_sequence_reloads_then_gives_up(
    make_service, runs, source, recorder
):
    source.rows["identidad"] = sequence("identidad", en_curso=True)
    sleeps: list[float] = []
    run = await runs.queue("masters-sync", Trigger.MANUAL, NOW)

    result = await make_service(sleeps=sleeps).execute(run.id)

    assert result.status is RunStatus.FAILED and "sigue en curso" in result.error
    assert sleeps == [60.0, 60.0] and recorder.calls == []


async def test_a_person_queues_a_run(make_service, runs, requested):
    service = make_service()

    run = await service.request_run("masters-sync", "ana@jdh.com.co")

    assert requested == [run.id] and run.triggered_by == "ana@jdh.com.co"
    with pytest.raises(ConflictError, match="en cola o en curso"):
        await service.request_run("masters-sync", "ana@jdh.com.co")
    with pytest.raises(NotFoundError):
        await service.request_run("nada", None)
    with pytest.raises(ConflictError, match="JOBS_ENABLED"):
        await make_service(enabled=False).request_run("masters-sync-products", None)


async def test_recover_overview_and_history(make_service, runs):
    service = make_service()
    await service.poll_dwh()

    assert await service.recover_stale_runs() == 5
    settings, status, items = await service.overview()
    history = await service.history("masters-sync", 10)

    assert settings.enabled and status.last_poll_at == NOW
    identity = next(item for item in items if item.key == "masters-sync")
    assert identity.last_run.status is RunStatus.FAILED and identity.last_success_at is None
    assert identity.sequence_state.ultima_datos_listos == READY
    assert len(history) == 1
    with pytest.raises(NotFoundError):
        await service.history("nada", 10)
    with pytest.raises(InvalidInputError):
        await service.history(None, 0)
