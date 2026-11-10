"""Billing centers, temporary customers and the offer's variation."""

import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain import rules
from app.modules.quotations.domain.entities import TemporaryCustomer, ValueTaxKey
from app.modules.salespeople.domain.entities import NOTHING
from tests.modules.quotations.conftest import NIT, branch, product
from tests.modules.supplier_supports.conftest import person


def confirm(service, principal, quotation_id, version, **values):
    options = {"list_action": None, "new_list_name": None, "activation_date": None}
    return service.confirm(principal, quotation_id, version, **{**options, **values})


def local(service, principal, quotation_id, version, **values):
    fields = {"description": "Pan tajado", "supplier_name": None, "price": 12000, "vat_pct": 5}
    return service.add_local_line(principal, quotation_id, version, **{**fields, **values})


# ------------------------------------------------------------------ 1. initial center


def test_a_coast_center_among_the_branches_wins() -> None:
    coast = [
        branch(billing_center_code="001"),
        branch(billing_center_code="001"),
        branch(billing_center_code="004"),
    ]

    assert rules.billing_center(coast) == ("004", True)
    assert rules.billing_center([branch(), branch(billing_center_code="003"), branch()]) == (
        "001",
        True,
    )
    assert rules.billing_center([branch(billing_center_code=None)]) == (None, False)
    context = rules.customer_context(NIT, coast)
    assert context.billing_center_code == "004" and "co_distintos" in context.warnings
    # List 001 has its own warehouses: the coast center costs it, not Nacional.
    assert context.cost_centers == ("004",)


# ------------------------------------------------------------------ 2. selectable center


async def test_billing_centers_are_listed_by_code(service, advisor) -> None:
    centers = await service.billing_centers(advisor)

    assert [(item.code, item.name) for item in centers] == [
        ("001", "ANTIOQUIA"),
        ("003", "CENTRO"),
        ("004", "COSTA"),
    ]
    with pytest.raises(ForbiddenError):
        await service.billing_centers(person("otro@jdh.com.co"))


async def test_choosing_a_center_reprices_and_survives_a_refresh(
    service, advisor, approver, warehouse, masters
) -> None:
    masters.catalog["2537"] = product(liquor_tax=Decimal(1000), liquor_tax_coast=Decimal(1500))
    warehouse.taxes["2537"] = ValueTaxKey("2537", "0024", "LICORES NAL")
    created = await service.create(advisor, NIT, 12)
    quotation = created.quotation
    assert quotation.billing_center_source == "auto"
    assert quotation.billing_center_name == "ANTIOQUIA"
    assert created.lines[0].value_tax_amount == Decimal(1000)
    await service.update_lines(
        advisor,
        quotation.id,
        1,
        [LineChange("2537", Decimal(4100), quantity="3", set_quantity=True)],
    )
    await local(service, advisor, quotation.id, 2)
    await confirm(service, advisor, quotation.id, 3)
    with pytest.raises(ConflictError, match="pendiente"):
        await service.set_billing_center(advisor, quotation.id, 4, ["004"])
    await service.validate(approver, quotation.id, 4)
    with pytest.raises(InvalidInputError, match="no existe"):
        await service.set_billing_center(advisor, quotation.id, 5, ["999"])
    with pytest.raises(ConflictError):
        await service.set_billing_center(advisor, quotation.id, 4, ["004"])

    changed = await service.set_billing_center(advisor, quotation.id, 5, [" 004 "])

    result = changed.quotation
    assert result.status == "borrador" and result.version == 6
    assert result.cost_centers == ("004",)
    # The customer still bills from its own center; only the cost moves.
    assert result.billing_center_code == "001" and result.billing_center_name == "ANTIOQUIA"
    assert result.billing_center_source == "manual" and result.regional == "costa"
    assert result.uploaded_at is None and result.validated_at is None
    line, kept = changed.lines
    # The Coast pays its own liquor tax; the price and the quantity stay.
    assert line.value_tax_amount == Decimal(1500)
    assert line.new_price == Decimal(4100) and line.quantity == 3
    assert kept.sku == "LOC-1" and kept.new_price == Decimal(12000)

    refreshed = await service.refresh(advisor, quotation.id, 6)
    assert refreshed.quotation.cost_centers == ("004",)
    assert refreshed.quotation.billing_center_source == "manual"
    assert refreshed.lines[0].value_tax_amount == Decimal(1500)


async def test_a_center_can_be_chosen_when_quoting(service, advisor) -> None:
    detail = await service.create(advisor, NIT, 12, billing_center_code="003")

    assert detail.quotation.billing_center_code == "003"
    assert detail.quotation.cost_centers == ("003",)
    assert detail.quotation.billing_center_source == "manual"
    assert detail.quotation.billing_center_name == "CENTRO"
    with pytest.raises(InvalidInputError, match="no existe"):
        await service.create(advisor, NIT, 12, billing_center_code="777")


