"""The quoter applies each person's sales scope on the server."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.errors import ForbiddenError
from app.modules.salespeople.domain.entities import EVERYTHING, NOTHING, SalesScope
from tests.modules.quotations.conftest import NIT


class FakeSales:
    def __init__(self) -> None:
        self.scopes: dict[str, SalesScope] = {}

    async def scope_for(self, principal):
        return self.scopes.get(principal.email, NOTHING)


@pytest.fixture
def sales(service) -> FakeSales:
    fake = FakeSales()
    service.sales = fake
    return fake


async def test_customers_are_decided_by_the_scope_not_the_client(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("mine", ("0051",), (), ("0051",))

    page, linked = await service.customers(advisor, "all", None, 1, 20)

    assert [item.nit for item in page.items] == [NIT] and linked

    sales.scopes[advisor.email] = NOTHING
    page, linked = await service.customers(advisor, "all", None, 1, 20)
    assert page.items == () and not linked


async def test_everyone_scope_still_chooses_mine_or_all(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("all", ("0051",), (), None)

    mine, _ = await service.customers(advisor, "mine", None, 1, 20)
    every, _ = await service.customers(advisor, "all", None, 1, 20)

    assert [item.nit for item in mine.items] == [NIT]
    assert len(every.items) == 2


async def test_quoting_a_customer_outside_the_portfolio_is_refused(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("mine", ("0099",), (), ("0099",))

    with pytest.raises(ForbiddenError):
        await service.create(advisor, NIT, 6)

    sales.scopes[advisor.email] = SalesScope("mine", ("0051",), (), ("0051",))
    detail = await service.create(advisor, NIT, 6)
    assert detail.quotation.salesperson_code == "0051"


async def test_quotations_are_seen_only_inside_the_scope(
    service, sales, repository, advisor, approver
) -> None:
    sales.scopes[advisor.email] = EVERYTHING
    detail = await service.create(advisor, NIT, 6)
    other = replace(
        detail.quotation, id=detail.quotation.id.__class__(int=1), created_by_id=approver.user_id
    )
    repository.items[other.id] = replace(detail, quotation=other)

    sales.scopes[advisor.email] = SalesScope("mine", ("0099",), (), ("0099",))
    page = await service.list(
        advisor, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    # Only the one the advisor made: the other is of a customer outside their codes.
    assert [q.id for q in page.items] == [detail.quotation.id]
    assert (await service.get(advisor, detail.quotation.id)).quotation.id == detail.quotation.id
    with pytest.raises(ForbiddenError):
        await service.get(advisor, other.id)

    sales.scopes[advisor.email] = SalesScope("team", (), ("S01",), ("0051",))
    assert (await service.get(advisor, other.id)).quotation.id == other.id


async def test_permissions_report_the_scope(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("team", (), ("S01",), ("0051",))

    assert (await service.permissions(advisor)).sales_scope == "team"
