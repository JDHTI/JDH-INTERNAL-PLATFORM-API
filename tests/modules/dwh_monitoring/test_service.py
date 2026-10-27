"""Read use cases of the monitoring screens."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.dwh_monitoring.application.service import DwhMonitoringService
from app.modules.dwh_monitoring.domain.entities import CycleStatus, LoadCycle
from tests.modules.dwh_monitoring.conftest import NOW, OUTSIDER, TI_MEMBER, FakeAccess, person


def card(name: str, **values) -> dict:
    base = {
        "secuencia": name,
        "descripcion": None,
        "estado_programacion": "Activa",
        "calendario": "*-*-* 03:00",
        "hora_inicio": time(3, 0),
        "tope_s": 7200,
        "tablas_fase1": 10,
        "tablas_fase2": 2,
        "transformacion": f"selector:{name}",
        "ultima_corrida_id": None,
        "ultima_estado": "success",
        "ultima_inicio": None,
        "ultima_datos_listos": None,
        "ultima_fin": None,
        "ultima_duracion_s": None,
        "en_curso": False,
        "eta_datos_listos_en_curso": None,
        "eta_fin_en_curso": None,
        "proxima_ejecucion": None,
        "eta_datos_listos_proxima": None,
        "eta_fin_proxima": None,
        "hora_tipica_datos_listos": time(4, 10),
        "extraccion_mediana_min": None,
        "transformacion_mediana_min": None,
        "datos_listos_mediana_min": None,
        "datos_listos_p90_min": None,
        "duracion_mediana_min": None,
        "pct_exito": None,
        "corridas_medidas": 12,
        "systemd_proximo": None,
        "systemd_ultimo": None,
        "sincronizado_en": None,
        "cargado_en": NOW,
    }
    return {**base, **values}


class FakeReader:
    """The local copy as plain lists."""

    def __init__(self) -> None:
        self.cards: list[dict] = []
        self.tables: list[dict] = []
        self.typical: list[dict] = []
        self.stats: list[dict] = []
        self.kpis: list[dict] = []
        self.domains: list[dict] = []
        self.freshness: list[dict] = []
        self.run_rows: list[dict] = []
        self.extraction_rows: list[dict] = []
        self.transformation_rows: list[dict] = []
        self.quality_rows: list[dict] = []
        self.cycle: LoadCycle | None = None
        self.filters = None

    async def sequences(self):
        return self.cards

    async def sequence(self, name):
        return next((row for row in self.cards if row["secuencia"] == name), None)

    async def sequence_tables(self, secuencia, dominio, fase):
        return [
            row
            for row in self.tables
            if (secuencia is None or row["secuencia"] == secuencia)
            and (dominio is None or row["dominio"] == dominio)
            and (fase is None or row["fase"] == fase)
        ]

    async def typical_schedule(self, secuencia=None):
        return [row for row in self.typical if secuencia in (None, row["secuencia"])]

    async def statistics(self, secuencia=None):
        return [row for row in self.stats if secuencia in (None, row["secuencia"])]

    async def freshness_latest(self):
        return max(self.kpis, key=lambda row: row["calculado_en"], default=None)

    async def freshness_history(self, start, end):
        return [row for row in self.kpis if start <= row["calculado_en"] < end]

    async def freshness_domains(self):
        return self.domains

    async def freshness_tables(self, estados, dominio, programacion, query_text):
        return [row for row in self.freshness if not estados or row["estado_frescura"] in estados]

    async def runs(self, filters, page, size):
        self.filters = filters
        return self.run_rows, len(self.run_rows)

    async def runs_between(self, start, end):
        return [row for row in self.run_rows if start <= row["inicio"] < end]

    async def extractions(self, filters, page, size):
        self.filters = filters
        return self.extraction_rows, len(self.extraction_rows)

    async def transformations(self, filters, page, size):
        self.filters = filters
        return self.transformation_rows, len(self.transformation_rows)

    async def quality(self, estados, tipo):
        return self.quality_rows

    async def last_cycle(self):
        return self.cycle

    async def last_successful_load(self):
        return self.cycle.started_at if self.cycle else None


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


def make_service(reader, **options) -> DwhMonitoringService:
    return DwhMonitoringService(reader, FakeAccess(), clock=lambda: NOW, **options)


async def test_only_ti_members_and_admins_read_the_module(reader) -> None:
    service = make_service(reader)

    assert await service.sequences(TI_MEMBER) == []
    assert await service.sequences(person("jefe@jdh.com.co", admin=True)) == []
    with pytest.raises(ForbiddenError):
        await service.sequences(OUTSIDER)


async def test_the_overview_shows_what_is_ready_today_and_the_eta_of_the_rest(reader) -> None:
    today_ready = datetime(2026, 10, 22, 9, 5, tzinfo=UTC)  # 04:05 in Bogota, today
    yesterday = datetime(2026, 10, 21, 9, 5, tzinfo=UTC)
    eta = datetime(2026, 10, 22, 17, 30, tzinfo=UTC)
    reader.cards = [
        card("reporte_ventas", ultima_datos_listos=today_ready),
        card("abastecimiento", ultima_datos_listos=yesterday, eta_datos_listos_proxima=eta),
        card("costeo", estado_programacion="Inactiva"),
        card("contabilidad", en_curso=True, eta_datos_listos_en_curso=eta),
    ]
    reader.kpis = [{"calculado_en": NOW, "pct_fresca_programadas": 97.5}]

    result = await make_service(reader).overview(TI_MEMBER)

    assert result["secuencias_activas"] == 3 and result["secuencias_en_curso"] == 1
    ready = {row["secuencia"]: row for row in result["datos_listos_hoy"]}
    assert set(ready) == {"reporte_ventas", "abastecimiento", "contabilidad"}
    assert ready["reporte_ventas"]["datos_listos"] == today_ready
    assert ready["abastecimiento"]["datos_listos"] is None
    assert ready["abastecimiento"]["eta_datos_listos"] == eta
    assert ready["contabilidad"]["eta_datos_listos"] == eta
    assert result["frescura"]["pct_fresca_programadas"] == 97.5
    assert result["carga"]["habilitada"] is False


async def test_a_sequence_comes_with_its_tables_schedule_and_statistics(reader) -> None:
    reader.cards = [card("reporte_ventas")]
    reader.tables = [
        {"secuencia": "reporte_ventas", "dominio": "ventas", "fase": 1},
        {"secuencia": "abastecimiento", "dominio": "compras", "fase": 1},
    ]
    reader.stats = [{"secuencia": "reporte_ventas", "corridas_medidas": 12}]

    detail = await make_service(reader).sequence("reporte_ventas", TI_MEMBER)

    assert [row["dominio"] for row in detail["tablas"]] == ["ventas"]
    assert detail["estadisticas"]["corridas_medidas"] == 12
    with pytest.raises(NotFoundError):
        await make_service(reader).sequence("nope", TI_MEMBER)


async def test_the_audit_is_always_filtered_by_business_days_in_bogota(reader) -> None:
    service = make_service(reader)

    await service.extractions(TI_MEMBER)
    # Last 7 days including today, from Bogota midnight (05:00 UTC).
    assert reader.filters.start == datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
    assert reader.filters.end == datetime(2026, 10, 23, 5, 0, tzinfo=UTC)

    await service.extractions(
        TI_MEMBER, date(2026, 10, 1), date(2026, 10, 1), dominio="ventas", estados=["failed"]
    )
    assert reader.filters.end - reader.filters.start == timedelta(days=1)
    assert reader.filters.dominio == "ventas" and reader.filters.estados == ["failed"]
    with pytest.raises(InvalidInputError):
        await service.extractions(TI_MEMBER, date(2026, 10, 2), date(2026, 10, 1))
    with pytest.raises(InvalidInputError):
        await service.extractions(TI_MEMBER, size=501)


async def test_the_run_history_has_no_date_limit_unless_asked(reader) -> None:
    await make_service(reader).runs(TI_MEMBER, secuencia="reporte_ventas")

    assert reader.filters.start is None and reader.filters.end is None
    assert reader.filters.secuencia == "reporte_ventas"


async def test_the_schedule_compares_the_typical_day_with_the_runs_of_that_day(reader) -> None:
    reader.typical = [{"secuencia": "reporte_ventas", "hora_programada": time(3, 0)}]
    reader.run_rows = [
        {"corrida_id": uuid4(), "inicio": datetime(2026, 10, 22, 8, 0, tzinfo=UTC)},  # today
        {"corrida_id": uuid4(), "inicio": datetime(2026, 10, 22, 4, 0, tzinfo=UTC)},  # yesterday
    ]

    result = await make_service(reader).schedule(TI_MEMBER)

    assert result["fecha"] == date(2026, 10, 22)
    assert len(result["tipico"]) == 1 and len(result["corridas"]) == 1


async def test_a_load_now_wakes_the_worker_or_says_it_is_off(reader) -> None:
    calls = []
    on = make_service(reader, sync_enabled=True, request_sync=lambda: calls.append(1))

    await on.request_sync(TI_MEMBER)

    assert calls == [1]
    with pytest.raises(ConflictError):
        await make_service(reader).request_sync(TI_MEMBER)
    with pytest.raises(ForbiddenError):
        await on.request_sync(OUTSIDER)


async def test_the_load_status_reports_the_last_cycle(reader) -> None:
    reader.cycle = LoadCycle(id=uuid4(), started_at=NOW, status=CycleStatus.PARTIAL)

    status = await make_service(reader, sync_enabled=True, sync_interval=300).sync_status(TI_MEMBER)

    assert status["habilitada"] is True and status["intervalo_s"] == 300
    assert status["ultima"].status is CycleStatus.PARTIAL
    assert status["ultima_exitosa"] == NOW