# ------------------------------------------------------------------ 6. temporary customer


class NobodysCustomers:
    """A sales scope that gives the advisor no customer at all."""

    async def scope_for(self, principal):
        return NOTHING


async def test_a_temporary_customer_starts_empty_and_goes_by_creation(
    service, advisor, approver, warehouse
) -> None:
    service.sales = NobodysCustomers()
    customer = TemporaryCustomer(
        name="  Panadería   La Espiga ", legal_name="La Espiga SAS", nit="900.123.456-7"
    )

    def create(**values):
        return service.create(advisor, values.pop("nit", None), 12, **values)

    with pytest.raises(InvalidInputError, match="centro de operación del cliente temporal"):
        await create(temporary_customer=customer)
    with pytest.raises(InvalidInputError, match="no los dos"):
        await create(nit=NIT, temporary_customer=customer, billing_center_code="004")
    with pytest.raises(InvalidInputError, match="NIT del cliente o los datos"):
        await create()
    with pytest.raises(InvalidInputError, match="razón social"):
        await create(temporary_customer=TemporaryCustomer("Espiga", " "), billing_center_code="004")
    with pytest.raises(InvalidInputError, match="máximo 120"):
        await create(
            temporary_customer=TemporaryCustomer("x" * 121, "Espiga SAS"),
            billing_center_code="004",
        )
    with pytest.raises(InvalidInputError, match="no existe"):
        await create(temporary_customer=customer, billing_center_code="999")
    # The sales scope does not apply to a temporary customer (it does to a SIESA one).
    with pytest.raises(ForbiddenError):
        await create(nit=NIT)

    created = await create(temporary_customer=customer, billing_center_code="004")

    quotation = created.quotation
    assert created.lines == () and quotation.is_temporary_customer
    assert quotation.customer_name == "Panadería La Espiga"
    assert quotation.customer_nit == "900123456"
    assert quotation.temporary_customer == TemporaryCustomer(
        "Panadería La Espiga", "La Espiga SAS", None, "900123456"
    )
    assert quotation.price_list_id is None and quotation.channel is None
    assert quotation.list_action == "creacion" and quotation.regional == "costa"
    assert quotation.billing_center_source == "manual" and quotation.seller_contact is None
    assert not any(call[0] == "last_sales" for call in warehouse.calls)

    added, _ = await service.add_lines(advisor, quotation.id, 1, ["26278"])
    plan = [call[2] for call in warehouse.calls if call[0] == "costs"][-1]
    # Priced from the chosen center (Costa: CTG1 and its cold room), with no customer list.
    assert plan.warehouses == ("CTG1", "CTG15")
    assert not any(call[0] == "list_prices" for call in warehouse.calls)
    assert [line.sku for line in added.lines] == ["26278"]
    await local(service, advisor, quotation.id, 2)
    refreshed = await service.refresh(advisor, quotation.id, 3)
    assert [line.sku for line in refreshed.lines] == ["26278", "LOC-1"]
    with pytest.raises(InvalidInputError, match="cliente temporal"):
        await confirm(service, advisor, quotation.id, 4, list_action="actualizacion")

    # Only a local product has a price: that is enough to confirm.
    pending = await confirm(service, advisor, quotation.id, 4)
    assert pending.quotation.list_action == "creacion"
    validated = await service.validate(approver, quotation.id, 5, price_list_id="c10")
    assert validated.quotation.export_price_list_id == "C10"
    assert validated.quotation.new_list_name == "COTIZADOR PANADERIA LA ESPIGA"
    lines = (await service.export(advisor, quotation.id, "txt")).content.split(b"\r\n")
    assert [len(item) for item in lines] == [18, 101, 18, 0]


# ------------------------------------------------------------------ 7. offer variation


async def test_the_offer_shows_the_consumption_tax_and_the_variation(
    service, advisor, warehouse, masters
) -> None:
    masters.catalog["2537"] = product(liquor_tax=Decimal(1000))
    warehouse.taxes["2537"] = ValueTaxKey("2537", "0024", "LICORES NAL")
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal(4400))])

    def table(content: bytes) -> tuple[list, list]:
        rows = list(load_workbook(io.BytesIO(content)).active.iter_rows(values_only=True))
        header = next(row for row in rows if row and row[0] == "Referencia")
        values = rows[rows.index(header) + 1]
        return list(header), list(values)

    header, values = table((await service.offer_file(advisor, quotation.id)).content)
    assert "Variación" not in header
    assert values[header.index("Imp. consumo")] == 1000

    changed = await service.update_offer(advisor, quotation.id, 2, include_variation=True)

    assert changed.quotation.include_variation and changed.quotation.status == "borrador"
    header, values = table((await service.offer_file(advisor, quotation.id)).content)
    assert header.index("Variación") == header.index("Precio con IVA") + 1
    # 4400 / 4000 - 1.
    assert values[header.index("Variación")] == pytest.approx(0.1)
