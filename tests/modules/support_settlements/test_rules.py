"""The settlement rules, with the cases of BI's September 2026 workbooks."""

from __future__ import annotations

from datetime import date

from app.modules.support_settlements.domain.entities import (
    COST_INVENTORY,
    COST_QUOTE,
    IN_REVIEW,
    INVOICE,
    INVOICE_COST,
    MINIMUM_NOT_MET,
    PARTIAL_DISCOUNT,
    QUOTE,
    SETTLED,
    SEVERAL_SUPPORTS,
    TIERS,
)
from app.modules.support_settlements.domain.rules import (
    build_settlement,
    quote_on,
    recomposed_margin,
)
from tests.modules.support_settlements.fakes import (
    SEPTEMBER,
    D,
    customer,
    product,
    quote,
    sale,
    support,
)


def settle(lines, supports, *, products=None, customers=None, quotes=None, basis=COST_QUOTE):
    return build_settlement(
        sales=lines,
        supports=supports,
        products={item.sku: item for item in (products or [product()])},
        customers={item.customer_key: item for item in (customers or [customer()])},
        quotes=quotes if quotes is not None else {"18369": [quote("9142.67")]},
        cost_basis=basis,
    )


def test_the_inventory_basis_settles_on_the_cost_billed() -> None:
    # The same line: 45 × (443.355 / 45 − 79.266,92 / 9), the invoice's cost, not marked.
    result = settle([sale()], [support()], basis=COST_INVENTORY)

    line = result.sales[0]
    assert line.cost_source == INVOICE and INVOICE_COST not in line.flags
    assert line.base_unit_cost == D("443355") / D("45")
    assert round(line.amount, 2) == round(D("443355") - 45 * D("79266.92") / 9, 2)


def test_a_return_subtracts_with_the_inventory_basis_too() -> None:
    returned = sale(
        quantity=D("-9"), gross=D("-94000"), net=D("-94000"), cost=D("-88671"), is_return=True
    )

    result = settle([returned], [support()], basis=COST_INVENTORY)

    # 88.671 / 9 = 9.852,33 per unit; nine units back take the support back.
    assert round(result.sales[0].amount, 2) == round(-9 * (D("9852.333333") - D("8807.435556")), 2)


def test_the_list_price_is_the_food_service_margin_whatever_the_channel() -> None:
    # 5 % on the list price: cost / (1 − Food Service 16 %), not the Mayoristas 10 %.
    on_list = support(calc_method="pct_precio_lista", supplier_value=D("0.05"), value_unit=None)
    item = product(margins={"margin_list": D("0.16"), "margin_wholesale": D("0.10")})
    wholesaler = customer(channel="MAYORISTAS Y DISTRIBUIDORES")
    price = D("9142.67") / D("0.84")
    # Sold at the negotiated price: the list price minus the 5 %.
    line = sale(net=price * D("0.95") * 45, gross=price * D("0.95") * 45)

    quoted = settle([line], [on_list], products=[item], customers=[wholesaler])
    billed = settle(
        [line], [on_list], products=[item], customers=[wholesaler], basis=COST_INVENTORY
    )

    assert quoted.sales[0].amount == (price * D("0.05") * 45).quantize(D("0.01"))
    assert quoted.sales[0].items[0].detail == "5.0000% sobre el precio lista 10,884.13"
    # With the inventory cost the list price is 443.355 / 45 / 0,84 = 11.728,97.
    assert "sobre el precio lista 11,728.97" in billed.sales[0].items[0].detail


def test_a_guaranteed_margin_pays_the_cost_minus_the_supported_cost() -> None:
    # 20 % guaranteed at the price sold 10.000: supported cost 8.000; cost 9.142,67 → 1.142,67.
    guaranteed = support(
        calc_method="precio_neto",
        supplier_value=None,
        value_unit=None,
        target_margin_pct=D("0.20"),
    )
    line = sale(quantity=D("10"), net=D("100000"), gross=D("100000"), cost=D("95000"))

    quoted = settle([line], [guaranteed])
    billed = settle([line], [guaranteed], basis=COST_INVENTORY)

    assert quoted.sales[0].amount == D("11426.70")
    assert "costo 9,142.67 − costo apoyado 8,000.00" in quoted.sales[0].items[0].detail
    assert billed.sales[0].amount == D("15000.00")


