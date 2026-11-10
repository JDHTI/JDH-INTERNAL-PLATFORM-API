"""The customer offer: validity, settings, seller, quantities, totals and its workbook."""

import io
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain import offer
from app.modules.quotations.domain.entities import SellerContact, ValueTaxKey
from tests.modules.quotations.conftest import NIT, branch, cost, product, sale
from tests.modules.supplier_supports.conftest import person

TODAY = date(2026, 10, 9)


def quantity(sku: str, value) -> LineChange:
    return LineChange(sku, quantity=value, set_price=False, set_quantity=True)


async def confirm(service, principal, quotation_id, version):
    return await service.confirm(
        principal,
        quotation_id,
        version,
        list_action=None,
        new_list_name=None,
        activation_date=None,
    )


async def local(service, principal, quotation_id, version, **values):
    fields = {"description": "Queso costeño", "supplier_name": None, "price": 12000, "vat_pct": 5}
    return await service.add_local_line(principal, quotation_id, version, **{**fields, **values})


# ------------------------------------------------------------------ 1. validity


async def test_a_quotation_holds_its_validity_from_today(service, advisor) -> None:
    default = (await service.create(advisor, NIT, 12)).quotation
    short = (await service.create(advisor, NIT, 12, validity_days=15)).quotation

    assert default.validity_days == 30 and default.valid_from == TODAY
    assert default.valid_until == date(2026, 11, 8)
    assert short.valid_until == date(2026, 10, 24)
    for days in (0, 366):
        with pytest.raises(InvalidInputError, match="vigencia"):
            await service.create(advisor, NIT, 12, validity_days=days)


# ------------------------------------------------------------------ 2. offer settings


async def test_offer_settings_change_only_what_is_sent(service, advisor, repository) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    stored = repository.items[quotation.id]
    repository.items[quotation.id] = replace(
        stored, quotation=replace(stored.quotation, valid_from=date(2026, 9, 1))
    )

    detail = await service.update_offer(
        advisor,
        quotation.id,
        1,
        conditions=[" Pago a 30 días ", "", "   ", "Entrega en 48 horas"],
        include_seller=False,
    )

    result = detail.quotation
    assert result.conditions == ("Pago a 30 días", "Entrega en 48 horas")
    assert not result.include_seller and not result.quote_quantities
    assert result.offer_scope == "changed" and result.version == 2
    # The validity did not change, so neither did its start.
    assert result.valid_from == date(2026, 9, 1) and result.status == "borrador"
    renewed = await service.update_offer(
        advisor, quotation.id, 2, validity_days=10, quote_quantities=True, offer_scope="all"
    )
    assert renewed.quotation.valid_from == TODAY
    assert renewed.quotation.valid_until == date(2026, 10, 19)
    assert renewed.quotation.quote_quantities and renewed.quotation.offer_scope == "all"
    assert renewed.quotation.conditions == ("Pago a 30 días", "Entrega en 48 horas")


async def test_offer_settings_are_checked(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(ConflictError):
        await service.update_offer(advisor, quotation.id, 2, include_seller=False)
    with pytest.raises(InvalidInputError, match="20 condiciones"):
        await service.update_offer(advisor, quotation.id, 1, conditions=["a"] * 21)
    with pytest.raises(InvalidInputError, match="condición 2"):
        await service.update_offer(advisor, quotation.id, 1, conditions=["a", "x" * 301])
    with pytest.raises(InvalidInputError, match="vigencia"):
        await service.update_offer(advisor, quotation.id, 1, validity_days=400)
    with pytest.raises(InvalidInputError, match="changed o all"):
        await service.update_offer(advisor, quotation.id, 1, offer_scope="some")
    with pytest.raises(ForbiddenError):
        await service.update_offer(person(advisor.email), quotation.id, 1, include_seller=False)


async def test_offer_settings_change_in_any_status_without_moving_it(
    service, advisor, approver
) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])
    pending = await confirm(service, advisor, quotation.id, 2)

    changed = await service.update_offer(advisor, quotation.id, 3, conditions=["Pago contado"])

    assert changed.quotation.status == "pendiente_validacion" and changed.quotation.version == 4
    assert changed.quotation.confirmed_at == pending.quotation.confirmed_at
    validated = await service.validate(approver, quotation.id, 4)
    closed = await service.update_offer(advisor, quotation.id, 5, quote_quantities=True)
    assert closed.quotation.status == "pendiente_carga" == validated.quotation.status


# ------------------------------------------------------------------ 3. seller contact


