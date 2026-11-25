"""The area's workbook while it is kept in a sheet: read it, upload it back, export the
history in its format, and date pending increases without a SIESA load (2026-11-25)."""

from __future__ import annotations

import io
import time
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import unquote
from uuid import UUID, uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Environment, Settings
from app.core.errors import ForbiddenError, InvalidInputError
from app.main import create_app
from app.modules.commercial_masters.application.price_increases import PriceIncreaseService
from app.modules.commercial_masters.domain.price_increases import (
    SOURCE_REQUEST,
    ExportRow,
    PriceIncreaseFilters,
    PriceIncreaseValues,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    HISTORY_HEADERS,
    PriceIncreaseFileReader,
    history_workbook,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll
from tests.modules.supplier_supports.conftest import FakeManagers, person

MANAGER = "costos@jdh.com.co"
BASE = "/api/v1/commercial/price-increases"
TODAY = date(2026, 11, 25)


def workbook(rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["Acumulado de alzas"])
    sheet.append(list(HISTORY_HEADERS))
    for row in rows:
        sheet.append([row.get(name) for name in HISTORY_HEADERS])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def row(**values):
    base = {
        "Fecha de notificacion": datetime(2026, 1, 10),
        "Fecha de actualizacion Sistema": datetime(2026, 2, 19),
        "Fecha de Incremento": datetime(2026, 2, 19),
        "Nit": "901868602",
        "Referencia": "27914",
        "Proveedor": "1214 - FELLIN",
        "Desc. item": "Mantequilla",
        "Costo anterior sin Impuesto Saludable": "$ 81.960,00",
        "Costo Nuevo sin Impuesto Saludable": "$ 95.484",
        "variacion $": "$ 13.524,00",
        "Variacion %": 0.165,
        "Estado Incremento": "SUBIO",
        "Precio": 117015.0,
    }
    return {**base, **values}


class FakeHistory:
    """The repository port over a dictionary by row key."""

    def __init__(self) -> None:
        self.rows: dict[str, PriceIncreaseValues] = {}
        self.ids: dict[str, UUID] = {}
        self.open: set[UUID] = set()
        self.products = {"27914"}

    async def existing(self, keys):
        return {key: self.rows[key] for key in keys if key in self.rows}

    async def known_skus(self, skus):
        return set(skus) & self.products

    async def upsert(self, rows, actor, now):
        for item in rows:
            self.rows[item.key] = item
            self.ids.setdefault(item.key, uuid4())

    async def export_rows(self, filters):
        items = [
            value
            for value in self.rows.values()
            if filters.pending is None or (value.system_updated_on is None) == filters.pending
        ]
        items.sort(key=lambda v: (v.increased_on, v.notified_on, v.sku, v.occurrence))
        return [ExportRow(value, "1214", "FELLIN", "901868602", "Mantequilla") for value in items]

    async def ids_for(self, filters, limit):
        return [
            self.ids[key] for key, value in self.rows.items() if value.system_updated_on is None
        ][:limit]

    async def mark_loaded(self, ids, day, actor, now):
        count = 0
        for key, row_id in self.ids.items():
            value = self.rows[key]
            if row_id in ids and value.system_updated_on is None and row_id not in self.open:
                self.rows[key] = replace(value, system_updated_on=day, siesa_changed=True)
                count += 1
        return count


@pytest.fixture
def history() -> FakeHistory:
    return FakeHistory()


@pytest.fixture
def service(history) -> PriceIncreaseService:
    return PriceIncreaseService(
        repository=history, managers=FakeManagers([MANAGER]), today=lambda: TODAY
    )


# ------------------------------------------------------------------ reading


def test_the_workbook_loads_as_the_area_keeps_it() -> None:
    content = workbook(
        [
            row(),
            row(
                **{
                    "Referencia": "16315",
                    "Fecha de notificacion": datetime(1900, 1, 25),
                    "Fecha de actualizacion Sistema": None,
                    "Costo anterior sin Impuesto Saludable": "#N/D",
                    "Costo Nuevo sin Impuesto Saludable": 74532.41,
                    "Costo nuevo con Impuesto Saludable": "$ 89.438,89",
                    "Estado Incremento": None,
                    "Notificacion": "OK",
                }
            ),
            row(**{"Referencia": None}),
            row(
                **{
                    "Referencia": "4207",
                    "Costo Nuevo sin Impuesto Saludable": "DESCODIFICADO",
                    "OBSERVACIONES": "MODIFICAR COTIZACION",
                    "Precio": "#¡VALOR!",
                }
            ),
            row(**{"Referencia": "1045", "Costo Nuevo sin Impuesto Saludable": "12 mil"}),
        ]
    )

    rows = PriceIncreaseFileReader().read("Acumulado_Alza_Precios.xlsx", content)

    assert [item.row_number for item in rows] == [3, 4, 6, 7]
    first = rows[0].values
    assert (first.sku, first.old_cost, first.new_cost) == (
        "27914",
        Decimal("81960.00"),
        Decimal("95484"),
    )
    assert first.status == "subio" and first.price == Decimal("117015.0")
    assert [item.problems for item in rows[:3]] == [(), (), ()]
    assert rows[1].values.notified is True and rows[1].values.system_updated_on is None
    assert rows[2].values.observations == (
        "MODIFICAR COTIZACION; Costo nuevo sin impuesto saludable: DESCODIFICADO"
    )
    assert rows[3].problems == ("Costo nuevo sin impuesto saludable: «12 mil» no se pudo leer",)
    with pytest.raises(InvalidInputError, match="Excel"):
        PriceIncreaseFileReader().read("alzas.csv", b"a,b")


# ------------------------------------------------------------------ upload


async def test_an_upload_simulates_then_upserts_and_keeps_chained_increases(
    service, history
) -> None:
    content = workbook(
        [
            row(),
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None}),
            # Typed twice, value for value: counts once.
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None}),
            # The same key, other values: a second increase of the day (#2).
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None, "Precio": 1}),
        ]
    )
    manager = person(MANAGER)

    simulated = await service.upload(manager, "alzas.xlsx", content, apply=False)
    assert (simulated.rows_read, simulated.inserted, simulated.duplicated) == (4, 3, 1)
    assert (simulated.unknown_skus, simulated.pending) == (1, 2) and history.rows == {}

    applied = await service.upload(manager, "alzas.xlsx", content, apply=True)
    assert applied.applied and applied.inserted == 3
    assert any(key.endswith("#2") for key in history.rows)
    again = await service.upload(manager, "alzas.xlsx", content, apply=False)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 3)
    with pytest.raises(ForbiddenError):
        await service.upload(person("otro@jdh.com.co"), "alzas.xlsx", content, apply=False)
    with pytest.raises(InvalidInputError, match="vacío"):
        await service.upload(manager, "alzas.xlsx", b"", apply=False)


