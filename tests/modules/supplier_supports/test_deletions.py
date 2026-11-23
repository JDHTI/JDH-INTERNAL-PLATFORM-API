"""Deleting supports and requests for good: the area leader and the platform administrators
only (2026-11-23)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.areas.domain.entities import AccessProfile, AreaGrants
from app.modules.areas.infrastructure.policies import AreaLeaderPolicy
from app.modules.supplier_supports.application.deletions import SupportDeletionService
from tests.modules.supplier_supports.conftest import FakeManagers, person


class FakeAreas:
    """Profiles by email: ``leader`` leads BI, ``analyst`` is a BI member with every grant."""

    def __init__(self, leader, analyst, responsible) -> None:
        self.profiles = {
            leader.email: AccessProfile(
                role="user", areas={"bi": AreaGrants(uuid4(), "bi", is_leader=True)}
            ),
            analyst.email: AccessProfile(
                role="user",
                areas={"bi": AreaGrants(uuid4(), "bi", grants={"*": "manage"})},
            ),
            responsible.email: AccessProfile(
                role="user", areas={"bi": AreaGrants(uuid4(), "bi", is_responsible=True)}
            ),
        }

    async def profile(self, principal):
        return self.profiles.get(principal.email, AccessProfile(role="user"))


async def test_only_the_area_leader_and_platform_admins_delete() -> None:
    leader, analyst, responsible = person("lider.bi@x"), person("analista@x"), person("resp@x")
    policy = AreaLeaderPolicy(FakeAreas(leader, analyst, responsible), "bi")

    assert await policy.is_manager(leader)
    assert await policy.is_manager(responsible)
    assert await policy.is_manager(person("admin@x", admin=True))
    # A member with every grant of the area manages it, but never deletes.
    assert not await policy.is_manager(analyst)
    assert not await policy.is_manager(person("otro@x"))
    moderator = SimpleNamespace(is_platform_admin=False, is_moderator=True, email=leader.email)
    assert not await policy.is_manager(moderator)
    assert not await AreaLeaderPolicy(FakeAreas(leader, analyst, responsible), "").is_manager(
        leader
    )


class FakeDeletions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def delete_supports(self, chosen, reason, actor, now):
        self.calls.append(("supports", dict(chosen), reason, actor))
        return len(chosen)

    async def delete_requests(self, request_ids):
        self.calls.append(("requests", list(request_ids)))
        return len(request_ids)

    async def delete_internal_requests(self, request_ids):
        self.calls.append(("internal", list(request_ids)))
        return len(request_ids)


class FakeSearch:
    def __init__(self, found) -> None:
        self.found = found
        self.criteria = None

    async def search_ids(self, limit, **criteria):
        self.criteria = criteria
        return self.found[:limit]


async def test_deleting_supports_takes_a_selection_or_a_filter_with_a_reason() -> None:
    repository, chosen, filtered = FakeDeletions(), uuid4(), [uuid4(), uuid4()]
    search = FakeSearch(filtered)
    leader = person("lider.bi@x")
    service = SupportDeletionService(repository, FakeManagers([leader.email]), search)

    deleted = await service.delete_supports(
        leader, {chosen: 3}, {"supplier_codes": ["233"]}, "  Cargados   por error  "
    )

    assert deleted == 3
    assert repository.calls[0] == (
        "supports",
        {chosen: 3, filtered[0]: None, filtered[1]: None},
        "Cargados por error",
        leader.email,
    )
    # The filter also reaches voided supports.
    assert search.criteria == {"supplier_codes": ["233"], "include_voided": True}
    with pytest.raises(InvalidInputError, match="por qué"):
        await service.delete_supports(leader, {chosen: 3}, None, "  ")
    with pytest.raises(InvalidInputError, match="entre 1 y"):
        await service.delete_supports(leader, {}, None, "x y z")
    with pytest.raises(ForbiddenError, match="líder del área"):
        await service.delete_supports(person("analista@x"), {chosen: 3}, None, "x y z")


async def test_deleting_requests_is_for_the_leader_too() -> None:
    repository, leader = FakeDeletions(), person("lider.bi@x")
    service = SupportDeletionService(repository, FakeManagers([leader.email]), FakeSearch([]))
    first, second = uuid4(), uuid4()

    assert await service.delete_requests(leader, [first, first, second]) == 2
    assert await service.delete_internal_requests(leader, [second]) == 1
    assert await service.can_delete(leader)
    assert not await service.can_delete(person("otro@x"))
    with pytest.raises(ForbiddenError):
        await service.delete_requests(person("otro@x"), [first])
    with pytest.raises(InvalidInputError):
        await service.delete_internal_requests(leader, [])
