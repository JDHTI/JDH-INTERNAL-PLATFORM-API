"""List updates: a quotation that starts from an existing SIESA price list."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.order_validation.domain.entities import CustomerScope
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain.entities import ItemInfo, ListPrice
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import LIST_MANAGER, MAIN_MANAGER, NIT, branch
from tests.modules.supplier_supports.conftest import TODAY, person

WHOLESALE = frozenset({"MAYORISTAS Y DISTRIBUIDORES"})


@pytest.fixture
def lister():
    return person(LIST_MANAGER)


async def test_a_list_update_starts_from_every_sku_of_the_list(service, lister, warehouse) -> None:
    warehouse.prices["26278"] = ListPrice("26278", Decimal(1))

    detail = await service.create_list_update(lister, price_list_id="10u")

    quotation = detail.quotation
    assert quotation.kind == "lista" and quotation.source == "lista"
    assert quotation.price_list_id == "10U" and quotation.customer_nit == ""
    assert quotation.customer_name == "MAYORISTAS CENTRO"
    assert quotation.list_action == "actualizacion" and quotation.price_family == "mayoristas"
    assert quotation.channel == "MAYORISTAS Y DISTRIBUIDORES"
    assert quotation.salesperson_code is None and quotation.price_list_customers == 2
    abrasive, gin = detail.lines
    # Compared against the list's price; the last sale goes apart, with its cost.
    assert abrasive.origin == "lista" and abrasive.last_price == Decimal("4568")
    assert abrasive.last_sale_price == Decimal("4400") and abrasive.last_sale_cost == 2950
    assert abrasive.last_sale_date == date(2026, 10, 20)
    assert abrasive.margin_basis == "mayoristas" and abrasive.target_margin_pct == Decimal("0.12")
    # A $1 placeholder is no price to compare.
    assert gin.last_price is None and "precio_marcador" in gin.problems
    assert gin.last_sale_price is None
    # The last sale is read for the list's active branches.
    call = next(item for item in warehouse.calls if item[0] == "list_last_sales")
    assert call[2] == ("1-900315138-001", "1-800111222-001")


async def test_every_support_of_the_list_is_shown_and_the_lowest_price_applies(
    service, lister, supports
) -> None:
    supports.clients = {
        NIT: CustomerScope(NIT, channels=WHOLESALE),
        "800111222": CustomerScope("800111222", channels=WHOLESALE),
    }
    general = agreement(supplier_value=Decimal("0.05"), product_sku="2537")
    one_customer = agreement(
        supplier_value=Decimal("0.10"), product_sku="2537", customer_nit="800111222"
    )
    elsewhere = agreement(supplier_value=Decimal("0.30"), product_sku="2537", customer_nit="999")
    review = agreement(
        supplier_value=Decimal("0.20"), product_sku="2537", status="pendiente_revision"
    )
    supports.items += [general, one_customer, elsewhere, review]

    detail = await service.create_list_update(lister, price_list_id="10U")

    line = detail.lines[0]
    options = {item.support_id: item for item in line.support_options}
    assert set(options) == {general.id, one_customer.id, review.id}
    # Cost 3,000, wholesale target 12 % (minimum 11 %).
    assert options[one_customer.id].min_price == Decimal(3034)
    assert options[one_customer.id].optimal_price == Decimal(3068)
    assert options[one_customer.id].customer_nit == "800111222"
    assert options[general.id].min_price == Decimal(3203)
    assert options[general.id].optimal_price == Decimal(3239)
    assert options[review.id].reason == "Apoyo en revisión" and not options[review.id].applied
    assert options[one_customer.id].applied and line.support.support_id == one_customer.id
    assert line.min_price == Decimal(3034) and line.support.amount == Decimal(300)
    assert line.support_options[0].support_id == one_customer.id
    assert "apoyo_en_revision" in line.problems


async def test_the_main_lists_only_go_through_listas_principales(service, lister, approver) -> None:
    # Not from Actualización de listas, not even for its validators.
    with pytest.raises(ForbiddenError, match="lista principal"):
        await service.create_list_update(lister, price_list_id="001")
    with pytest.raises(ForbiddenError, match="lista principal"):
        await service.create_list_update(approver, price_list_id="001")
    # Listas principales takes only those.
    collaborator = person(MAIN_MANAGER)
    with pytest.raises(InvalidInputError, match="no es una lista principal"):
        await service.create_list_update(collaborator, price_list_id="10U", kind="principal")
    # Managing Actualización de listas does not open Listas principales.
    with pytest.raises(ForbiddenError, match="listas principales"):
        await service.create_list_update(lister, price_list_id="001", kind="principal")

    detail = await service.create_list_update(collaborator, price_list_id="001", kind="principal")

    assert detail.quotation.kind == "principal" and detail.quotation.price_list_id == "001"
    found = await service.price_lists(lister, "001")
    assert [item.code for item in found] == ["001"]
    assert not service.list_allowed("lista", "001") and service.list_allowed("principal", "001")
    main = await service.price_lists(collaborator, None, "principal")
    assert [item.code for item in main] == ["001"]


async def test_by_customer_it_updates_its_list_with_its_own_last_sales(
    service, lister, warehouse
) -> None:
    warehouse.branch_rows[NIT] = [branch(price_list_id="10U", price_list_name="MAYORISTAS")]

    detail = await service.create_list_update(lister, customer_nit="900.315.138-1")

    quotation = detail.quotation
    assert quotation.source == "cliente" and quotation.customer_nit == NIT
    assert quotation.price_list_id == "10U" and quotation.customer_name == "HOTEL EL BOSQUE SAS"
    call = next(item for item in warehouse.calls if item[0] == "list_last_sales")
    assert call[2] == ("1-900315138-001",)


async def test_a_slow_last_sale_read_does_not_stop_the_update(service, lister, warehouse) -> None:
    warehouse.sales_down = True

    detail = await service.create_list_update(lister, price_list_id="10U")

    assert "ultima_venta_no_leida" in detail.quotation.warnings
    assert detail.lines[0].last_price == Decimal("4568")
    assert detail.lines[0].last_sale_price is None


async def test_list_updates_and_quotations_are_apart(service, lister, advisor) -> None:
    await service.create_list_update(lister, price_list_id="10U")
    await service.create(advisor, NIT, 12)

    updates = await service.list(
        lister, mine=False, status=None, customer_nit=None, q=None, page=1, size=20, kind="lista"
    )
    quotations = await service.list(
        advisor, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )

    assert [item.kind for item in updates.items] == ["lista"]
    assert [item.kind for item in quotations.items] == ["cotizacion"]
    # Managing list updates only does not open the quoter.
    with pytest.raises(ForbiddenError):
        await service.list(
            lister, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
        )
    permissions = await service.permissions(lister, "lista")
    assert permissions.can_manage and not permissions.can_validate


async def test_a_list_update_has_no_offer_quantities_or_new_list(service, lister, approver) -> None:
    detail = await service.create_list_update(lister, price_list_id="10U")
    quotation = detail.quotation

    with pytest.raises(InvalidInputError, match="no tiene oferta"):
        await service.update_offer(lister, quotation.id, quotation.version, quote_quantities=True)
    with pytest.raises(InvalidInputError, match="no tiene oferta"):
        await service.update_lines(
            lister,
            quotation.id,
            quotation.version,
            [LineChange("2537", quantity=Decimal(3), set_price=False, set_quantity=True)],
        )
    priced, _ = await service.update_lines(
        lister, quotation.id, quotation.version, [LineChange("2537", Decimal(4700))]
    )
    # Reviewed item by item: never confirmed or validated as a whole.
    with pytest.raises(InvalidInputError, match="se valida por ítem"):
        await service.confirm(
            lister,
            quotation.id,
            priced.quotation.version,
            list_action=None,
            new_list_name=None,
            activation_date=TODAY + timedelta(days=1),
        )
    with pytest.raises(InvalidInputError, match="se valida por ítem"):
        await service.validate(approver, quotation.id, priced.quotation.version)


async def test_refresh_keeps_the_new_prices_and_the_added_skus(service, lister, warehouse) -> None:
    detail = await service.create_list_update(lister, price_list_id="10U")
    quotation = detail.quotation
    priced, _ = await service.update_lines(
        lister, quotation.id, quotation.version, [LineChange("2537", Decimal(4700))]
    )
    warehouse.item_rows["31000"] = ItemInfo("31000", "Nuevo", "UNID", Decimal(19), "359")
    added, result = await service.add_lines(
        lister, quotation.id, priced.quotation.version, ["31000", "9999"]
    )
    assert result.added == ("31000",) and result.inactive == ("9999",)
    assert next(line for line in added.lines if line.sku == "31000").origin == "agregado"

    refreshed = await service.refresh(lister, quotation.id, added.quotation.version)

    by_sku = {line.sku: line for line in refreshed.lines}
    assert by_sku["2537"].new_price == Decimal(4700) and by_sku["2537"].origin == "lista"
    assert by_sku["31000"].origin == "agregado"
    with pytest.raises(InvalidInputError, match="deja vacío su precio nuevo"):
        await service.remove_line(lister, quotation.id, refreshed.quotation.version, "2537")
