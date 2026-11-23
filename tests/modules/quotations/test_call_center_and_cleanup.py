"""The call center, the cleanup of stale drafts and the increase date of a line (2026-11-23)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError
from app.modules.quotations.presentation.router import _deleted_on
from tests.modules.quotations.conftest import MANAGER, NIT
from tests.modules.supplier_supports.conftest import FakeManagers, person


def _age(repository, quotation_id, days: int, **changes) -> None:
    detail = repository.items[quotation_id]
    created = datetime.now(UTC) - timedelta(days=days)
    repository.items[quotation_id] = replace(
        detail, quotation=replace(detail.quotation, created_at=created, **changes)
    )


# ------------------------------------------------------------------ stale drafts


async def test_drafts_never_sent_are_deleted_after_thirty_days(
    service, advisor, repository
) -> None:
    stale = (await service.create(advisor, NIT, 12)).quotation.id
    returned = (await service.create(advisor, NIT, 6)).quotation.id
    recent = (await service.create(advisor, NIT, 12)).quotation.id
    _age(repository, stale, 31)
    # Sent once and returned to draft: it stays.
    _age(repository, returned, 40, confirmed_at=datetime.now(UTC) - timedelta(days=35))
    _age(repository, recent, 29)

    result = await service.purge_stale_drafts()

    assert result["deleted"] == 1
    assert stale not in repository.items
    assert returned in repository.items and recent in repository.items


async def test_a_draft_says_when_it_will_be_deleted(service, advisor, repository) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    assert _deleted_on(quotation) == quotation.created_at.date() + timedelta(days=30)
    assert _deleted_on(replace(quotation, confirmed_at=datetime.now(UTC))) is None
    assert _deleted_on(replace(quotation, status="pendiente_validacion")) is None
    assert _deleted_on(replace(quotation, kind="lista")) is None


# ------------------------------------------------------------------ increase date


async def test_a_line_carries_the_date_of_its_latest_price_increase(
    service, advisor, masters
) -> None:
    masters.increase_on = {"2537": date(2026, 11, 1)}

    detail = await service.create(advisor, NIT, 12)

    by_sku = {line.sku: line for line in detail.lines}
    assert by_sku["2537"].increase_on == date(2026, 11, 1)


# ------------------------------------------------------------------ call center


class FakeTeam:
    """The call center: ``leader`` leads it and its members are ``members``."""

    def __init__(self, leader, members) -> None:
        self.leader = leader
        self.members = frozenset(member.user_id for member in members)

    async def led_members(self, principal):
        return self.members if principal.user_id == self.leader.user_id else None


@pytest.fixture
def call_center(service, advisor):
    operator = person("operador.cc@jdh.com.co")
    leader = person("lider.cc@jdh.com.co")
    service.managers = FakeManagers([MANAGER, operator.email, leader.email])
    service.team = FakeTeam(leader, [advisor, operator])
    return operator, leader


async def test_each_operator_sees_its_own_quotations_and_the_leader_every_one(
    service, advisor, call_center
) -> None:
    operator, leader = call_center
    first = (await service.create(advisor, NIT, 12)).quotation.id
    second = (await service.create(operator, NIT, 12)).quotation.id

    def listed(page):
        return {item.id for item in page.items}

    common = {"status": None, "customer_nit": None, "q": None, "page": 1, "size": 50}
    assert listed(await service.list(operator, mine=False, **common)) == {second}
    assert listed(await service.list(leader, mine=False, **common)) == {first, second}
    # «Mine» is still only what the leader created.
    assert listed(await service.list(leader, mine=True, **common)) == set()
    assert (await service.get(leader, first)).quotation.id == first
    with pytest.raises(ForbiddenError, match="Solo puedes ver"):
        await service.get(operator, first)