async def test_a_row_from_a_supplier_request_keeps_its_origin(service, history) -> None:
    line_id = uuid4()
    from_request = PriceIncreaseValues(
        "27914",
        notified_on=date(2026, 1, 10),
        system_updated_on=date(2026, 2, 19),
        increased_on=date(2026, 2, 19),
        old_cost=Decimal("81960"),
        new_cost=Decimal("95484"),
        variation_amount=Decimal("13524"),
        variation_pct=Decimal("0.165"),
        status="subio",
        price=Decimal("110000"),
        source=SOURCE_REQUEST,
        request_line_id=line_id,
    )
    await history.upsert([from_request], MANAGER, datetime(2026, 11, 25))

    report = await service.upload(person(MANAGER), "alzas.xlsx", workbook([row()]), apply=True)

    stored = next(iter(history.rows.values()))
    assert report.updated == 1 and stored.price == Decimal("117015.0")
    assert (stored.source, stored.request_line_id) == (SOURCE_REQUEST, line_id)


# ------------------------------------------------------------------ export


async def test_an_export_uploaded_back_changes_nothing(service, history) -> None:
    content = workbook(
        [
            row(),
            row(
                **{
                    "Referencia": "16315",
                    "Fecha de actualizacion Sistema": None,
                    "Regional": "ANTIOQUIA",
                }
            ),
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None, "Precio": 1}),
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None, "Precio": 2}),
            row(
                **{
                    "Referencia": "1045",
                    "Notificacion": "OK",
                    "Cambio en siesa": "OK",
                    "Margen": 0.18,
                }
            ),
        ]
    )
    await service.upload(person(MANAGER), "alzas.xlsx", content, apply=True)

    name, exported = await service.export(service_filters())
    report = await service.upload(person(MANAGER), name, exported, apply=False)

    assert name == "Acumulado Alza Precios 25-11-2026.xlsx"
    assert (report.rows_read, report.inserted, report.updated, report.unchanged) == (5, 0, 0, 5)
    sheet = openpyxl.load_workbook(io.BytesIO(exported)).worksheets[0]
    assert [cell.value for cell in sheet[1]] == list(HISTORY_HEADERS)
    first = {header: cell.value for header, cell in zip(HISTORY_HEADERS, sheet[2], strict=True)}
    assert first["Proveedor"] == "1214 - FELLIN" and first["Nit"] == "901868602"


