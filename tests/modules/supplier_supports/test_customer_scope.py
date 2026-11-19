"""Who sees which supports (2026-11-19): BI sees all, a salesperson their customers' and the
general ones, and someone without customers sees none."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import NotFoundError
from app.modules.salespeople.domain.entities import EVERYTHING, NOTHING, SalesScope
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain.entities import SupportData
from tests.modules.supplier_supports.conftest import FakeManagers, FixedCalendar, person

SELLER = person("vendedor@jdh.com.co")
LEADER = person("lider@jdh.com.co")
NOBODY = person("sin.cartera@jdh.com.co")
BI = person("comercial@jdh.com.co")  # manages ``apoyos`` in the fixtures


class FakeScopes:
    def __init__(self) -> None:
        self.scopes = {
            SELLER.email: SalesScope("mine", ("0051",), (), ("0051",)),
            LEADER.email: EVERYTHING,
        }

    async def scope_for(self, principal):
        return self.scopes.get(principal.email, NOTHING)


@pytest.fixture
def scoped(repo, lookup) -> SupplierSupportService:
    return SupplierSupportService(
        repository=repo,
        masters=lookup,
        managers=FakeManagers([BI.email]),
        calendar=FixedCalendar(),
        sales=FakeScopes(),
    )


def support(**values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "costo_neto",
        "supplier_value": Decimal("80"),
        "reference_cost": Decimal("100"),
        "target_margin_pct": Decimal("0.17"),
        "customer_nit": "800116299",
        "product_sku": "1128",
        "valid_from": date(2026, 7, 1),
        "valid_to": date(2027, 1, 30),
    }
    return SupportData(**{**base, **values})


async def test_each_person_sees_the_supports_of_their_customers(scoped, repo) -> None:
    mine = (await scoped.create(BI, support(), supplier_code="233")).support
    general = (
        await scoped.create(
            BI,
            support(
                kind="actividad_temporal",
                calc_method="pct_costo",
                supplier_value=Decimal("0.05"),
                customer_nit=None,
            ),
            supplier_code="233",
        )
    ).support
    other = replace(mine, id=uuid4(), data=replace(mine.data, customer_nit="900999999"))
    repo.items[other.id] = other
    repo.salespeople = {"800116299": "0051", "900999999": "0099"}

    async def seen(principal) -> set:
        page = await scoped.search(principal)
        return {view.support.id for view in page.items}

    # BI (manages apoyos) and Comercial's leaders see every support.
    assert await seen(BI) == {mine.id, general.id, other.id}
    assert await seen(LEADER) == {mine.id, general.id, other.id}
    # A salesperson: their customers' supports and the general ones.
    assert await seen(SELLER) == {mine.id, general.id}
    # Without customers, no supports at all.
    assert await seen(NOBODY) == set()
    assert [await scoped.customer_scope(item) for item in (BI, LEADER, SELLER, NOBODY)] == [
        "all",
        "all",
        "mine",
        "none",
    ]

    # The detail follows the same rule (404 outside).
    assert (await scoped.get(mine.id, SELLER)).support.id == mine.id
    assert (await scoped.get(general.id, SELLER)).support.id == general.id
    with pytest.raises(NotFoundError):
        await scoped.get(other.id, SELLER)
    with pytest.raises(NotFoundError):
        await scoped.get(general.id, NOBODY)
    assert (await scoped.get(other.id, LEADER)).support.id == other.id
