"""% Margen Garantizado with the supplier's minimum margin and its requests (2026-11-23)."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidInputError
from app.modules.supplier_supports.application.service import FieldError
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.requests import (
    MARGIN_MODE,
    SUPPLIER_MARGIN_MODE,
    request_mode,
    require_below_supplier_minimum,
    validate_value,
)

D = Decimal


def guarantee(**values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "precio_neto",
        "value_unit": "unidad",
        "target_margin_pct": D("0.18"),
        "supplier_min_margin_pct": D("0.13"),
        "product_sku": "1128",
        "customer_nit": "800116299",
        "valid_from": None,
        "valid_to": None,
    }
    return SupportData(**{**base, **values})


async def test_a_guaranteed_margin_is_created_with_the_supplier_minimum(service, manager) -> None:
    view = await service.create(manager, guarantee(), supplier_code="233")

    assert view.support.data.supplier_min_margin_pct == D("0.13")
    with pytest.raises(FieldError, match="Margen mínimo garantizado proveedor") as raised:
        await service.create(
            manager, guarantee(supplier_min_margin_pct=None, customer_nit=None), supplier_code="233"
        )
    assert raised.value.field == "supplier_min_margin_pct"


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (D("0.18"), "menor que el «Margen garantizado»"),
        (D("0.25"), "menor que el «Margen garantizado»"),
        (D("-1"), "entre -100 % y 100 %"),
    ],
)
async def test_the_supplier_minimum_stays_under_the_guarantee(
    service, manager, value, fragment
) -> None:
    with pytest.raises(FieldError, match=fragment):
        await service.create(manager, guarantee(supplier_min_margin_pct=value), supplier_code="233")


async def test_the_supplier_minimum_may_be_zero_or_negative(service, manager) -> None:
    zero = await service.create(
        manager, guarantee(supplier_min_margin_pct=D("0")), supplier_code="233"
    )
    negative = await service.create(
        manager,
        guarantee(supplier_min_margin_pct=D("-0.05"), customer_nit="900430148"),
        supplier_code="233",
    )

    assert zero.support.data.supplier_min_margin_pct == D("0")
    assert negative.support.data.supplier_min_margin_pct == D("-0.05")


def test_a_guaranteed_margin_asks_the_supplier_not_jdh() -> None:
    assert request_mode("precio_neto") == SUPPLIER_MARGIN_MODE
    assert request_mode("pct_costo") == MARGIN_MODE
    # Negative margins are valid for the supplier, never for JDH.
    validate_value(SUPPLIER_MARGIN_MODE, D("-0.05"))
    with pytest.raises(InvalidInputError):
        validate_value(MARGIN_MODE, D("-0.05"))
    with pytest.raises(InvalidInputError):
        validate_value(SUPPLIER_MARGIN_MODE, D("-1"))


def test_a_supplier_margin_request_asks_for_less_than_the_minimum_in_force() -> None:
    support = SimpleNamespace(supplier_min_margin_pct=D("0.13"), target_margin_pct=D("0.18"))

    require_below_supplier_minimum(support, D("0.07"))
    require_below_supplier_minimum(support, D("-0.10"))
    with pytest.raises(InvalidInputError, match="13.00 %"):
        require_below_supplier_minimum(support, D("0.13"))
    # A support saved before, without the minimum: under the guaranteed margin.
    older = SimpleNamespace(supplier_min_margin_pct=None, target_margin_pct=D("0.18"))
    with pytest.raises(InvalidInputError, match="18.00 %"):
        require_below_supplier_minimum(older, D("0.20"))