def service_filters() -> PriceIncreaseFilters:
    return PriceIncreaseFilters()


def test_a_history_sized_export_writes_quickly() -> None:
    rows = [
        ExportRow(
            PriceIncreaseValues(
                str(index), increased_on=date(2026, 1, 1), new_cost=Decimal("1000.5")
            )
        )
        for index in range(50_000)
    ]
    start = time.perf_counter()
    content = history_workbook(rows)
    assert time.perf_counter() - start < 30 and len(content) > 100_000


# ------------------------------------------------------------------ system date


async def test_pending_increases_are_dated_without_a_load(service, history) -> None:
    content = workbook(
        [
            row(),
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None}),
            row(**{"Referencia": "1045", "Fecha de actualizacion Sistema": None}),
        ]
    )
    await service.upload(person(MANAGER), "alzas.xlsx", content, apply=True)
    ids = {value.sku: history.ids[key] for key, value in history.rows.items()}
    history.open.add(ids["1045"])

    result = await service.mark_loaded(person(MANAGER), date(2026, 11, 24), list(ids.values()))

    # The one already dated and the one in an open load are skipped.
    assert (result.updated, result.skipped) == (1, 2)
    dated = {value.sku: value for value in history.rows.values()}
    assert dated["16315"].system_updated_on == date(2026, 11, 24) and dated["16315"].siesa_changed
    every = await service.mark_loaded(person(MANAGER), TODAY, all=True)
    assert (every.updated, every.skipped) == (0, 1)
    with pytest.raises(InvalidInputError, match="hoy o anterior"):
        await service.mark_loaded(person(MANAGER), date(2026, 11, 26), [ids["1045"]])
    with pytest.raises(InvalidInputError, match="al menos"):
        await service.mark_loaded(person(MANAGER), TODAY, [])
    with pytest.raises(ForbiddenError):
        await service.mark_loaded(person("otro@jdh.com.co"), TODAY, [ids["1045"]])


# ------------------------------------------------------------------ http


def test_the_book_routes(service, history) -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        uuid4(), MANAGER, "Costos", False
    )
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.price_increase_service.override(service),
        TestClient(app) as client,
    ):
        files = {
            "file": (
                "alzas.xlsx",
                workbook(
                    [row(), row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None})]
                ),
                "application/octet-stream",
            )
        }
        simulated = client.post(f"{BASE}/upload", files=files)
        assert simulated.status_code == 200, simulated.text
        assert simulated.json()["applied"] is False and simulated.json()["inserted"] == 2
        applied = client.post(f"{BASE}/upload", params={"apply": "true"}, files=files)
        assert applied.json()["applied"] is True

        exported = client.get(f"{BASE}/export", params={"pending": "true"})
        assert exported.status_code == 200
        assert "Acumulado Alza Precios 25-11-2026.xlsx" in unquote(
            exported.headers["content-disposition"]
        )
        sheet = openpyxl.load_workbook(io.BytesIO(exported.content)).worksheets[0]
        assert sheet.max_row == 2

        marked = client.post(
            f"{BASE}/system-date", json={"all": True, "system_updated_on": "2026-11-25"}
        )
        assert marked.status_code == 200 and marked.json() == {"updated": 1, "skipped": 0}
        future = client.post(
            f"{BASE}/system-date", json={"all": True, "system_updated_on": "2026-12-01"}
        )
        assert future.status_code == 422