def test_familia_supported_cost_settles_on_the_supplier_quotation() -> None:
    # Familia 18369, FEV-17614: BI's note is 15.085,55 = 45 × (9.142,67 − 79.266,92 / 9).
    result = settle([sale()], [support()])

    line = result.sales[0]
    assert line.cost_source == QUOTE and line.base_unit_cost == D("9142.67")
    assert round(line.amount, 2) == D("15085.55")
    assert line.items[0].status == SETTLED


def test_without_quotation_the_invoice_cost_is_used_and_marked() -> None:
    result = settle([sale()], [support()], quotes={})

    line = result.sales[0]
    assert line.cost_source == INVOICE and INVOICE_COST in line.flags
    assert line.base_unit_cost == D("443355") / D("45")


def test_a_return_subtracts() -> None:
    returned = sale(
        quantity=D("-9"),
        gross=D("-94000"),
        net=D("-94000"),
        cost=D("-88671"),
        is_return=True,
        document="NDE-1",
    )

    result = settle([sale(), returned], [support()])

    amounts = sorted(line.amount for line in result.sales)
    assert amounts[0] < 0 and round(sum(amounts), 2) == round(
        D("36") * (D("9142.67") - D("79266.92") / 9), 2
    )


def test_percent_on_cost_drops_by_the_discount_not_given() -> None:
    # Team 9951: 7 % on the quotation 183.524; list price at the 16 % margin 218.480,95.
    team = support(
        supplier_code="8",
        product_sku="9951",
        calc_method="pct_costo",
        supplier_value=D("0.07"),
        value_unit="unidad",
        pack_factor=None,
    )
    item = product(sku="9951", supplier_code="8", margins={"margin_list": D("0.16")})
    quotes = {"9951": [quote("183524", sku="9951")]}
    negotiated = D("183524") / D("0.84") - D("183524") * D("0.07")
    full = sale(
        sku="9951", quantity=D("2"), net=negotiated * 2, gross=negotiated * 2, cost=D("379437")
    )
    partial = sale(
        sku="9951", quantity=D("2"), net=D("428000"), gross=D("428000"), cost=D("379437")
    )

    result = settle([full, partial], [team], products=[item], quotes=quotes)

    by_net = {line.line.net: line for line in result.sales}
    whole = by_net[full.net].items[0]
    assert whole.settled_pct == D("0.07") and PARTIAL_DISCOUNT not in by_net[full.net].flags
    cut = by_net[D("428000")]
    assert PARTIAL_DISCOUNT in cut.flags
    granted = 1 - D("214000") / (D("183524") / D("0.84"))
    expected = D("0.07") - ((1 - (negotiated / (D("183524") / D("0.84")))) - granted)
    assert abs(cut.items[0].settled_pct - expected) < D("0.000001")
    assert cut.amount == (D("183524") * cut.items[0].settled_pct * 2).quantize(D("0.01"))


def test_copies_of_one_support_count_once_and_different_ones_add_up() -> None:
    group_copy = support(customer_nit="811000000", customer_group="SANCELA", group_wide=True)
    customer_copy = support(customer_nit="900973779", customer_group="SANCELA", group_wide=True)
    branch = customer(business_group="SANCELA")

    once = settle([sale()], [group_copy, customer_copy], customers=[branch])

    assert len(once.sales[0].items) == 1
    assert once.sales[0].items[0].support_id == customer_copy.id
    assert SEVERAL_SUPPORTS not in once.sales[0].flags

    other = support(calc_method="valor_unidad", supplier_value=D("100"), value_unit="unidad")
    twice = settle([sale()], [customer_copy, other], customers=[branch])

    assert len(twice.sales[0].items) == 2 and SEVERAL_SUPPORTS in twice.sales[0].flags
    assert round(twice.sales[0].amount, 2) == round(D("15085.55") + D("4500"), 2)


def test_a_supplier_margin_request_copy_replaces_its_base() -> None:
    base = support(calc_method="valor_unidad", supplier_value=D("100"), value_unit="unidad")
    copy = support(
        calc_method="valor_unidad",
        supplier_value=D("300"),
        value_unit="unidad",
        replaces_support_id=base.id,
    )

    result = settle([sale()], [base, copy])

    assert [item.support_id for item in result.sales[0].items] == [copy.id]


