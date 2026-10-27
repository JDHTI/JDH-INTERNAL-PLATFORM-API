"""Load cycles: snapshots, the freshness history and the incremental run history."""

from datetime import timedelta
from uuid import uuid4

from app.modules.dwh_monitoring.application.sync import MonitoringSyncService, to_local
from app.modules.dwh_monitoring.domain.entities import CycleStatus
from app.modules.dwh_monitoring.domain.views import (
    AUDITORIA_EXTRACCIONES,
    FRESCURA_TABLAS,
    SECUENCIAS,
    VIEWS,
)
from tests.modules.dwh_monitoring.conftest import NOW


def service(source, copy, now=NOW) -> MonitoringSyncService:
    return MonitoringSyncService(source, copy, batch_size=2, clock=lambda: now)


def extraction(inicio, estado="success", **values):
    fin = None if estado == "running" else inicio + timedelta(minutes=3)
    return {"run_id": uuid4(), "inicio": inicio, "fin": fin, "estado": estado, **values}


def test_every_view_of_the_contract_has_a_key_among_its_columns() -> None:
    assert len(VIEWS) == 11
    for view in VIEWS:
        assert set(view.key) <= set(view.source_columns), view.name


def test_intervals_become_seconds_and_json_text_is_parsed() -> None:
    row = to_local(
        FRESCURA_TABLAS,
        {"pipeline_name": "extract_ventas_f461", "edad": timedelta(hours=2), "max_staleness": None},
    )
    assert row["edad_s"] == 7200 and row["max_staleness_s"] is None
    assert "edad" not in row

    audit = to_local(AUDITORIA_EXTRACCIONES, {"ventana_desde": '{"ts": "2026-10-21"}'})
    assert audit["ventana_desde"] == {"ts": "2026-10-21"}


async def test_snapshots_replace_the_copy_and_the_kpi_is_appended(source, copy) -> None:
    source.views["secuencias"] = [{"secuencia": "reporte_ventas", "tope": timedelta(hours=2)}]
    source.views["frescura_resumen"] = [{"calculado_en": NOW, "pct_fresca_programadas": 97.5}]

    cycle = await service(source, copy).run()

    assert cycle.status is CycleStatus.SUCCESS
    assert copy.tables["secuencias"][("reporte_ventas",)]["tope_s"] == 7200
    source.views["secuencias"] = [{"secuencia": "abastecimiento"}]
    source.views["frescura_resumen"] = [{"calculado_en": NOW + timedelta(minutes=10)}]
    await service(source, copy, NOW + timedelta(minutes=10)).run()

    assert list(copy.tables["secuencias"]) == [("abastecimiento",)]
    assert len(copy.tables["frescura_resumen"]) == 2  # the KPI keeps its history


async def test_an_empty_answer_never_wipes_a_snapshot(source, copy) -> None:
    source.views["secuencias"] = [{"secuencia": "reporte_ventas"}]
    await service(source, copy).run()
    source.views["secuencias"] = []

    cycle = await service(source, copy, NOW + timedelta(minutes=10)).run()

    assert cycle.views["secuencias"].read == 0
    assert list(copy.tables["secuencias"]) == [("reporte_ventas",)]


async def test_the_first_load_takes_the_backfill_and_then_reads_from_the_newest_run(
    source, copy
) -> None:
    rows = [extraction(NOW - timedelta(days=days)) for days in (200, 30, 3, 1)]
    source.views["auditoria_extracciones"] = rows

    first = await service(source, copy).run(["auditoria_extracciones"])

    assert source.since["auditoria_extracciones"] == NOW - timedelta(days=90)
    assert first.views["auditoria_extracciones"].read == 3  # paged by 2: 2 + 1
    await service(source, copy, NOW + timedelta(minutes=10)).run(["auditoria_extracciones"])
    assert source.since["auditoria_extracciones"] == rows[-1]["inicio"] - timedelta(days=2)


async def test_a_running_row_is_updated_even_after_it_left_the_window(source, copy) -> None:
    running = extraction(NOW - timedelta(days=5), estado="running")
    newer = extraction(NOW - timedelta(hours=1))
    source.views["auditoria_extracciones"] = [running, newer]
    await service(source, copy).run(["auditoria_extracciones"])
    # The newest run is an hour old, so the next read starts two days before it and
    # no longer reaches the run that was still going.
    finished = {**running, "estado": "success", "fin": NOW}
    source.views["auditoria_extracciones"] = [finished, newer]

    later = NOW + timedelta(minutes=10)
    await service(source, copy, later).run(["auditoria_extracciones"])

    assert source.since["auditoria_extracciones"] > running["inicio"]

    table = copy.tables["auditoria_extracciones"]
    assert table[(running["run_id"],)]["estado"] == "success"
    assert source.keys_read["auditoria_extracciones"] == [running["run_id"]]


async def test_a_warehouse_outage_fails_the_cycle_and_touches_nothing(source, copy) -> None:
    source.down = True

    cycle = await service(source, copy).run()

    assert cycle.status is CycleStatus.FAILED and "Sin conexión" in cycle.error
    assert cycle.views == {} and copy.tables == {}
    assert (await copy.last_cycle()).id == cycle.id


async def test_one_failing_view_leaves_the_cycle_partial(source, copy) -> None:
    source.views["secuencias"] = [{"secuencia": "reporte_ventas"}]
    source.failing.add(FRESCURA_TABLAS.name)

    cycle = await service(source, copy).run()

    assert cycle.status is CycleStatus.PARTIAL
    assert "frescura_tablas" in cycle.error
    assert cycle.views[SECUENCIAS.name].written == 1
    assert cycle.views[FRESCURA_TABLAS.name].error
