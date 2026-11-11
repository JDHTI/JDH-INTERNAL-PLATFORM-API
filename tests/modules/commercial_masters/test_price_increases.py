"""Price increase history: reading the dirty workbook, the upsert and the system date."""

import io
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Environment, Settings
from app.core.errors import ForbiddenError, InvalidInputError
from app.main import create_app
from app.modules.commercial_masters.application.price_increases import PriceIncreaseService
from app.modules.commercial_masters.domain.price_increases import (
    CellError,
    CellNote,
    PriceIncrease,
    PriceIncreasePage,
    PriceIncreaseValues,
    parse_date,
    parse_money,
    parse_ratio,
    parse_status,
    pending_cost,
    status_of,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    PriceIncreaseFileReader,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll
from tests.modules.supplier_supports.conftest import FakeManagers, person

MANAGER = "bi@jdh.com.co"
BASE = "/api/v1/commercial"
HEADER = [
    "Fecha de notificacion",
    "Fecha de actualizacion Sistema",
    "Fecha de Incremento",
    "SKU Proveedor",
    "Codigo de Barras EAN 13",
    "Nit",
    "Referencia",
    "Proveedor",
    "Desc. item",
    "Unidad de Empaque",
    "Estado el Item",
    "Categoria",
    "ICUI (Indique el porcentaje)",
    "IBUA (Indique el valor por unidad)",
    "Costo anterior con Impuesto Saludable",
    "Costo anterior sin Impuesto Saludable",
    "Costo Nuevo sin Impuesto Saludable",
    "Costo nuevo con Impuesto Saludable",
    "variacion $",
    "Variacion %",
    "Estado Incremento",
    "OBSERVACIONES",
    "Margen",
    "Precio",
    "Notificacion",
    "Cambio en siesa",
]


def workbook(rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["Acumulado de alzas"])
    sheet.append(HEADER)
    for row in rows:
        sheet.append([row.get(name) for name in HEADER])
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


# ------------------------------------------------------------------ cells


def test_money_cells_in_every_format_the_area_types() -> None:
    assert parse_money("$ 81.960,00") == Decimal("81960.00")
    assert parse_money("$ 95.484") == Decimal("95484")
    assert parse_money("$ 1,00") == Decimal("1.00")
    assert parse_money(70980.5) == Decimal("70980.5")
    assert parse_money("-") is None and parse_money("$ -") is None and parse_money(None) is None
    # Formula errors are cells without a value, not problems.
    assert parse_money("#N/D") is None and parse_money("#¡REF!") is None
    assert parse_money("#¡VALOR!") is None
    # Words where a number goes are a note about the row.
    with pytest.raises(CellNote):
        parse_money("CODIGO YA NO EXISTE")
    with pytest.raises(CellError):
        parse_money("12abc")
    assert parse_ratio("16,5 %") == Decimal("0.165") and parse_ratio(0.02) == Decimal("0.02")


def test_dates_are_kept_as_the_file_has_them() -> None:
    assert parse_date(datetime(2026, 2, 19, 0, 0)) == date(2026, 2, 19)
    assert parse_date("19/02/2026") == date(2026, 2, 19)
    assert parse_date(46072) == date(2026, 2, 19)
    assert parse_date(None) is None and parse_date(" ") is None
    # Old or mistyped, the history keeps them: 1900 and the year 0206 the file has.
    assert parse_date(datetime(1900, 1, 18)) == date(1900, 1, 18)
    assert parse_date("18/09/0206") == date(206, 9, 18)
    with pytest.raises(CellError):
        parse_date("pendiente")


def test_status_cost_and_key() -> None:
    assert parse_status("Subió") == "subio" and parse_status("ESTABLE") == "estable"
    assert parse_status("otro") is None
    assert status_of(Decimal(10), Decimal(12)) == "subio"
    assert status_of(Decimal(10), Decimal(8)) == "bajo"
    assert status_of(Decimal(10), Decimal(10)) == "estable"
    assert status_of(None, Decimal(1)) is None
    values = PriceIncreaseValues(
        "1", increased_on=date(2026, 2, 1), new_cost=Decimal(100), new_cost_with_tax=Decimal(120)
    )
    assert values.key == "1|2026-02-01|"
    # The new cost with the healthy-food tax is what the supplier charges.
    assert pending_cost(values) == Decimal(120)
    assert pending_cost(PriceIncreaseValues("1", new_cost=Decimal(100))) == Decimal(100)
    assert (
        pending_cost(
            PriceIncreaseValues("1", system_updated_on=date(2026, 2, 2), new_cost=Decimal(1))
        )
        is None
    )


# ------------------------------------------------------------------ file


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
                    "Fecha de notificacion": "18/09/0206",
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
    assert first.variation_amount == Decimal("13524.00") and first.variation_pct == Decimal("0.165")
    assert first.status == "subio" and first.price == Decimal("117015.0")
    # 1900 dates, formula errors and notes load without a problem.
    assert [item.problems for item in rows[:3]] == [(), (), ()]
    second = rows[1].values
    assert second.notified_on == date(1900, 1, 25) and second.system_updated_on is None
    assert second.notified is True and second.old_cost is None
    # Without a state in the file it is worked out; the old cost is unknown here.
    assert second.status is None
    assert pending_cost(second) == Decimal("89438.89")
    third = rows[2].values
    assert third.notified_on == date(206, 9, 18)
    assert third.new_cost is None and third.price is None
    assert third.observations == (
        "MODIFICAR COTIZACION; Costo nuevo sin impuesto saludable: DESCODIFICADO"
    )
    # Only a number that cannot be read is reported.
    assert rows[3].problems == ("Costo nuevo sin impuesto saludable: «12 mil» no se pudo leer",)


def test_a_file_without_the_header_or_not_excel_is_refused() -> None:
    reader = PriceIncreaseFileReader()
    with pytest.raises(InvalidInputError, match="Excel"):
        reader.read("alzas.csv", b"a,b")
    book = Workbook()
    book.active.append(["Referencia", "Descripcion"])
    buffer = io.BytesIO()
    book.save(buffer)
    with pytest.raises(InvalidInputError, match="Costo Nuevo"):
        reader.read("alzas.xlsx", buffer.getvalue())


# ------------------------------------------------------------------ service


class FakeIncreases:
    def __init__(self) -> None:
        self.rows: dict[str, PriceIncreaseValues] = {}
        self.products = {"27914"}
        self.upserts: list[list[PriceIncreaseValues]] = []
        self.dates: list[tuple] = []

    async def existing(self, keys):
        return {key: self.rows[key] for key in keys if key in self.rows}

    async def known_skus(self, skus):
        return set(skus) & self.products

    async def upsert(self, rows, actor, now):
        self.upserts.append(list(rows))
        for item in rows:
            self.rows[item.key] = item

    async def set_system_date(self, increase_id, day, actor, now):
        self.dates.append((increase_id, day, actor))

    async def get(self, increase_id):
        return PriceIncrease(
            increase_id, PriceIncreaseValues("27914"), MANAGER, datetime(2026, 11, 11)
        )

    async def page(self, filters, page, size):
        self.filters = filters
        item = PriceIncrease(
            uuid4(),
            PriceIncreaseValues(
                "27914", new_cost=Decimal(95484), old_cost=Decimal(81960), status="subio"
            ),
            MANAGER,
            datetime(2026, 11, 11, 10, 0),
            description="Mantequilla",
        )
        counts = {"subio": 1, "bajo": 0, "estable": 0, "pendientes": 1}
        return PriceIncreasePage((item,), 1, page, size, counts)


@pytest.fixture
def increases() -> FakeIncreases:
    return FakeIncreases()


@pytest.fixture
def service(increases) -> PriceIncreaseService:
    return PriceIncreaseService(
        repository=increases,
        managers=FakeManagers([MANAGER]),
        reader=PriceIncreaseFileReader(),
        max_upload_bytes=5 * 1024 * 1024,
    )


async def test_an_upload_simulates_then_upserts(service, increases) -> None:
    content = workbook(
        [
            row(),
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None}),
            # The same key again: the last row of the file wins.
            row(**{"Referencia": "16315", "Fecha de actualizacion Sistema": None, "Precio": 1}),
        ]
    )
    manager = person(MANAGER)

    simulated = await service.upload(manager, "alzas.xlsx", content, apply=False)

    assert (simulated.applied, simulated.rows_read, simulated.inserted) == (False, 3, 2)
    assert (simulated.duplicated, simulated.unknown_skus, simulated.pending) == (1, 1, 1)
    assert increases.upserts == []

    applied = await service.upload(manager, "alzas.xlsx", content, apply=True)
    assert applied.applied and applied.inserted == 2
    assert {item.sku: item.price for item in increases.upserts[0]}["16315"] == Decimal(1)

    again = await service.upload(manager, "alzas.xlsx", content, apply=False)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)
    changed = workbook([row(**{"Precio": 120000})])
    assert (await service.upload(manager, "alzas.xlsx", changed, apply=False)).updated == 1


