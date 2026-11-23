"""A key granted by name only: Comercial's directors (2026-11-23)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.areas.domain.catalog import AreaModule
from app.modules.areas.domain.entities import AccessProfile, AreaGrants
from app.modules.areas.infrastructure.policies import ExplicitGrantPolicy

KEY = "comercial-director"


def who(email: str, *, admin: bool = False, moderator: bool = False) -> SimpleNamespace:
    return SimpleNamespace(email=email, is_platform_admin=admin, is_moderator=moderator)


class FakeAreas:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self.profiles = profiles

    async def profile(self, principal):
        return self.profiles.get(principal.email, AccessProfile(role="user"))


def comercial(**values) -> AccessProfile:
    return AccessProfile(
        role="user", areas={"comercial": AreaGrants(uuid4(), "comercial", **values)}
    )


async def test_only_a_grant_by_name_the_leader_and_admins_make_a_director() -> None:
    areas = FakeAreas(
        {
            "director@x": comercial(grants={KEY: "manage"}),
            "lector@x": comercial(grants={KEY: "read"}),
            "comodin@x": comercial(grants={"*": "manage"}),
            "lider@x": comercial(is_leader=True),
            "resp@x": comercial(is_responsible=True),
            "bi@x": AccessProfile(
                role="user", areas={"bi": AreaGrants(uuid4(), "bi", grants={KEY: "manage"})}
            ),
        }
    )
    policy = ExplicitGrantPolicy(areas, "comercial", KEY)

    for email in ("director@x", "lider@x", "resp@x"):
        assert await policy.is_manager(who(email)), email
    assert await policy.is_manager(who("admin@x", admin=True))
    # The wildcard, read, another area and moderators never make a director.
    for email in ("lector@x", "comodin@x", "bi@x", "otro@x"):
        assert not await policy.is_manager(who(email)), email
    assert not await policy.is_manager(who("director@x", moderator=True))
    assert not await ExplicitGrantPolicy(areas, "", KEY).is_manager(who("director@x"))


async def test_switched_off_in_the_area_it_gives_nothing() -> None:
    profile = comercial(grants={KEY: "manage"})
    off = AccessProfile(
        role="user",
        areas=profile.areas,
        overrides={"comercial": {KEY: AreaModule(is_enabled=False)}},
    )

    assert not await ExplicitGrantPolicy(FakeAreas({"d@x": off}), "comercial", KEY).is_manager(
        who("d@x")
    )
