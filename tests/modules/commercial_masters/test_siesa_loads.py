"""SIESA loads of pending increases: the 0212 file, what goes and what not, the flow."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.main import create_app
from app.modules.commercial_masters.application.price_increases import PriceIncreaseService
from app.modules.commercial_masters.application.siesa_loads import Selection, SiesaLoadService
from app.modules.commercial_masters.domain.price_increases import (
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreaseValues,
)
from app.modules.commercial_masters.domain.siesa_loads import (
    GENERATED,
    LOAD_STATUSES,
    LOADED,
    VOIDED,
    CurrentQuotation,
    LoadPage,
    LoadRow,
    SiesaLoad,
    file_name,
    latest_quotations,
    plan,
)
from app.modules.commercial_masters.infrastructure.siesa_load_files import (
    HEADERS,
    SHEET,
    quotation_file,
    quotation_workbook,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll
from tests.modules.supplier_supports.conftest import FakeManagers, person

MANAGER = "costos@jdh.com.co"
TODAY = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 16, 48, tzinfo=UTC)
BASE = "/api/v1/commercial/price-increases/siesa-loads"

# The area's file of 2026-11-18 (``34524H11M48…txt``), as SIESA's importer read it.
AREA_FILE = (
    "000000100000001001\r\n"
    "0000002021200020011901053485      001COP000000025887"
    + " "
    * 105
    + "20261118KL  000000000022000.000000520991231\r\n"
    "0000003021200020011901053485      001COP000000025888"
    + " "
    * 105
    + "20261118KL  000000000024000.000000520991231\r\n"
    "0000004021200020011901053485      001COP000000026195"
    + " "
    * 105
    + "20261118KL  000000000014500.000000520991231\r\n"
    "0000005021200020011890900099      001COP000000027603"
    + " "
    * 105
    + "20261118UNID000000000011962.500000520991231\r\n"
    "0000006021200020011890900099      001COP000000028868"
    + " "
    * 105
    + "20261118UNID000000000016162.500000520991231\r\n"
    "0000007021200020011890900099      001COP000000029109"
    + " "
    * 105
    + "20261118UNID000000000020962.500000520991231\r\n"
    "0000008021200020011890900099      001COP00000009773"
    + " "
    * 106
    + "20261118UNID000000000018150.000000520991231\r\n"
    "0000009021200020011890326050      001COP000000025806"
    + " "
    * 105
    + "20261118UNID000000000015583.000000520991231\r\n"
    "000001099990001001\r\n"
).encode("ascii")

AREA_ROWS = [
    ("901053485", "25887", "KL", "22000"),
    ("901053485", "25888", "KL", "24000"),
    ("901053485", "26195", "KL", "14500"),
    ("890900099", "27603", "UNID", "11962.5"),
    ("890900099", "28868", "UNID", "16162.5"),
    ("890900099", "29109", "UNID", "20962.5"),
    ("890900099", "9773", "UNID", "18150"),
    ("890326050", "25806", "UNID", "15583"),
]


def load_row(nit, sku, unit, price, day=TODAY) -> LoadRow:
    return LoadRow(
        increase_id=uuid4(),
        sku=sku,
        description=None,
        nit=nit,
        branch="001",
        currency="COP",
        unit=unit,
        price=Decimal(price).quantize(Decimal("0.0001")),
        activation_date=day,
        delivery_days=5,
    )


# ------------------------------------------------------------------ files


def test_the_file_is_the_area_file_byte_for_byte() -> None:
    rows = [load_row(*values) for values in AREA_ROWS]

    content = quotation_file(rows)

    assert content == AREA_FILE
    assert all(len(line) == 200 for line in content.decode().split("\r\n")[1:-2])


def test_the_workbook_is_the_area_workbook() -> None:
    rows = [load_row(*values) for values in AREA_ROWS]

    sheet = openpyxl.load_workbook(io.BytesIO(quotation_workbook(rows)))[SHEET]

    assert SHEET == "Encabezado Cotizaciones " and [cell.value for cell in sheet[1]] == list(
        HEADERS
    )
    assert [cell.value for cell in sheet[2]] == [
        "901053485",
        "001",
        "25887",
        "20261118",
        "KL  ",
        "22000.00",
    ]
    assert [cell.value for cell in sheet[5]][4:] == ["UNID", "11962.50"]
    assert all(cell.number_format == "@" for cell in sheet[2])
    assert file_name(TODAY, 12, "txt") == "IncrementoCostos Entradas 18-11-2026 L12.txt"


def test_a_value_that_does_not_fit_is_refused() -> None:
    with pytest.raises(InvalidInputError):
        quotation_file([load_row("9" * 16, "1", "UNID", "1")])
    with pytest.raises(InvalidInputError):
        quotation_file([load_row("1", "Ñ1", "UNID", "1")])


# ------------------------------------------------------------------ plan


def increase(sku, *, cost="1000", day=date(2026, 12, 1), regional=None, **values):
    return PriceIncrease(
        uuid4(),
        PriceIncreaseValues(
            sku,
            increased_on=day,
            notified_on=date(2026, 11, 10),
            new_cost=None if cost is None else Decimal(cost),
            new_cost_with_tax=Decimal("1200"),
            regional=regional,
            **values,
        ),
        MANAGER,
        NOW,
        description=f"Producto {sku}",
    )


def quote(sku, nit="890900099", unit="UNID", day=date(2026, 1, 1), **values):
    return CurrentQuotation(sku, nit, "001", unit, "COP", 5, day, **values)


QUOTES = {sku: quote(sku) for sku in ("1", "2", "3", "4", "5", "6", "7")}


def test_what_goes_and_what_not() -> None:
    past = increase("1", day=date(2026, 11, 1))
    future = increase("2")
    regional = increase("3", regional="ANTIOQUIA")
    national = increase("3b", regional=" nacional ")
    loaded = increase("4", system_updated_on=date(2026, 11, 2))
    without_cost = increase("5", cost=None)
    in_load = increase("6")
    no_quote = increase("8")
    older = increase("7", day=date(2026, 11, 20))
    newer = increase("7", day=date(2026, 12, 2))

    result = plan(
        [past, future, regional, national, loaded, without_cost, in_load, no_quote, older, newer],
        {**QUOTES, "3b": quote("3b")},
        {in_load.id: 4},
        TODAY,
    )

    rows = {row.sku: row for row in result.rows}
    assert list(rows) == ["1", "2", "3b", "7"]
    # A past increase date is the day the file is generated; a later one stays.
    assert rows["1"].activation_date == TODAY and rows["2"].activation_date == date(2026, 12, 1)
    # The new cost without the healthy-food tax, as SIESA's unit price.
    assert rows["1"].price == Decimal("1000.0000") and rows["7"].increase_id == newer.id
    reasons = {item.sku: item.reason for item in result.excluded}
    assert "regional ANTIOQUIA" in reasons["3"]
    assert reasons["4"] == "Ya está cargada en SIESA"
    assert reasons["5"] == "No tiene costo nuevo sin impuesto saludable"
    assert reasons["6"] == "Ya está en el lote 4, sin confirmar"
    assert "no tiene cotización vigente" in reasons["8"]
    assert reasons["7"] == "La referencia tiene un alza más reciente en la selección"


def test_the_latest_quotation_in_force_goes_and_the_others_are_told() -> None:
    chosen = latest_quotations(
        [
            quote("25887", nit="811006789", unit="KL", day=date(2025, 3, 1)),
            quote("25887", nit="901053485", unit="KL", day=date(2026, 11, 18)),
            quote("9773"),
        ]
    )

    assert chosen["25887"].nit == "901053485" and chosen["25887"].others == ("811006789",)
    assert chosen["9773"].others == ()


# ------------------------------------------------------------------ service


class FakeHistory:
    def __init__(self, *items: PriceIncrease) -> None:
        self.items = {item.id: item for item in items}
        self.filters: list[PriceIncreaseFilters] = []

    async def ids_for(self, filters, limit):
        self.filters.append(filters)
        return [
            item.id
            for item in self.items.values()
            if item.values.system_updated_on is None
            and (not filters.supplier or filters.supplier == "100")
        ][:limit]

    async def by_ids(self, ids):
        return [self.items[i] for i in ids if i in self.items]


class FakeQuotations:
    def __init__(self, quotes=QUOTES) -> None:
        self.quotes = quotes
        self.asked: list[list[str]] = []

    async def current(self, skus):
        self.asked.append(list(skus))
        return {sku: self.quotes[sku] for sku in skus if sku in self.quotes}


class FakeLoads:
    def __init__(self, history: FakeHistory) -> None:
        self.history = history
        self.loads: dict[UUID, SiesaLoad] = {}

    async def open_loads(self, ids):
        return {
            row.increase_id: load.number
            for load in self.loads.values()
            if load.status == GENERATED
            for row in load.rows
            if row.increase_id in ids
        }

    async def create(self, rows, actor, now):
        if await self.open_loads([row.increase_id for row in rows]):
            raise ConflictError("en otro lote")
        load = SiesaLoad(
            uuid4(), len(self.loads) + 1, GENERATED, len(rows), actor, now, rows=tuple(rows)
        )
        self.loads[load.id] = load
        return load

    async def get(self, load_id):
        return self.loads.get(load_id)

    async def page(self, status, page, size):
        items = [load for load in self.loads.values() if not status or load.status == status]
        counts = {
            value: sum(1 for load in self.loads.values() if load.status == value)
            for value in LOAD_STATUSES
        }
        return LoadPage(tuple(items), len(items), page, size, counts)

    def _open(self, load_id):
        load = self.loads.get(load_id)
        if load is None:
            raise NotFoundError("Lote no encontrado")
        if load.status != GENERATED:
            raise ConflictError("El lote ya fue confirmado o anulado")
        return load

    async def confirm(self, load_id, loaded_on, actor, now):
        load = self._open(load_id)
        for row in load.rows:
            item = self.history.items[row.increase_id]
            self.history.items[item.id] = replace(
                item, values=replace(item.values, system_updated_on=loaded_on)
            )
        self.loads[load_id] = replace(
            load, status=LOADED, confirmed_by=actor, confirmed_at=now, loaded_on=loaded_on
        )

    async def void(self, load_id, actor, now):
        load = self._open(load_id)
        self.loads[load_id] = replace(load, status=VOIDED, voided_by=actor, voided_at=now)


def world(*items, managers=(MANAGER,)):
    history = FakeHistory(*items)
    loads = FakeLoads(history)
    increases = PriceIncreaseService(repository=history, managers=FakeManagers(list(managers)))
    service = SiesaLoadService(
        loads, increases, FakeQuotations(), today=lambda: TODAY, clock=lambda: NOW
    )
    return service, history, loads


async def test_generate_confirm_and_the_increases_leave_the_pending() -> None:
    first, second, regional = increase("1"), increase("2"), increase("3", regional="BOGOTA")
    service, history, loads = world(first, second, regional)
    manager = person(MANAGER)

    preview = await service.generate(manager, Selection(all=True), apply=False)
    assert preview.load is None and loads.loads == {}
    assert [row.sku for row in preview.plan.rows] == ["1", "2"]
    assert [item.sku for item in preview.plan.excluded] == ["3"]
    # Every pending one of the filter: pending is forced.
    assert history.filters[-1].pending is True

    stored = await service.generate(manager, Selection((first.id, second.id)), apply=True)
    load = stored.load
    assert load.number == 1 and load.status == GENERATED and load.row_count == 2
    again = await service.generate(manager, Selection((first.id,)), apply=False)
    assert again.plan.excluded[0].reason == "Ya está en el lote 1, sin confirmar"
    with pytest.raises(InvalidInputError, match="Ninguna"):
        await service.generate(manager, Selection((first.id,)), apply=True)

    txt = await service.file(load.id, "txt")
    assert txt.name == "IncrementoCostos Entradas 18-11-2026 L1.txt"
    assert txt.content.startswith(b"000000100000001001\r\n0000002021200020011890900099")
    assert (await service.file(load.id, "xlsx")).name.endswith("L1.xlsx")

    confirmed = await service.confirm(manager, load.id)
    assert confirmed.status == LOADED and confirmed.loaded_on == TODAY
    assert history.items[first.id].values.system_updated_on == TODAY
    with pytest.raises(ConflictError):
        await service.void(manager, load.id)
    after = await service.generate(manager, Selection((first.id,)), apply=False)
    assert after.plan.excluded[0].reason == "Ya está cargada en SIESA"


async def test_a_voided_load_frees_its_increases() -> None:
    first = increase("1")
    service, _, _ = world(first)
    manager = person(MANAGER)
    load = (await service.generate(manager, Selection((first.id,)), apply=True)).load

    voided = await service.void(manager, load.id)

    assert voided.status == VOIDED
    assert (await service.generate(manager, Selection((first.id,)), apply=True)).load.number == 2
    page = await service.page(None, 1, 10)
    assert page.counts == {GENERATED: 1, LOADED: 0, VOIDED: 1}


async def test_who_and_what_can_be_asked() -> None:
    first = increase("1")
    service, _, _ = world(first)
    reader = person("lector@jdh.com.co")

    with pytest.raises(ForbiddenError):
        await service.generate(reader, Selection((first.id,)), apply=False)
    with pytest.raises(InvalidInputError, match="al menos"):
        await service.generate(person(MANAGER), Selection(), apply=False)
    load = (await service.generate(person(MANAGER), Selection((first.id,)), apply=True)).load
    with pytest.raises(ForbiddenError):
        await service.confirm(reader, load.id)
    with pytest.raises(InvalidInputError, match="hoy o anterior"):
        await service.confirm(person(MANAGER), load.id, date(2026, 11, 19))
    with pytest.raises(NotFoundError):
        await service.get(uuid4())
    with pytest.raises(InvalidInputError):
        await service.file(load.id, "pdf")


# ------------------------------------------------------------------ http


def test_the_load_routes() -> None:
    first, second = increase("1"), increase("2")
    service, _, _ = world(first, second)
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        uuid4(), MANAGER, "Costos", False
    )
    v1 = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        v1.siesa_load_service.override(service),
        v1.price_increase_service.override(service.increases),
        TestClient(app) as client,
    ):
        preview = client.post(BASE, json={"all": True, "filters": {"supplier": "100"}})
        assert preview.status_code == 200, preview.text
        assert preview.json()["applied"] is False and len(preview.json()["rows"]) == 2
        assert preview.json()["rows"][0]["price"] == "1000.0000"

        stored = client.post(BASE, params={"apply": "true"}, json={"increase_ids": [str(first.id)]})
        load = stored.json()["load"]
        assert stored.json()["applied"] and load["number"] == 1 and len(load["rows"]) == 1

        listed = client.get(BASE).json()
        assert listed["total"] == 1 and listed["can_manage"] is True
        assert listed["items"][0]["status_label"] == "Generado"
        txt = client.get(f"{BASE}/{load['id']}/file", params={"kind": "txt"})
        assert txt.status_code == 200 and txt.content.endswith(b"000000399990001001\r\n")
        assert "L1.txt" in txt.headers["content-disposition"]
        assert client.get(f"{BASE}/{load['id']}/file", params={"kind": "pdf"}).status_code == 422

        confirmed = client.post(f"{BASE}/{load['id']}/confirm", json={"loaded_on": "2026-11-18"})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "cargado"
        assert client.post(f"{BASE}/{load['id']}/void").status_code == 409
        assert client.get(f"{BASE}/{uuid4()}").status_code == 404
