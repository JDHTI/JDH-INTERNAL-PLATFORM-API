"""Sales scope: who sees which customers, and the salesperson links."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.areas.domain.entities import AccessProfile, AreaGrants
from app.modules.identity.domain.principal import DirectoryUser
from app.modules.salespeople.application.service import SalesScopeService
from app.modules.salespeople.domain.entities import (
    SELLER,
    SUPERVISOR,
    RosterEntry,
    SalespersonLink,
)
from tests.modules.fakes import principal

NOW = datetime(2026, 11, 5, tzinfo=UTC)
ANA = "ana.vendedora@jdh.com.co"
LUIS = "luis.supervisor@jdh.com.co"
PAULA = "paula.asistente@jdh.com.co"


class FakeLinks:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], SalespersonLink] = {}

    async def list_for_user(self, user_id):
        return [link for link in self.rows.values() if link.user_id == user_id]

    async def list_all(self):
        return list(self.rows.values())

    async def get(self, link_id):
        return next((link for link in self.rows.values() if link.id == link_id), None)

    async def get_by_code(self, kind, code):
        return self.rows.get((kind, code))

    async def put(self, kind, code, user_id, source, created_by_id):
        current = self.rows.get((kind, code))
        link = SalespersonLink(
            current.id if current else uuid.uuid4(),
            kind,
            code,
            user_id,
            source,
            created_by_id,
            NOW,
            NOW,
        )
        self.rows[(kind, code)] = link
        return link

    async def create_missing(self, rows, source):
        created = 0
        for kind, code, user_id in rows:
            if (kind, code) not in self.rows:
                await self.put(kind, code, user_id, source, None)
                created += 1
        return created


class FakeRoster:
    def __init__(self) -> None:
        self.seller_rows = [
            RosterEntry(SELLER, "0051", "PEREZ ANA", ANA, "S01"),
            RosterEntry(SELLER, "0052", "GOMEZ JUAN", None, "S01"),
            RosterEntry(SELLER, "0099", "OTRO", None, "S02"),
            RosterEntry(SELLER, "0010", "RETIRADO", None, "S01", is_active=False),
        ]
        self.supervisor_rows = [
            RosterEntry(SUPERVISOR, "S01", "SUPERVISOR UNO"),
            RosterEntry(SUPERVISOR, "S02", "SUPERVISOR DOS"),
        ]
        # The coordinators' master (``consumo.supervisores``): supervisor code -> e-mail.
        self.coordinators: dict[str, str] = {}
        self.down = False

    async def sellers(self):
        return list(self.seller_rows)

    async def supervisors(self):
        return [
            RosterEntry(SUPERVISOR, e.code, e.name, self.coordinators.get(e.code))
            for e in self.supervisor_rows
        ]

    async def sellers_by_email(self, email):
        if self.down:
            raise RuntimeError("warehouse down")
        return [e for e in self.seller_rows if e.email == email.lower() and e.is_active]

    async def supervisors_by_email(self, email):
        if self.down:
            raise RuntimeError("warehouse down")
        return sorted(code for code, mail in self.coordinators.items() if mail == email.lower())

    async def team(self, supervisor_codes):
        return [
            e.code
            for e in self.seller_rows
            if e.supervisor_code in supervisor_codes and e.is_active
        ]


class FakeUsers:
    def __init__(self, *people) -> None:
        self.people = {p.email: DirectoryUser(p.user_id, p.email, p.email, True) for p in people}

    async def require_user(self, email):
        user = self.people.get(email.lower())
        if user is None:
            raise NotFoundError("No hay un usuario con ese correo")
        return user

    async def get_users(self, user_ids):
        ids = set(user_ids)
        return {u.id: u for u in self.people.values() if u.id in ids}

    async def active_emails(self):
        return {u.email: u.id for u in self.people.values() if u.is_active}


class FakeAccess:
    def __init__(self) -> None:
        self.areas: dict[uuid.UUID, dict[str, AreaGrants]] = {}

    def put(self, who, slug, **values):
        self.areas.setdefault(who.user_id, {})[slug] = AreaGrants(uuid.uuid4(), slug, **values)

    async def profile(self, who):
        return AccessProfile(role=who.role, areas=self.areas.get(who.user_id, {}))


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def record_global_event(self, action, principal, *, target_user_id, detail=None):
        self.events.append((action, detail or {}))


@pytest.fixture
def people():
    return {
        "ana": principal(ANA),
        "luis": principal(LUIS),
        "paula": principal(PAULA),
        "admin": principal("admin@jdh.com.co", admin=True),
    }


@pytest.fixture
def world(people):
    links, roster, access, audit = FakeLinks(), FakeRoster(), FakeAccess(), FakeAudit()
    users = FakeUsers(*people.values())
    service = SalesScopeService(links, roster, users, access, audit, ("bi",), ("comercial",))
    return service, links, roster, access, audit


async def test_a_salesperson_sees_their_portfolio_by_email(world, people) -> None:
    service, *_ = world

    scope = await service.scope_for(people["ana"])

    assert scope.scope == "mine" and scope.codes == ("0051",)
    assert scope.allows("0051") and not scope.allows("0099")


async def test_nobody_linked_sees_nothing_even_in_comercial(world, people) -> None:
    service, _, _, access, _ = world
    access.put(people["paula"], "comercial", role_id=uuid.uuid4(), grants={"*": "manage"})

    scope = await service.scope_for(people["paula"])

    assert scope.scope == "none" and scope.codes == () and not scope.allows("0051")


async def test_bi_the_comercial_leader_and_admins_see_everything(world, people) -> None:
    service, _, _, access, _ = world
    access.put(people["paula"], "bi", grants={"cotizador": "read"})
    access.put(people["luis"], "comercial", is_leader=True)

    for who in (people["paula"], people["luis"], people["admin"]):
        scope = await service.scope_for(who)
        assert scope.scope == "all" and scope.is_all and scope.allows("0099")
    moderator = principal("auditor@jdh.com.co", role="moderator")
    assert (await service.scope_for(moderator)).is_all


class Directors:
    def __init__(self, *emails: str) -> None:
        self.emails = set(emails)

    async def is_manager(self, who) -> bool:
        return who.email in self.emails


async def test_a_comercial_director_sees_everything(world, people) -> None:
    service, *_ = world
    service.directors = Directors(PAULA)

    assert (await service.scope_for(people["paula"])).is_all
    assert (await service.scope_for(people["ana"])).scope == "mine"


async def test_a_supervisor_sees_their_team(world, people) -> None:
    service, *_ = world
    await service.link(people["admin"], LUIS, "supervisor", "S01")

    scope = await service.scope_for(people["luis"])

    assert scope.scope == "team"
    assert scope.codes == ("0051", "0052")  # the inactive 0010 is left out
    assert scope.supervisor_codes == ("S01",)


async def test_a_coordinator_sees_their_team_by_the_coordinators_master(world, people) -> None:
    # Juan Rojas (2026-11-24): a coordinator of consumo.supervisores; his team is the master's.
    service, links, roster, *_ = world
    roster.coordinators["S01"] = LUIS

    scope = await service.scope_for(people["luis"])

    assert scope.scope == "team" and scope.codes == ("0051", "0052")
    assert scope.supervisor_codes == ("S01",)
    # Unlinking the code stops the e-mail from giving it back.
    await service.sync_by_email(None)
    assert links.rows[(SUPERVISOR, "S01")].source == "auto"
    await service.unlink(people["admin"], links.rows[(SUPERVISOR, "S01")].id)
    assert (await service.scope_for(people["luis"])).scope == "none"


async def test_a_supervisor_held_by_someone_else_is_not_given_by_email(world, people) -> None:
    service, _, roster, *_ = world
    roster.coordinators["S01"] = LUIS
    await service.link(people["admin"], PAULA, "supervisor", "S01")

    assert (await service.scope_for(people["luis"])).scope == "none"
    assert (await service.scope_for(people["paula"])).codes == ("0051", "0052")
    page = await service.roster_page(people["admin"], kind="supervisor")
    assert {row.entry.code: row.holder_email for row in page.items}["S01"] == PAULA


async def test_a_manual_link_wins_and_unlinking_stops_the_email(world, people) -> None:
    service, links, _, _, audit = world
    await service.link(people["admin"], PAULA, "seller", "0051")

    assert (await service.scope_for(people["paula"])).codes == ("0051",)
    assert (await service.scope_for(people["ana"])).scope == "none"  # her e-mail lost it

    link = links.rows[(SELLER, "0051")]
    await service.unlink(people["admin"], link.id)

    assert (await service.scope_for(people["paula"])).scope == "none"
    assert (await service.scope_for(people["ana"])).scope == "none"  # excluded, not by e-mail
    assert [action for action, _ in audit.events] == ["salesperson_linked", "salesperson_unlinked"]


async def test_linking_checks_the_code_and_who_holds_it(world, people) -> None:
    service, *_ = world
    with pytest.raises(NotFoundError):
        await service.link(people["admin"], PAULA, "seller", "7777")
    with pytest.raises(NotFoundError):
        await service.link(people["admin"], "nadie@jdh.com.co", "seller", "0052")
    with pytest.raises(ForbiddenError):
        await service.link(people["ana"], PAULA, "seller", "0052")

    await service.link(people["admin"], PAULA, "seller", "0052")
    with pytest.raises(ConflictError):
        await service.link(people["admin"], ANA, "seller", "0052")


async def test_sync_stores_the_email_matches_once(world, people) -> None:
    service, links, *_ = world

    first = await service.sync_by_email(people["admin"])
    second = await service.sync_by_email(None)

    assert (first.created, first.matched) == (1, 1)
    assert second.created == 0
    assert links.rows[(SELLER, "0051")].source == "auto"
    assert (await service.scope_for(people["ana"])).codes == ("0051",)


async def test_a_warehouse_outage_only_loses_the_email_match(world, people) -> None:
    service, _, roster, *_ = world
    roster.down = True

    assert (await service.scope_for(people["ana"])).scope == "none"


async def test_the_roster_shows_who_holds_each_code(world, people) -> None:
    service, *_ = world
    await service.link(people["admin"], PAULA, "seller", "0052")

    page = await service.roster_page(people["admin"])
    holders = {row.entry.code: row.holder_email for row in page.items}

    assert holders == {"0051": ANA, "0052": PAULA, "0099": None}
    unlinked = await service.roster_page(people["admin"], linked=False)
    assert [row.entry.code for row in unlinked.items] == ["0099"]
    with pytest.raises(ForbiddenError):
        await service.roster_page(people["ana"])
