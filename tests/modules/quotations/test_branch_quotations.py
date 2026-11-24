"""Quoting one branch of a customer, and the hierarchy of supports (2026-11-24).

NIT-sucursal > NIT > grupo empresarial: the most specific support that covers the line
replaces the others. A branch's support only applies to a quotation of that branch.
"""

import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.errors import InvalidInputError
from app.modules.order_validation.domain.entities import CustomerScope
from app.modules.quotations.domain.entities import CustomerBranchOption
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, branch, sale


@pytest.fixture
def branches(warehouse, masters, supports):
    """The customer has two branches: 001 on list 001 and 010 on list C10 billed from 003."""

    warehouse.branch_rows[NIT] = [
        branch(branch_id="001"),
        branch(
            branch_id="010",
            price_list_id="C10",
            price_list_name="LISTA SUCURSAL 10",
            billing_center_code="003",
            legal_name="HOTEL EL BOSQUE SEDE 10",
        ),
    ]
    warehouse.existing_lists.add("C10")
    warehouse.sales[f"{NIT}/010"] = [sale("2537", price="4300")]
    masters.branches[NIT] = [
        CustomerBranchOption(
            "001", "PRINCIPAL", "EL BOSQUE", "MEDELLIN", "HOTELES", "PEREZ ANA", True, "0051"
        ),
        CustomerBranchOption(
            "010", "SEDE 10", "EL BOSQUE 10", "BOGOTA", "HOTELES", "PEREZ ANA", True, "0051"
        ),
    ]
    supports.clients[NIT] = CustomerScope(NIT, channels=frozenset({"HOTELES Y CLUBES"}))
    supports.branch_ids[NIT] = ("001", "010")
    return "010"


def _support(**values):
    base = {
        "supplier_code": None,
        "product_sku": "2537",
        "calc_method": "pct_costo",
    }
    return agreement(**{**base, **values})


async def test_the_customer_branches_to_choose(service, advisor, branches) -> None:
    found = await service.customer_branches(advisor, NIT)

    assert [item.branch_id for item in found] == ["001", "010"]


async def test_a_branch_is_quoted_alone(service, advisor, warehouse, branches) -> None:
    detail = await service.create(advisor, NIT, 12, customer_branch_id="010")
    quotation = detail.quotation

    assert quotation.customer_branch_id == "010"
    # That branch decides the list and the center, not the majority of the NIT.
    assert quotation.price_list_id == "C10" and quotation.billing_center_code == "003"
    assert "listas_distintas" not in quotation.warnings
    # Its own sales are the window.
    assert ("last_sales", NIT, 12, "010") in warehouse.calls

    refreshed = await service.refresh(advisor, quotation.id, 1)
    assert refreshed.quotation.customer_branch_id == "010"
    assert refreshed.quotation.price_list_id == "C10"

    offer_file = await service.offer_file(advisor, quotation.id)
    sheet = load_workbook(io.BytesIO(offer_file.content)).active
    labels = {row[0]: row[1] for row in sheet.iter_rows(values_only=True) if row and row[0]}
    assert labels["Sucursal"] == "010"


async def test_a_branch_needs_the_nit_and_must_exist(service, advisor, branches) -> None:
    with pytest.raises(InvalidInputError, match="necesita el NIT"):
        await service.create(advisor, None, 12, business_group="QBANO", customer_branch_id="010")
    with pytest.raises(InvalidInputError, match="La sucursal 099 no existe"):
        await service.create(advisor, NIT, 12, customer_branch_id="099")


async def test_the_most_specific_support_prices_the_line(
    service, advisor, supports, branches
) -> None:
    group = _support(customer_group="GRUPO X", supplier_value=Decimal("0.30"))
    nit = _support(customer_nit=NIT, supplier_value=Decimal("0.05"))
    own_branch = _support(
        customer_nit=NIT, customer_branch_id="010", supplier_value=Decimal("0.02")
    )
    supports.clients[NIT] = CustomerScope(
        NIT, channels=frozenset({"HOTELES Y CLUBES"}), business_group="GRUPO X"
    )
    supports.items = [group, nit, own_branch]

    # The NIT as a whole: the branch's support does not apply, and the NIT's beats the group's
    # even though the group's brings more money.
    general = await service.create(advisor, NIT, 12)
    line = general.lines[0]
    assert line.support is not None and line.support.support_id == nit.id
    reasons = {item.support_id: item.reason for item in line.support_candidates}
    assert reasons[group.id] == "Hay un apoyo más específico (NIT)"
    assert own_branch.id not in reasons

    # Branch 010: its own support, the most specific, replaces the NIT's and the group's.
    by_branch = await service.create(advisor, NIT, 12, customer_branch_id="010")
    line = by_branch.lines[0]
    assert line.support is not None and line.support.support_id == own_branch.id
    reasons = {item.support_id: item.reason for item in line.support_candidates}
    assert reasons[nit.id] == reasons[group.id] == "Hay un apoyo más específico (NIT-sucursal)"