async def test_the_customer_salesperson_is_kept_with_the_quotation(
    service, advisor, warehouse
) -> None:
    created = (await service.create(advisor, NIT, 12)).quotation
    assert created.seller_contact == SellerContact(
        "0051", "PEREZ ANA", "asesor@jdh.com.co", "3001234567"
    )

    warehouse.contacts["0051"] = SellerContact("0051", "PEREZ ANA", None, "3109999999")
    refreshed = await service.refresh(advisor, created.id, 1)
    assert refreshed.quotation.seller_contact.phone == "3109999999"

    warehouse.branch_rows[NIT] = [branch(salesperson_code=None)]
    assert (await service.create(advisor, NIT, 12)).quotation.seller_contact is None
    warehouse.branch_rows[NIT] = [branch(salesperson_code="0777")]
    assert (await service.create(advisor, NIT, 12)).quotation.seller_contact is None


# ------------------------------------------------------------------ 4. quantities


async def test_quantities_alone_never_touch_prices_or_status(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])
    await confirm(service, advisor, quotation.id, 2)
    # Pending validation: prices are locked, quantities are not.
    with pytest.raises(ConflictError, match="pendiente"):
        await service.update_lines(advisor, quotation.id, 3, [LineChange("2537", Decimal(1))])

    detail, skipped = await service.update_lines(
        advisor, quotation.id, 3, [quantity("2537", "12.5")]
    )

    line = detail.lines[0]
    assert skipped == () and line.quantity == Decimal("12.50")
    assert line.new_price == Decimal(4100) and line.compliance == "cumple"
    assert detail.quotation.status == "pendiente_validacion" and detail.quotation.version == 4
    with pytest.raises(ConflictError):
        await service.update_lines(advisor, quotation.id, 3, [quantity("2537", 1)])
    cleared, _ = await service.update_lines(advisor, quotation.id, 4, [quantity("2537", None)])
    assert cleared.lines[0].quantity is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0", "mayor que cero"),
        (-1, "mayor que cero"),
        ("1.234", "dos decimales"),
        ("10000000.01", "máximo"),
        ("abc", "número"),
    ],
)
async def test_quantities_are_checked(service, advisor, value, message) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(InvalidInputError, match=message):
        await service.update_lines(advisor, quotation.id, 1, [quantity("2537", value)])


async def test_quantities_go_with_prices_and_survive_rebuilds(
    service, advisor, warehouse, masters
) -> None:
    masters.catalog["26278"] = product("26278")
    warehouse.cost_rows["26278"] = cost("26278", inventory="60000")
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await local(service, advisor, quotation.id, 1)

    detail, _ = await service.update_lines(
        advisor,
        quotation.id,
        2,
        [
            LineChange("2537", Decimal(4100), quantity="10", set_quantity=True),
            quantity("LOC-1", 3),
        ],
    )

    by_sku = {line.sku: line for line in detail.lines}
    assert by_sku["2537"].new_price == Decimal(4100) and by_sku["2537"].quantity == 10
    assert by_sku["LOC-1"].quantity == 3 and by_sku["LOC-1"].new_price == Decimal(12000)
    with pytest.raises(InvalidInputError, match="margen y cantidad"):
        await service.update_lines(
            advisor,
            quotation.id,
            3,
            [LineChange("2537", margin_pct=Decimal("0.3"), quantity="1", set_quantity=True)],
        )
    refreshed = await service.refresh(advisor, quotation.id, 3)
    again, _ = await service.add_lines(advisor, quotation.id, 4, ["2537"])
    for current in (refreshed, again):
        quantities = {line.sku: line.quantity for line in current.lines}
        assert quantities == {"2537": Decimal(10), "LOC-1": Decimal(3)}


# ------------------------------------------------------------------ 5. offer lines and totals


async def offer_quotation(service, advisor, warehouse, masters):
    """2537 at 4100 (VAT 19), 1485 without a new price, a local product at 12000 (VAT 5) and
    26278 at 80000 (VAT 5, liquor tax 42451)."""

    warehouse.sales[NIT].append(sale("1485", "2000"))
    masters.catalog["1485"] = product("1485")
    masters.catalog["26278"] = product("26278", vat_pct=Decimal(5), liquor_tax=Decimal(42451))
    warehouse.cost_rows["26278"] = cost("26278", inventory="60000")
    warehouse.taxes["26278"] = ValueTaxKey("26278", "0024", "LICORES NAL")
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.add_lines(advisor, quotation.id, 1, ["26278"])
    await local(service, advisor, quotation.id, 2)
    detail, _ = await service.update_lines(
        advisor,
        quotation.id,
        3,
        [
            LineChange("2537", Decimal(4100), quantity="10", set_quantity=True),
            LineChange("26278", Decimal(80000), quantity="1.5", set_quantity=True),
            quantity("LOC-1", 2),
            quantity("1485", 3),
        ],
    )
    return detail


