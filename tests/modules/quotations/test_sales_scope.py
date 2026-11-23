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


@pytest.mark.parametrize(
    "scope",
    [
        EVERYTHING,
        SalesScope("mine", ("0051",), (), ("0051",)),
    ],
)
async def test_an_advisor_sees_only_the_quotations_they_created(
    service, sales, repository, advisor, approver, scope
) -> None:
    sales.scopes[advisor.email] = EVERYTHING
    detail = await service.create(advisor, NIT, 6)
    # Same customer and salesperson, made by someone else.
    other = replace(
        detail.quotation, id=detail.quotation.id.__class__(int=1), created_by_id=approver.user_id
    )
    repository.items[other.id] = replace(detail, quotation=other)

    sales.scopes[advisor.email] = scope
    page = await service.list(
        advisor, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert [q.id for q in page.items] == [detail.quotation.id]
    assert (await service.get(advisor, detail.quotation.id)).quotation.id == detail.quotation.id
    with pytest.raises(ForbiddenError):
        await service.get(advisor, other.id)

    # The validator still reads both.
    every = await service.list(
        approver, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert every.total == 2


async def test_permissions_report_the_scope(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("team", (), ("S01",), ("0051",))

    assert (await service.permissions(advisor)).sales_scope == "team"


async def _two_quotations(service, sales, repository, advisor, approver):
    """One made by ``approver`` for a customer of seller 0051, one for seller 0099."""

    sales.scopes[advisor.email] = EVERYTHING
    detail = await service.create(advisor, NIT, 6)
    made_by_other = replace(detail.quotation, created_by_id=approver.user_id)
    team = replace(made_by_other, id=detail.quotation.id.__class__(int=1))
    outside = replace(
        made_by_other, id=detail.quotation.id.__class__(int=2), salesperson_code="0099"
    )
    del repository.items[detail.quotation.id]
    for quotation in (team, outside):
        repository.items[quotation.id] = replace(detail, quotation=quotation)
    return team, outside


async def test_a_coordinator_sees_the_quotations_of_their_sellers_but_edits_none(
    service, sales, repository, advisor, approver
) -> None:
    team, outside = await _two_quotations(service, sales, repository, advisor, approver)
    sales.scopes[advisor.email] = SalesScope("team", (), ("S01",), ("0051",))

    page = await service.list(
        advisor, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert [q.id for q in page.items] == [team.id]
    seen = await service.get(advisor, team.id)
    assert not await service.can_edit(advisor, seen.quotation)
    with pytest.raises(ForbiddenError):
        await service.get(advisor, outside.id)
    mine = await service.list(
        advisor, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert mine.items == ()

    permissions = await service.permissions(advisor)
    assert permissions.can_see_team and permissions.team_scope == "assigned"


class Directors:
    def __init__(self, *emails: str) -> None:
        self.emails = set(emails)

    async def is_manager(self, principal) -> bool:
        return principal.email in self.emails


async def test_a_director_sees_every_quotation_and_edits_only_their_own(
    service, sales, repository, advisor, approver
) -> None:
    team, outside = await _two_quotations(service, sales, repository, advisor, approver)
    sales.scopes[advisor.email] = NOTHING
    service.directors = Directors(advisor.email)

    page = await service.list(
        advisor, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert {q.id for q in page.items} == {team.id, outside.id}
    seen = await service.get(advisor, outside.id)
    assert not await service.can_edit(advisor, seen.quotation)
    assert await service.can_edit(approver, seen.quotation)

    permissions = await service.permissions(advisor)
    assert permissions.can_see_team and permissions.team_scope == "all"


async def test_someone_who_sees_only_their_own_has_no_team_scope(service, sales, advisor) -> None:
    sales.scopes[advisor.email] = SalesScope("mine", ("0051",), (), ("0051",))

    permissions = await service.permissions(advisor)

    assert not permissions.can_see_team and permissions.team_scope is None
