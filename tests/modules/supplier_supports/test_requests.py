"""Permissions, proposal validation and effective cost preserve supplier funding."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import derive
from app.modules.supplier_supports.domain.requests import request_mode, validate_value
from tests.modules.supplier_supports.conftest import FakeManagers, FixedCalendar, person


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1.01", "-1.01", "0.0000001"])
def test_invalid_percentages(value):
    with pytest.raises(InvalidInputError):
        validate_value("porcentaje", Decimal(value))


@pytest.mark.parametrize("value", ["-1", "1000000000000", "0.1234567"])
def test_invalid_net_cost(value):
    with pytest.raises(InvalidInputError):
        validate_value("costo_neto", Decimal(value))


def test_all_other_methods_use_percentage():
    assert request_mode("costo_neto") == "costo_neto"
    for method in (
        "pct_costo",
        "pct_precio_lista",
        "precio_neto",
        "valor_unidad",
        "puntos_margen",
        "pct_ventas",
        "margen_minimo",
    ):
        assert request_mode(method) == "porcentaje"


def test_net_cost_keeps_supplier_amount_and_cancels_back_to_original():
    data = SupportData(
        kind="tactico_cliente",
        calc_method="costo_neto",
        supplier_value=Decimal(80),
        reference_cost=Decimal(100),
        jdh_net_cost=Decimal(70),
        value_unit="caja",
        pack_factor=Decimal(10),
    )
    result = derive(data)
    assert result.supplier_amount == 20
    assert result.net_cost == 70
    assert result.jdh_amount == 10
    assert result.net_cost_per.unit == 7
    original = derive(replace(data, jdh_net_cost=None))
    assert original.net_cost == 80
    assert original.supplier_amount == 20


async def test_roles_and_ownership_are_enforced():
    repo = AsyncMock()
    service = SupportRequestService(
        repo, FakeManagers(["bi"]), FakeManagers(["sales"]), FixedCalendar()
    )
    sales, bi, outsider = person("sales"), person("bi"), person("other")
    assert await service.permissions(sales) == {
        "can_manage": False,
        "can_review": False,
        "can_request": True,
        "can_read": True,
    }
    await service.search(sales, None, None, 1, 20)
    assert repo.search.call_args.args[0] == sales.user_id
    await service.search(bi, None, None, 1, 20)
    assert repo.search.call_args.args[0] is None
    await service.search(bi, None, None, 1, 20, mine=True)
    assert repo.search.call_args.args[0] == bi.user_id
    await service.get(bi, uuid4(), mine=True)
    assert repo.get.call_args.args[1] == bi.user_id
    for principal in (sales, outsider):
        with pytest.raises(ForbiddenError):
            await service.decide(principal, uuid4(), 1, 1, True)
        with pytest.raises(ForbiddenError):
            await service.cancel(principal, uuid4(), 1, "cancelar")
        with pytest.raises(ForbiddenError):
            await service.history(principal, uuid4())
    with pytest.raises(ForbiddenError):
        await service.create(outsider, uuid4(), 1, Decimal("0.1"), "motivo")
    with pytest.raises(InvalidInputError):
        await service.create(sales, uuid4(), 1, Decimal("0.1"), "   ")
    with pytest.raises(InvalidInputError):
        await service.decide(bi, uuid4(), 1, None, False, " ")
