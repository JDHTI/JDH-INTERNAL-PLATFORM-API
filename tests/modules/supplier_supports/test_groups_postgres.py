"""Opt-in: a business group's supports, the state counts and «por vencer» on PostgreSQL.

Runs with ``SUPPORT_REQUEST_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
)
from app.modules.commercial_masters.infrastructure.repository import (
    SqlAlchemyCommercialMasterRepository,
)
from app.modules.order_validation.infrastructure.agreements import SqlAlchemyAgreementSource
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.filters import Condition, Group, SearchSpec
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)


@pytest.fixture
async def world():
    """A group of three customers (two of them loaded, one that joined later), another
    customer and a supplier, all with names of their own for this run."""

    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    run = uuid4().hex[:8]
    supplier_id = uuid4()
    nits = {name: f"9{run}{index}" for index, name in enumerate(("a", "b", "late", "other"))}
    group = f"Grupo  {run}"  # written with two spaces in the master
    now = datetime.now(UTC)
    async with database.session_factory() as session, session.begin():
        session.add(CommercialSupplierModel(id=supplier_id, code=f"P{run}", name="Prov"))
        for name, nit in nits.items():
            session.add(
                CustomerModel(
                    id=uuid4(),
                    cliente_key=f"K{nit}",
                    company_id=1,
                    nit=nit,
                    branch_id="001",
                    legal_name=name,
                    business_group=None if name == "other" else group,
                    salesperson_code=f"V{run}{name}",
                    is_active=True,
                    synced_at=now,
                )
            )
    try:
        yield database, supplier_id, nits, group.replace("  ", " ").lower(), run
    finally:
        await database.dispose()


def support(supplier_id, sku, **values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "commercial_supplier_id": supplier_id,
        "supplier_value": Decimal("0.05"),
        "product_sku": sku,
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
    }
    return SupportData(**{**base, **values})


def spec(**values) -> SearchSpec:
    base = {
        "group": Group("all", ()),
        "q": None,
        "active_on": date(2026, 11, 20),
        "validity": None,
        "state": None,
        "needs_review": None,
        "has_issues": None,
        "has_conflicts": None,
        "effective_only": False,
        "include_voided": False,
        "sort": (),
        "page": 1,
        "size": 50,
    }
    return SearchSpec(**{**base, **values})


async def test_a_group_support_covers_a_customer_that_joined_later(world) -> None:
    database, supplier_id, nits, group, run = world
    repo = SqlAlchemySupportRepository(database)
    now = datetime.now(UTC)
    sku = f"G{run}"
    copies = [
        await repo.create(
            uuid4(),
            support(
                supplier_id, sku, customer_nit=nits[name], customer_group=group, group_wide=True
            ),
            "bi",
            now,
        )
        for name in ("a", "b")
    ]
    own = await repo.create(
        uuid4(),
        support(supplier_id, sku, customer_nit=nits["a"], customer_group=group, valid_from=None),
        "bi",
        now,
    )
    general = await repo.create(uuid4(), support(supplier_id, sku), "bi", now)

    # The salesperson of the customer that joined later sees the group's support (once: the
    # copy of the lowest NIT) and the general one, never a support of one NIT of the group.
    seen, _ = await repo.search(spec(salesperson_codes=(f"V{run}late",)))
    ids = {row.support.id for row in seen}
    assert {copies[0].id, general.id} <= ids and copies[1].id not in ids and own.id not in ids
    assert await repo.in_groups_of(group.upper(), (f"V{run}late",))
    assert not await repo.in_groups_of(group, (f"V{run}other",))
    outside, _ = await repo.search(spec(salesperson_codes=(f"V{run}other",)))
    assert not {copy.id for copy in copies} & {row.support.id for row in outside}

    # The base search by that customer finds the group's support too, once.
    found, _ = await repo.search(spec(customer_nits=(nits["late"],)))
    assert {row.support.id for row in found} == {copies[0].id}

    # The quoter takes the copies as one support for the whole group.
    agreements = await SqlAlchemyAgreementSource(database).agreements(
        [f"P{run}"], [sku], [nits["late"]]
    )
    wide = [item for item in agreements if item.customer_group]
    assert len(wide) == 1 and wide[0].customer_nit is None
    assert {item.id for item in agreements} >= {general.id}
    # The customer's own copy is the one kept when it has one.
    mine = await SqlAlchemyAgreementSource(database).agreements([f"P{run}"], [sku], [nits["b"]])
    assert copies[1].id in {item.id for item in mine}


async def test_a_group_support_is_one_row_and_a_nit_support_stays_apart(world) -> None:
    """2026-11-23: a group's copies used to come once per customer (they looked like
    duplicates), and a NIT's own support could not be told from the group's."""

    database, supplier_id, nits, group, run = world
    repo = SqlAlchemySupportRepository(database)
    now = datetime.now(UTC)
    sku = f"D{run}"
    copies = [
        await repo.create(
            uuid4(),
            support(
                supplier_id,
                sku,
                customer_nit=nits[name],
                # As the master writes it for each customer.
                customer_group=group.upper() if name == "b" else group,
                group_wide=True,
            ),
            "bi",
            now,
        )
        for name in ("a", "b")
    ]
    own = await repo.create(
        uuid4(),
        support(
            supplier_id,
            sku,
            customer_nit=nits["b"],
            customer_group=group,
            supplier_value=Decimal("0.10"),
        ),
        "bi",
        now,
    )

    # By NIT: its own support and the group's, once, with the customer's own copy.
    by_nit, total = await repo.search(spec(customer_nits=(nits["b"],)))
    assert {row.support.id for row in by_nit} == {copies[1].id, own.id} and total == 2
    # Only the NIT: never the group's copy.
    alone, _ = await repo.search(spec(customer_nits=(nits["b"],), customer_nit_only=True))
    assert [row.support.id for row in alone] == [own.id]
    # By group, however it is written: the group's support once, not the NIT's own.
    by_group, total = await repo.search(spec(business_groups=(f"  {group.upper()} ",)))
    assert [row.support.id for row in by_group] == [copies[0].id] and total == 1
    # Without a customer filter, still once.
    every, _ = await repo.search(spec(group=Group("all", (Condition("product_sku", "eq", sku),))))
    assert {row.support.id for row in every} == {copies[0].id, own.id}
    # Acting on the row acts on every copy; a NIT's own support has none.
    assert set(await repo.with_group_copies([copies[0].id])) == {copy.id for copy in copies}
    assert await repo.with_group_copies([own.id]) == [own.id]
    # The id listing behind void, expire and delete takes every copy.
    ids = await repo.search_ids(spec(business_groups=(group,)), 50)
    assert set(ids) == {copy.id for copy in copies}


async def test_the_members_of_a_group_come_once_per_nit(world) -> None:
    database, _, nits, group, run = world
    async with database.session_factory() as session, session.begin():
        # Another branch of customer «a» writes the group in upper case with spaces.
        session.add(
            CustomerModel(
                id=uuid4(),
                cliente_key=f"K{nits['a']}-2",
                company_id=1,
                nit=nits["a"],
                branch_id="002",
                legal_name="a",
                business_group=f" {group.upper()}  ",
                is_active=True,
                synced_at=datetime.now(UTC),
            )
        )
    masters = SqlAlchemyCommercialMasterRepository(database)

    members = await masters.business_group_members([group, group.upper()])

    expected = sorted(nits[name] for name in ("a", "b", "late"))
    assert sorted(nit for nit, _ in members[group]) == expected
    assert sorted(nit for nit, _ in members[group.upper()]) == expected


async def test_the_counts_by_state_and_por_vencer(world) -> None:
    database, supplier_id, nits, _, run = world
    repo = SqlAlchemySupportRepository(database)
    now = datetime.now(UTC)
    day = date(2026, 11, 20)
    sku = f"C{run}"
    nit = nits["other"]

    def make(**values) -> SupportData:
        return support(supplier_id, sku, customer_nit=nit, **values)

    soon = await repo.create(uuid4(), make(valid_to=day + timedelta(days=30)), "bi", now)
    # 20 December is one calendar month out: «por vencer»; the 21st is not.
    await repo.create(uuid4(), make(product_sku=f"{sku}2", valid_to=date(2026, 12, 21)), "bi", now)
    await repo.create(uuid4(), make(product_sku=f"{sku}3", valid_from=date(2027, 1, 1)), "bi", now)
    await repo.create(uuid4(), make(product_sku=f"{sku}4", valid_to=date(2026, 10, 31)), "bi", now)
    await repo.create(uuid4(), make(product_sku=f"{sku}5", is_enabled=False), "bi", now)

    counts = await repo.state_counts(spec(customer_nits=(nit,), state="vencido"))

    assert counts == {
        "vigente": 2,
        "por_vencer": 1,
        "programado": 1,
        "vencido": 1,
        "inactivo": 1,
        "total": 5,
    }
    rows, total = await repo.search(spec(customer_nits=(nit,), state="por_vencer"))
    assert total == 1 and rows[0].support.id == soon.id
    assert (await repo.search(spec(customer_nits=(nit,), validity="por_vencer")))[1] == 1
