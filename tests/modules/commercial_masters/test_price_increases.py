"""Price increase history: the cell parsers the «MAESTRA» uses, the key, the system date and
the routes. The area's workbook is no longer uploaded (2026-11-25)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

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
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll
from tests.modules.supplier_supports.conftest import FakeManagers, person

MANAGER = "bi@jdh.com.co"
BASE = "/api/v1/commercial"


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
    assert PriceIncreaseValues("1", increased_on=date(2026, 2, 1), occurrence=2).key == (
        "1|2026-02-01|#2"
    )
    # The new cost with the healthy-food tax is what the supplier charges.
    assert pending_cost(values) == Decimal(120)
    assert pending_cost(PriceIncreaseValues("1", new_cost=Decimal(100))) == Decimal(100)
    assert (
        pending_cost(
            PriceIncreaseValues("1", system_updated_on=date(2026, 2, 2), new_cost=Decimal(1))
        )
        is None
    )


# ------------------------------------------------------------------ service


class FakeIncreases:
    def __init__(self) -> None:
        self.dates: list[tuple] = []

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
            open_load=7,
        )
        counts = {"subio": 1, "bajo": 0, "estable": 0, "pendientes": 1}
        return PriceIncreasePage((item,), 1, page, size, counts)


@pytest.fixture
def increases() -> FakeIncreases:
    return FakeIncreases()


@pytest.fixture
def service(increases) -> PriceIncreaseService:
    return PriceIncreaseService(repository=increases, managers=FakeManagers([MANAGER]))


async def test_only_managers_set_the_date(service, increases) -> None:
    with pytest.raises(ForbiddenError):
        await service.set_system_date(person("comercial@jdh.com.co"), uuid4(), date(2026, 11, 11))

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
        assert body["items"][0]["open_load"] == 7
        assert client.get(f"{BASE}/price-increases", params={"status": "otro"}).status_code == 422

        increase_id = uuid4()
        patched = client.patch(
            f"{BASE}/price-increases/{increase_id}", json={"system_updated_on": "2026-11-11"}
        )
        assert patched.status_code == 200 and increases.dates[-1][1] == date(2026, 11, 11)
