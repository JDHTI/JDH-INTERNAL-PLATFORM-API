"""HTTP contract of the data warehouse monitoring routes."""

from datetime import UTC, datetime, time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.dwh_monitoring.application.service import DwhMonitoringService
from app.modules.dwh_monitoring.domain.entities import CycleStatus, LoadCycle, ViewReport
from app.modules.identity.presentation.principal import get_current_principal
from fastapi.testclient import TestClient

from tests.modules.dwh_monitoring.conftest import NOW, OUTSIDER, TI_MEMBER, FakeAccess
from tests.modules.dwh_monitoring.test_service import FakeReader, card

BASE = "/api/v1/dwh-monitoring"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


def extraction(**values) -> dict:
    base = {
        "run_id": uuid4(),
        "inicio": NOW,
        "fin": None,
        "duracion_s": 30,
        "estado": "running",
        "disparo": "scheduled",
        "secuencia": "reporte_ventas",
        "corrida_id": uuid4(),
        "dominio": "ventas",
        "tabla": "f461",
        "pipeline_name": "extract_ventas_f461",
        "estrategia": "incremental",
        "filas_extraidas": 100,
        "filas_cargadas": 100,
        "filas_origen": 100,
        "filas_esperadas": None,
        "conciliacion": "Cuadra",
        "paginas": 1,
        "ventana_desde": {"ts": "2026-10-21T00:00:00"},
        "ventana_hasta": None,
        "batch_id": uuid4(),
        "error": None,
        "cancelada_por": None,
        "os_hostname": "dwh01",
        "os_pid": 4242,
        "cargado_en": NOW,
    }
    return {**base, **values}


@pytest.fixture
def reader() -> FakeReader:
    reader = FakeReader()
    reader.cards = [card("reporte_ventas", pct_exito=95, extraccion_mediana_min=12.5)]
    reader.extraction_rows = [extraction()]
    reader.cycle = LoadCycle(
        id=uuid4(),
        started_at=NOW,
        finished_at=NOW,
        status=CycleStatus.SUCCESS,
        views={"secuencias": ViewReport(read=1, written=1)},
    )
    return reader


def make_client(reader, principal):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: principal
    service = DwhMonitoringService(reader, FakeAccess(), clock=lambda: NOW)
    container = app.state.container
    return app, container, service


@pytest.fixture
def client(reader):
    app, container, service = make_client(reader, TI_MEMBER)
    with (
        container.redis.override(AllowAll()),
        container.v1_container.dwh_monitoring_service.override(service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_sequences_keep_the_contract_names_with_times_in_bogota(client) -> None:
    response = client.get(f"{BASE}/sequences")

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["secuencia"] == "reporte_ventas" and item["tope_s"] == 7200
    assert item["hora_inicio"] == "03:00:00" and item["hora_tipica_datos_listos"] == "04:10:00"
    assert item["pct_exito"] == 95 and item["extraccion_mediana_min"] == 12.5
    assert item["corridas_medidas"] == 12


def test_the_extraction_audit_is_paged_and_validates_its_filters(client) -> None:
    response = client.get(
        f"{BASE}/extractions",
        params={"desde": "2026-10-20", "estado": ["running", "failed"], "size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and body["page"] == 1 and body["size"] == 10
    item = body["items"][0]
    assert item["ventana_desde"] == {"ts": "2026-10-21T00:00:00"} and item["fin"] is None
    assert client.get(f"{BASE}/extractions", params={"estado": "x"}).status_code == 422
    assert client.get(f"{BASE}/extractions", params={"size": 501}).status_code == 422
    bad_range = client.get(
        f"{BASE}/extractions", params={"desde": "2026-10-22", "hasta": "2026-10-01"}
    )
    assert bad_range.status_code == 422


def test_overview_schedule_and_load_status_answer(client, reader) -> None:
    reader.kpis = [
        {"calculado_en": NOW, "pct_fresca_programadas": 97.5}
        | dict.fromkeys(
            [
                "tablas_habilitadas",
                "tablas_deshabilitadas",
                "tablas_programadas",
                "frescas_programadas",
                "frescas_total",
                "pct_fresca_total",
                "atrasadas",
                "nunca_extraidas",
                "ultima_extraccion_ok",
            ]
        )
    ]
    reader.typical = [
        {
            "secuencia": "reporte_ventas",
            "calendario": "*-*-* 03:00",
            "dias_semana": [1, 2, 3, 4, 5],
            "hora_programada": time(3, 0),
            "hora_inicio_real": time(3, 1),
            "hora_fin_extraccion": time(3, 40),
            "hora_datos_listos": time(4, 10),
            "hora_datos_listos_p90": time(4, 30),
            "hora_fin": time(4, 50),
            "corridas_medidas": 12,
        }
    ]

    overview = client.get(f"{BASE}/overview").json()
    assert overview["frescura"]["pct_fresca_programadas"] == 97.5
    assert overview["carga"]["ultima"]["detalle"]["secuencias"]["escritas"] == 1
    assert overview["zona_horaria"] == "America/Bogota"

    schedule = client.get(f"{BASE}/schedule", params={"fecha": "2026-10-22"}).json()
    assert schedule["tipico"][0]["hora_datos_listos"] == "04:10:00"
    assert schedule["secuencias"][0]["secuencia"] == "reporte_ventas"

    status = client.get(f"{BASE}/sync").json()
    assert status["habilitada"] is False and status["ultima"]["estado"] == "success"
    assert client.post(f"{BASE}/sync").status_code == 409


def test_a_missing_sequence_is_404(client) -> None:
    assert client.get(f"{BASE}/sequences/nope").status_code == 404


def test_people_outside_ti_get_403(reader) -> None:
    app, container, service = make_client(reader, OUTSIDER)
    with (
        container.redis.override(AllowAll()),
        container.v1_container.dwh_monitoring_service.override(service),
        TestClient(app) as client,
    ):
        assert client.get(f"{BASE}/sequences").status_code == 403
        assert client.get(f"{BASE}/extractions").status_code == 403
    app.dependency_overrides.clear()


def test_the_run_history_serializes_dates_with_offset(client, reader) -> None:
    start = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
    reader.run_rows = [
        {
            "corrida_id": uuid4(),
            "secuencia": "reporte_ventas",
            "inicio": start,
            **dict.fromkeys(
                [
                    "origen",
                    "estado",
                    "programada",
                    "fuera_de_horario",
                    "extraccion_fin",
                    "transformacion_inicio",
                    "transformacion_fin",
                    "datos_listos",
                    "fase2_inicio",
                    "fase2_fin",
                    "fin",
                    "extraccion_s",
                    "transformacion_s",
                    "hasta_datos_listos_s",
                    "duracion_s",
                    "retraso_inicio_s",
                    "extraccion_fin_desde_programada_s",
                    "datos_listos_desde_programada_s",
                    "fin_desde_programada_s",
                    "extracciones",
                    "extracciones_fallidas",
                    "filas_extraidas",
                    "transformaciones_fallidas",
                    "codigo_salida",
                    "os_hostname",
                    "os_pid",
                ]
            ),
        }
    ]

    body = client.get(f"{BASE}/sequence-runs", params={"estado": "running"}).json()

    assert body["items"][0]["inicio"] == "2026-10-22T08:00:00Z"