async def test_only_masters_managers_upload_or_set_the_date(service, increases) -> None:
    other = person("comercial@jdh.com.co")
    with pytest.raises(ForbiddenError):
        await service.upload(other, "alzas.xlsx", workbook([row()]), apply=False)
    with pytest.raises(ForbiddenError):
        await service.set_system_date(other, uuid4(), date(2026, 11, 11))
    with pytest.raises(InvalidInputError, match="vacío"):
        await service.upload(person(MANAGER), "alzas.xlsx", b"", apply=False)

    increase_id = uuid4()
    await service.set_system_date(person(MANAGER), increase_id, date(2026, 11, 11))
    await service.set_system_date(person(MANAGER), increase_id, None)
    assert [item[1] for item in increases.dates] == [date(2026, 11, 11), None]
    with pytest.raises(InvalidInputError, match="2000"):
        await service.set_system_date(person(MANAGER), increase_id, date(1999, 1, 1))


# ------------------------------------------------------------------ http


def test_the_price_increase_routes(service, increases) -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        uuid4(), MANAGER, "BI", True
    )
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.price_increase_service.override(service),
        TestClient(app) as client,
    ):
        listed = client.get(f"{BASE}/price-increases", params={"q": "mante", "pending": "true"})
        assert listed.status_code == 200
        body = listed.json()
        assert body["counts"] == {"subio": 1, "bajo": 0, "estable": 0, "pendientes": 1}
        assert body["can_manage"] is True and body["items"][0]["status_label"] == "Subió"
        assert body["items"][0]["is_pending"] is True and increases.filters.pending is True
        assert client.get(f"{BASE}/price-increases", params={"status": "otro"}).status_code == 422

        uploaded = client.post(
            f"{BASE}/price-increases/upload",
            files={"file": ("alzas.xlsx", workbook([row()]), "application/octet-stream")},
        )
        assert uploaded.status_code == 200 and uploaded.json()["inserted"] == 1
        assert uploaded.json()["applied"] is False

        increase_id = uuid4()
        patched = client.patch(
            f"{BASE}/price-increases/{increase_id}", json={"system_updated_on": "2026-11-11"}
        )
        assert patched.status_code == 200 and increases.dates[-1][1] == date(2026, 11, 11)
