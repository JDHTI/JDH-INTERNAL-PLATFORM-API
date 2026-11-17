"""The JDH employee price list: MDE1 cost at the Margen Empleado JDH."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.main import create_app
from app.modules.commercial_masters.application.employee_prices import EmployeePriceService
from app.modules.commercial_masters.domain.employee_prices import (
    EmployeePriceProduct,
    WarehouseCost,
    employee_price,
    with_vat,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll

D = Decimal
_THIRTEEN = D("0.13")
_NINETEEN = D("19")


def item(sku: str, margin: Decimal | None = _THIRTEEN, vat: Decimal | None = _NINETEEN):
    return EmployeePriceProduct(
        sku=sku,
        description=f"Producto {sku}",
        brand="COLOMBINA",
        inventory_unit="UNID",
        vat_pct=vat,
        margin=margin,
        is_active=True,
    )


class Products:
    def __init__(self, *products: EmployeePriceProduct) -> None:
        self.products = list(products)

    async def employee_price_products(self):
        return self.products


class Costs:
    def __init__(self, *costs: WarehouseCost, fail: bool = False) -> None:
        self.costs = list(costs)
        self.fail = fail
        self.asked: list[str] = []

    async def warehouse_costs(self, warehouse):
        self.asked.append(warehouse)
        if self.fail:
            raise DataProviderUnavailable("down")
        return self.costs


@pytest.mark.parametrize(
    ("cost", "margin", "price"),
    [
        (D("8700"), D("0.13"), D("10000")),
        (D("1000"), D("0.13"), D("1149")),  # 1149.43 rounds down
        (D("1305"), D("0.13"), D("1500")),
        (D("1000"), None, D("1149")),  # no margin of its own: the 13 % default
        (D("1000"), D("0.2"), D("1250")),
        (D("1"), D("0.13"), None),  # a one-peso cost is a placeholder
        (None, D("0.13"), None),
    ],
)
def test_price_is_the_cost_over_one_minus_the_margin(cost, margin, price) -> None:
    assert employee_price(cost, margin) == price


def test_vat_is_added_in_whole_pesos() -> None:
    assert with_vat(D("10000"), D("19")) == D("11900")
    assert with_vat(D("1149"), D("5")) == D("1206")  # 1206.45
    assert with_vat(D("1149"), None) == D("1149")
    assert with_vat(None, D("19")) is None


async def test_the_list_prices_every_active_product_with_an_mde1_cost() -> None:
    costs = Costs(
        WarehouseCost("7244", D("8700"), date(2026, 11, 17)),
        WarehouseCost("1569", D("1000"), date(2026, 11, 16)),
        WarehouseCost("9999", D("500"), date(2026, 11, 17)),  # not an active product
    )
    service = EmployeePriceService(
        Products(item("1569", margin=D("0.2"), vat=None), item("7244"), item("0001")), costs
    )

    result = await service.price_list()

    assert costs.asked == ["MDE1"]
    assert result.warehouse == "MDE1" and result.snapshot_date == date(2026, 11, 17)
    assert [(line.sku, line.price, line.price_with_vat) for line in result.items] == [
        ("1569", D("1250"), D("1250")),
        ("7244", D("10000"), D("11900")),
    ]
    assert result.without_cost == 1  # 0001 has no MDE1 cost


async def test_a_warehouse_outage_is_a_dependency_error() -> None:
    service = EmployeePriceService(Products(item("7244")), Costs(fail=True))

    with pytest.raises(DependencyUnavailableError):
        await service.price_list()


def test_the_endpoint_returns_the_whole_list() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    who = Principal(uuid4(), "ana@jdh.com.co", "Ana", True)
    app.dependency_overrides[get_current_principal] = lambda: who
    container = app.state.container
    service = EmployeePriceService(
        Products(item("7244"), item("0001")),
        Costs(WarehouseCost("7244", D("8700"), date(2026, 11, 17))),
    )
    with (
        container.redis.override(AllowAll()),
        container.v1_container.employee_price_service.override(service),
        TestClient(app) as client,
    ):
        response = client.get("/api/v1/commercial/products/employee-prices")

    assert response.status_code == 200
    body = response.json()
    assert body["warehouse"] == "MDE1" and body["snapshot_date"] == "2026-11-17"
    assert body["total"] == 1 and body["without_cost"] == 1
    line = body["items"][0]
    assert line["sku"] == "7244" and line["margin"] == "0.13"
    assert line["cost"] == "8700" and line["price"] == "10000"
    assert line["price_with_vat"] == "11900"
