"""Use cases of the quoter."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.quotations.application.service import LineChange, normalize_nit
from app.modules.quotations.domain.entities import CostInfo, ListPrice, ValueTaxKey
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, product, sale
from tests.modules.supplier_supports.conftest import person


async def test_create_builds_every_sku_with_cost_support_and_target(
    service, advisor, warehouse, masters, supports
) -> None:
    warehouse.sales[NIT].append(sale("12014", "12141", supplier_code="359"))
    masters.catalog["12014"] = product("12014", description="Vino tetrapack")
    warehouse.prices["12014"] = ListPrice("12014", Decimal("12141"), Decimal("6911"))
    warehouse.taxes["12014"] = ValueTaxKey("12014", "0005", "IMPOCONSUMO")
    supports.items.append(agreement(supplier_value=Decimal("0.05"), product_sku="2537"))

    detail = await service.create(advisor, "900.315.138-1", 12)

    quotation = detail.quotation
    assert quotation.customer_nit == NIT and quotation.status == "borrador"
    assert quotation.price_list_id == "1" and quotation.price_list_customers == 28113
    assert "lista_compartida" in quotation.warnings
    assert quotation.cost_cut_date == date(2026, 11, 3) and quotation.line_count == 2
    first, second = detail.lines
    assert first.sku == "2537" and first.supplier_name == "LOCERIA COLOMBIANA S.A."
    assert first.support is not None and first.support.amount == Decimal("150")
    assert second.value_tax_key == "0005" and second.value_tax_amount == Decimal("6911")
    assert "sin_costo" in second.problems
    assert ("last_sales", NIT, 12) in warehouse.calls
    assert ("costs", ("2537", "12014"), "001") in warehouse.calls


async def test_create_checks_access_window_customer_and_sales(service, advisor, warehouse) -> None:
    with pytest.raises(ForbiddenError):
        await service.create(person("otro@jdh.com.co"), NIT, 12)
    with pytest.raises(InvalidInputError):
        await service.create(advisor, NIT, 3)
    with pytest.raises(NotFoundError):
        await service.create(advisor, "111", 6)
    warehouse.sales[NIT] = []
    with pytest.raises(InvalidInputError, match="no tiene ventas"):
        await service.create(advisor, NIT, 6)
    warehouse.down = True
    with pytest.raises(DependencyUnavailableError):
        await service.create(advisor, NIT, 6)


async def test_prices_are_saved_marked_and_versioned(service, advisor) -> None:
    created = await service.create(advisor, NIT, 12)
    quotation = created.quotation

    priced = await service.update_lines(
        advisor, quotation.id, 1, [LineChange("2537", Decimal("3500"))]
    )

    line = priced.lines[0]
    assert line.new_price == Decimal("3500") and line.compliance == "bajo_minimo"
    assert priced.quotation.version == 2 and priced.quotation.below_rule_count == 1
    with pytest.raises(ConflictError):
        await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", None)])
    with pytest.raises(InvalidInputError, match="no está"):
        await service.update_lines(advisor, quotation.id, 2, [LineChange("1", Decimal(1))])
    with pytest.raises(InvalidInputError, match="4 decimales"):
        await service.update_lines(
            advisor, quotation.id, 2, [LineChange("2537", Decimal("1.00001"))]
        )
    with pytest.raises(InvalidInputError, match="mayor que cero"):
        await service.update_lines(advisor, quotation.id, 2, [LineChange("2537", Decimal(0))])
    ok = await service.update_lines(
        advisor, quotation.id, 2, [LineChange("2537", Decimal("4100.00000"))]
    )
    assert ok.lines[0].compliance == "cumple" and ok.quotation.below_rule_count == 0


async def test_generate_requires_prices_that_meet_the_rule(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    with pytest.raises(InvalidInputError, match="No hay precios"):
        await service.generate(advisor, quotation.id, 1, None, None)
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("3000"))])
    with pytest.raises(InvalidInputError, match="no cumplen"):
        await service.generate(advisor, quotation.id, 2, None, None)
    with pytest.raises(ConflictError):
        await service.export(advisor, quotation.id, "txt")
    await service.update_lines(advisor, quotation.id, 2, [LineChange("2537", Decimal("4100"))])
    with pytest.raises(InvalidInputError, match="anterior a hoy"):
        await service.generate(advisor, quotation.id, 3, date(2020, 1, 1), None)
    with pytest.raises(InvalidInputError, match="lista"):
        await service.generate(advisor, quotation.id, 3, None, "1-M")

    generated = await service.generate(advisor, quotation.id, 3, None, "1M")

    assert generated.quotation.status == "generado"
    assert generated.quotation.activation_date == date(2026, 10, 10)
    flat = await service.export(advisor, quotation.id, "txt")
    lines = flat.content.decode("ascii").split("\r\n")
    assert lines[1][19:22] == "1M " and lines[1][29:33] == "2537"
    assert lines[1][139:147] == "20261010" and lines[1][167:187] == "000000000004100.0000"
    assert flat.filename == "precios-lista1M-900315138-20261010.txt"
    workbook = await service.export(advisor, quotation.id, "xlsx")
    assert workbook.filename == "ACTUALIZACION LISTAS PRECIOS_asesor@jdh.com.co.xlsx"
    edited = await service.update_lines(advisor, quotation.id, 4, [LineChange("2537", None)])
    assert edited.quotation.status == "borrador"


async def test_refresh_keeps_prices_and_reprices_with_the_new_cost(
    service, advisor, warehouse
) -> None:
    quotation = (await service.create(advisor, NIT, 6)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4000"))])
    warehouse.cost_rows["2537"] = CostInfo("2537", Decimal("3500"), True)

    refreshed = await service.refresh(advisor, quotation.id, 2)

    line = refreshed.lines[0]
    assert line.new_price == Decimal("4000") and line.cost == Decimal("3500")
    assert line.compliance == "bajo_minimo" and "costo_global" in line.problems
    assert refreshed.quotation.version == 3


async def test_customers_mine_uses_the_seller_linked_by_email(service, advisor, warehouse) -> None:
    mine, linked = await service.customers(advisor, "mine", None, 1, 20)
    everyone, _ = await service.customers(advisor, "all", None, 1, 20)

    assert linked and [item.nit for item in mine.items] == [NIT]
    assert everyone.total == 2
    unlinked = person("nuevo@jdh.com.co", admin=True)
    empty, linked = await service.customers(unlinked, "mine", None, 1, 20)
    assert empty.total == 0 and not linked
    warehouse.down = True
    everyone, linked = await service.customers(advisor, "all", None, 1, 20)
    assert everyone.total == 2 and not linked
    permissions = await service.permissions(advisor)
    assert permissions.can_manage and permissions.seller is None


async def test_list_and_delete(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 6)).quotation
    other = person("otro@jdh.com.co", admin=True)

    assert (
        await service.list(
            advisor, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
        )
    ).total == 1
    assert (
        await service.list(
            other, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
        )
    ).total == 0
    with pytest.raises(ForbiddenError):
        await service.delete(person(advisor.email), quotation.id)
    await service.delete(other, quotation.id)
    with pytest.raises(NotFoundError):
        await service.get(advisor, quotation.id)


def test_normalize_nit() -> None:
    assert normalize_nit(" 900.315.138-1 ") == "900315138"
    with pytest.raises(InvalidInputError):
        normalize_nit("--")
