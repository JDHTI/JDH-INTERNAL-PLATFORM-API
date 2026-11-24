"""Quoting a whole business group (BI, 2026-11-19)."""

import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.quotations.application.service import _group_agreements
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, branch, sale
from tests.modules.supplier_supports.conftest import person

OTHER = "800125313"


@pytest.fixture
def group(warehouse, masters):
    masters.groups = {"QBANO": [NIT, OTHER], "SOLO OTRO": [OTHER]}
    # The other NIT has two active branches on list C01: it stands for the group.
    warehouse.branch_rows[OTHER] = [
        branch(branch_id="001", price_list_id="C01", price_list_name="QBANO", legal_name="QB 1"),
        branch(branch_id="002", price_list_id="C01", price_list_name="QBANO", legal_name="QB 2"),
    ]
    warehouse.sales[OTHER] = [sale("2537", price="4500")]
    return "QBANO"


async def test_the_groups_of_the_portfolio(service, advisor, group) -> None:
    page, linked = await service.business_groups(advisor, "mine", None, 1, 20)

    # The advisor (seller 0051) has a customer in QBANO, none in SOLO OTRO.
    assert [item.name for item in page.items] == ["QBANO"] and linked
    assert page.items[0].customers == 2
    every, _ = await service.business_groups(advisor, "all", "solo", 1, 20)
    assert [item.name for item in every.items] == ["SOLO OTRO"]


async def test_a_group_is_quoted_as_one_customer(service, advisor, warehouse, group) -> None:
    detail = await service.create(advisor, None, 12, business_group=" QBANO ")
    quotation = detail.quotation

    assert quotation.business_group == "QBANO" and quotation.group_customers == 2
    assert quotation.customer_name == "QBANO"
    # The majority of the branches decides: list C01 (2 branches against 1).
    assert quotation.price_list_id == "C01" and "listas_distintas" in quotation.warnings
    assert quotation.customer_nit == OTHER
    # The sales of the whole group, one line per SKU.
    assert ("group_last_sales", (NIT, OTHER), 12) in warehouse.calls
    assert [line.sku for line in detail.lines] == ["2537"]

    # A refresh rebuilds the group.
    refreshed = await service.refresh(advisor, quotation.id, 1)
    assert refreshed.quotation.business_group == "QBANO"
    assert refreshed.quotation.group_customers == 2

    # The offer goes to the group.
    offer_file = await service.offer_file(advisor, quotation.id)
    sheet = load_workbook(io.BytesIO(offer_file.content)).active
    labels = {row[0]: row[1] for row in sheet.iter_rows(values_only=True) if row and row[0]}
    assert labels["Grupo empresarial"] == "QBANO" and "NIT" not in labels


async def test_a_group_quotation_applies_the_group_support(
    service, advisor, supports, group
) -> None:
    """2026-11-24 (Newrest, SKU 12043): the group's quotation has no one customer, and its
    group's support (no NIT, the group's name) never applied."""

    supports.items = [
        agreement(
            supplier_code=None,
            product_sku="2537",
            customer_group="QBANO",
            calc_method="pct_costo",
            supplier_value=Decimal("0.05"),
        ),
        # One NIT's own support stays that customer's: not for the whole group.
        agreement(
            supplier_code=None,
            product_sku="2537",
            customer_nit=OTHER,
            customer_group="QBANO",
            calc_method="pct_costo",
            supplier_value=Decimal("0.30"),
        ),
    ]

    detail = await service.create(advisor, None, 12, business_group="QBANO")

    line = detail.lines[0]
    assert line.support is not None and line.support.support_id == supports.items[0].id


async def test_one_of_nit_group_or_temporary(service, advisor, group) -> None:
    with pytest.raises(InvalidInputError, match="uno solo"):
        await service.create(advisor, NIT, 12, business_group="QBANO")
    with pytest.raises(NotFoundError):
        await service.create(advisor, None, 12, business_group="NO EXISTE")


async def test_a_group_out_of_the_portfolio(service, warehouse, masters, group) -> None:
    class Scope:
        scope, seller_codes, supervisor_codes, codes = "mine", ("0077",), (), ("0077",)
        is_all = False

        def allows(self, code):
            return code in self.codes

    class Sales:
        async def scope_for(self, principal):
            return Scope()

    service.sales = Sales()
    with pytest.raises(ForbiddenError, match="no está en tu cartera"):
        await service.create(person("asesor@jdh.com.co"), None, 12, business_group="QBANO")


def test_only_the_general_and_the_group_supports_apply() -> None:
    general = agreement(supplier_value=Decimal("0.05"))
    one_nit = agreement(customer_nit=NIT, supplier_value=Decimal("0.10"))
    # The group's copies (group_wide) come from the source as one support with no NIT
    # (whole_groups); two equal ones collapse.
    copies = [
        agreement(customer_group="QBANO", supplier_value=Decimal("0.08")),
        agreement(customer_group="QBANO", supplier_value=Decimal("0.08")),
    ]
    # One NIT's own support carries its group too (the master fills it): it stays that
    # customer's and never covers the whole group (2026-11-23).
    nit_of_group = agreement(customer_nit=OTHER, customer_group="QBANO", supplier_value="0.3")
    other_group = agreement(customer_group="OTRO", supplier_value=Decimal("1"))

    kept = _group_agreements([general, one_nit, *copies, nit_of_group, other_group], "qbano")

    assert len(kept) == 2 and kept[0] == general
    whole = kept[1]
    assert whole.customer_nit is None and whole.supplier_value == Decimal("0.08")
