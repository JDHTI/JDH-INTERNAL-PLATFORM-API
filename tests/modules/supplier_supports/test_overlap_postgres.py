"""Opt-in: crossing supports and mass retirement on PostgreSQL (SUPPORT_REQUEST_TEST_DATABASE_URL)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.overlap import Supersede
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)


@pytest.fixture
async def storage():
    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    supplier_id = uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(CommercialSupplierModel(id=supplier_id, code=supplier_id.hex[:12], name="Prov"))
    try:
        yield SqlAlchemySupportRepository(database), database, supplier_id
    finally:
        await database.dispose()


def tactic(supplier_id, **values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "commercial_supplier_id": supplier_id,
        "supplier_value": Decimal("0.05"),
        "customer_nit": "800116299",
        "product_sku": "SKU-OVERLAP",
        "valid_from": date(2026, 7, 1),
        "valid_to": date(2026, 12, 31),
    }
    return SupportData(**{**base, **values})


async def test_a_new_support_expires_the_one_it_crosses_in_one_transaction(storage) -> None:
    repo, database, supplier_id = storage
    now = datetime.now(UTC)
    first = await repo.create(uuid4(), tactic(supplier_id), "bi", now)
    new = tactic(supplier_id, valid_from=date(2026, 10, 1), valid_to=None)

    crossed = await repo.overlapping(new)
    assert [item.id for item in crossed] == [first.id]
    assert await repo.overlapping(tactic(supplier_id, customer_nit="900")) == []

    plan = [Supersede(first.id, first.version, date(2026, 9, 30), "Nueva negociación")]
    created = await repo.create(uuid4(), new, "bi", now, supersede=plan)

    previous = await repo.get(first.id)
    assert previous.data.valid_to == date(2026, 9, 30) and previous.version == 2
    assert "Nueva negociación" in previous.data.notes
    assert await repo.overlapping(new, exclude=[created.id]) == []
    async with database.session_factory() as session:
        queued = await session.scalar(
            select(func.count())
            .select_from(DwhOutboxModel)
            .where(DwhOutboxModel.entity_id.in_([first.id, created.id]))
        )
    assert queued >= 3  # first's two versions and the new one


async def test_a_stale_version_aborts_everything(storage) -> None:
    repo, _, supplier_id = storage
    now = datetime.now(UTC)
    one = await repo.create(uuid4(), tactic(supplier_id), "bi", now)
    two = await repo.create(uuid4(), tactic(supplier_id, product_sku="SKU-2"), "bi", now)

    with pytest.raises(ConflictError):
        await repo.supersede(
            [Supersede(one.id, 1, None, "x"), Supersede(two.id, 9, None, "x")], "bi", now
        )

    assert (await repo.get(one.id)).data.status == "activo"  # rolled back
    changed = await repo.supersede([Supersede(one.id, None, None, "Duplicado")], "bi", now)
    assert changed[0].data.status == "anulado" and changed[0].void_reason == "Duplicado"