def test_the_scope_is_the_branch_sold_to() -> None:
    antioquia = support(regionals=("ANTIOQUIA",))
    occidente = customer(customer_key="900973779-002", branch_id="002", billing_center="OCCIDENTE")

    elsewhere = settle(
        [sale(customer_key="900973779-002", branch_id="002")],
        [antioquia],
        customers=[customer(), occidente],
    )
    here = settle([sale()], [antioquia], customers=[customer(), occidente])

    assert elsewhere.sales == () and len(here.sales) == 1


def test_validity_is_the_sale_day() -> None:
    later = support(valid_from=date(2026, 10, 1))

    assert settle([sale()], [later]).sales == ()


def test_minimum_quantity_is_the_customer_month() -> None:
    minimum = support(min_quantity=D("100"))

    short = settle([sale(), sale()], [minimum])
    enough = settle([sale(), sale(), sale()], [minimum])

    assert {item.status for line in short.sales for item in line.items} == {MINIMUM_NOT_MET}
    assert sum(line.amount for line in short.sales) == 0
    assert {item.status for line in enough.sales for item in line.items} == {SETTLED}


def test_supports_in_review_or_with_tiers_are_listed_not_claimed() -> None:
    review = settle([sale()], [support(status="pendiente_revision")])
    tiers = settle([sale()], [support(has_tiers=True)])

    assert review.sales[0].items[0].status == IN_REVIEW and review.sales[0].amount == 0
    assert tiers.sales[0].items[0].status == TIERS and tiers.sales[0].amount == 0
    assert review.references[0].flagged_amount > 0


def test_lines_without_a_sale_are_not_settled_and_the_month_counts_every_sale() -> None:
    gift = sale(net=D("0"), gross=D("0"), document="FEV-GIFT")
    unsupported = sale(nit="111", customer_key="111-001", document="FEV-OTHER")

    result = settle([sale(), gift, unsupported], [support()])

    assert [line.line.document for line in result.sales] == ["FEV-17614"]
    reference = result.references[0]
    assert reference.net == D("940000") and reference.supported_lines == 1
    assert reference.cost == D("443355") * 3


def test_a_value_per_kilo_uses_the_kilos_sold() -> None:
    kilo = support(calc_method="valor_unidad", supplier_value=D("1000"), value_unit="kg")

    result = settle([sale(kg=D("90"))], [kilo])

    assert result.sales[0].amount == D("90000")


def test_a_supported_cost_above_the_cost_pays_nothing() -> None:
    above = support(supplier_value=D("99000"))

    assert settle([sale()], [above]).sales[0].amount == 0


def test_the_quotation_of_the_day_and_center() -> None:
    quotes = [
        quote("100", since=date(2026, 1, 1)),
        quote("120", since=date(2026, 9, 1)),
        quote("130", since=date(2026, 9, 1), co="002"),
        quote("150", since=date(2026, 9, 20)),
    ]

    assert quote_on(quotes, SEPTEMBER, "001").cost == D("120")
    assert quote_on(quotes, SEPTEMBER, "002").cost == D("130")
    assert quote_on(quotes, date(2026, 9, 25), "001").cost == D("150")
    assert quote_on(quotes, date(2025, 1, 1), "001") is None


def test_recomposed_margin() -> None:
    assert recomposed_margin(D("100"), D("90"), D("8")) == D("0.18")
    assert recomposed_margin(D("0"), D("90"), D("8")) is None


def test_only_supplier_classes_are_settled() -> None:
    exception = support(kind="excepcion_margen", calc_method="margen_minimo")
    jdh = support(funding="jdh")

    assert settle([sale()], [exception, jdh]).sales == ()


def test_a_supplier_wide_support_covers_its_supplier_products_only() -> None:
    wide = support(
        product_sku=None, calc_method="pct_costo", supplier_value=D("0.05"), value_unit="unidad"
    )
    other = product(sku="777", supplier_code="999")

    result = settle(
        # Billed under the negotiated price: the whole % is paid.
        [sale(net=D("400000")), sale(sku="777", document="FEV-777")],
        [wide],
        products=[product(), other],
        quotes={"18369": [quote("9142.67")], "777": [quote("1000", sku="777")]},
    )

    assert [line.line.sku for line in result.sales] == ["18369"]
    assert result.sales[0].amount == (D("9142.67") * D("0.05") * 45).quantize(D("0.01"))