async def test_offer_lines_follow_the_scope(service, advisor, warehouse, masters) -> None:
    detail = await offer_quotation(service, advisor, warehouse, masters)
    by_sku = {line.sku: line for line in detail.lines}

    assert offer.offer_price(by_sku["1485"]) == Decimal("2000.0000")
    assert offer.offer_price(by_sku["2537"]) == Decimal(4100)
    assert [line.sku for line in offer.offer_lines(detail)] == ["2537", "26278", "LOC-1"]
    everything = await service.update_offer(
        advisor, detail.quotation.id, detail.quotation.version, offer_scope="all"
    )
    assert [line.sku for line in offer.offer_lines(everything)] == [
        "2537",
        "1485",
        "26278",
        "LOC-1",
    ]
    inactive = replace(by_sku["2537"], is_active=False)
    assert not offer.in_offer(inactive, "all") and not offer.in_offer(inactive, "changed")


async def test_offer_totals_come_from_quantities(service, advisor, warehouse, masters) -> None:
    detail = await offer_quotation(service, advisor, warehouse, masters)
    assert offer.offer_summary(detail) is None

    on = await service.update_offer(
        advisor, detail.quotation.id, detail.quotation.version, quote_quantities=True
    )

    summary = offer.offer_summary(on)
    # 2537: 10 x 4100 = 41000, VAT 7790. 26278: 1.5 x 80000 = 120000, VAT 6000, liquor
    # round(1.5 x 42451) = 63677. Local: 2 x 12000 = 24000, VAT 1200. 1485 is not offered.
    assert summary.item_count == 3
    assert [(group.vat_pct, group.base, group.vat) for group in summary.vat_groups] == [
        (Decimal(19), Decimal(41000), Decimal(7790)),
        (Decimal(5), Decimal(144000), Decimal(7200)),
    ]
    assert summary.subtotal == Decimal(185000) and summary.liquor_tax == Decimal(63677)
    assert summary.total == Decimal(263667)


# ------------------------------------------------------------------ 6. customer workbook


async def test_the_customer_workbook(service, advisor, approver, warehouse, masters) -> None:
    detail = await offer_quotation(service, advisor, warehouse, masters)
    quotation_id, version = detail.quotation.id, detail.quotation.version
    await service.update_offer(
        advisor, quotation_id, version, quote_quantities=True, conditions=["Pago a 30 días"]
    )

    # Whoever reads the quotation downloads it, in any status.
    result = await service.offer_file(approver, quotation_id)

    assert result.filename == "Cotizacion HOTEL EL BOSQUE SAS 2026-10-09.xlsx"
    sheet = load_workbook(io.BytesIO(result.content)).active
    rows = [[cell for cell in row if cell is not None] for row in sheet.iter_rows(values_only=True)]
    text = " ".join(str(cell) for row in rows for cell in row)
    assert rows[0] == ["Cotización"]
    assert ["Válida hasta", "8 de noviembre de 2026"] in rows
    assert ["Teléfono", "3001234567"] in rows
    header = next(row for row in rows if row and row[0] == "Referencia")
    assert header[-4:] == ["Cantidad", "Subtotal", "IVA", "Total"]
    items = rows[rows.index(header) + 1 : rows.index(header) + 4]
    assert [row[0] for row in items] == ["2537", "26278", "Por crear"]
    # 4100 x 1.19 = 4879; 80000 x 1.05 + 42451 = 126451.
    assert items[0][-5:] == [4879, 10, 41000, 7790, 48790]
    assert items[1][6] == 126451
    assert ["Total", 263667] in rows
    assert rows[-3:] == [
        ["Condiciones de la oferta"],
        ["• Precios válidos hasta el 8 de noviembre de 2026"],
        ["• Pago a 30 días"],
    ]
    for word in ("Costo", "Margen", "Apoyo", "mínimo", "óptimo", "cumple"):
        assert word not in text

    hidden = await service.update_offer(
        advisor, quotation_id, version + 1, include_seller=False, quote_quantities=False
    )
    plain = load_workbook(io.BytesIO((await service.offer_file(advisor, quotation_id)).content))
    values = [cell for row in plain.active.iter_rows(values_only=True) for cell in row if cell]
    assert "Teléfono" not in values and "Cantidad" not in values and "Total" not in values
    assert hidden.quotation.version == version + 2
    with pytest.raises(ForbiddenError):
        await service.offer_file(person("otro@jdh.com.co"), quotation_id)
