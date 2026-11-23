"""Opt-in: internal support requests on PostgreSQL (SUPPORT_REQUEST_TEST_DATABASE_URL)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel
from app.modules.quotations.infrastructure.models import QuotationModel
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.internal_requests import (
    Approval,
    NewRequest,
    RequestItem,
)
from app.modules.supplier_supports.infrastructure.internal_requests_repository import (
    SqlAlchemyInternalRequestRepository,
)
from app.modules.supplier_supports.infrastructure.models import SupplierSupportModel
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)
D = Decimal


@pytest.fixture
async def storage():
    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    supplier_id, quotation_id, now = uuid4(), uuid4(), datetime.now(UTC)
    async with database.session_factory() as session, session.begin():
        session.add(CommercialSupplierModel(id=supplier_id, code=supplier_id.hex[:12], name="Prov"))
        await session.flush()
        session.add(
            QuotationModel(
                id=quotation_id,
                customer_nit="800116299",
                window_months=6,
                status="borrador",
                built_at=now,
                max_points_below_target=D("0.01"),
                created_by_id=uuid4(),
                created_by="ventas@jdh.com.co",
                created_at=now,
                updated_by="ventas@jdh.com.co",
                updated_at=now,
                valid_from=now.date(),
            )
        )
    try:
        yield database, supplier_id, quotation_id
    finally:
        await database.dispose()


def new_request(supplier_id, quotation_id) -> NewRequest:
    return NewRequest(
        quotation_id=quotation_id,
        quotation_label="Cotización prueba",
        customer_nit="800116299",
        customer_name="Cliente",
        commercial_supplier_id=supplier_id,
        supplier_code="P1",
        supplier_name="Prov",
        requested_for=date(2026, 11, 13),
        agreed_with="Gerente",
        reason="Negociación",
        items=(
            RequestItem("SKU-A", D("0.16"), cost=D("80")),
            RequestItem("SKU-B", D("0.15"), cost=D("50")),
        ),
    )


async def test_approving_creates_linked_supports_published_to_the_dwh(storage) -> None:
    database, supplier_id, quotation_id = storage
    repo = SqlAlchemyInternalRequestRepository(database)
    actor = uuid4()
    created = await repo.create(
        new_request(supplier_id, quotation_id), actor, "v", datetime.now(UTC)
    )
    assert created.number > 0 and [item.sku for item in created.items] == ["SKU-A", "SKU-B"]
    with pytest.raises(ConflictError):  # SKU-A already pending in this quotation
        await repo.create(new_request(supplier_id, quotation_id), actor, "v", datetime.now(UTC))

    data = SupportData(
        kind="excepcion_margen",
        calc_method="margen_minimo",
        commercial_supplier_id=supplier_id,
        customer_nit="800116299",
        product_sku="SKU-A",
        min_margin_pct=D("0.14"),
        supported_margin_pct=D("0.14"),
        valid_from=date(2026, 11, 13),
        valid_to=date(2026, 11, 13),
    )
    approved = await repo.approve(
        created.id,
        1,
        Approval(
            "unica_vez",
            date(2026, 11, 13),
            date(2026, 11, 13),
            "Puntual",
            {"SKU-A": D("0.14")},
            {"SKU-A": data},
        ),
        "bi@jdh.com.co",
        datetime.now(UTC),
    )

    assert approved.status == "aprobada" and approved.version == 2
    kept = approved.items[0]
    assert kept.approved_margin_pct == D("0.14") and kept.support_id is not None
    assert approved.items[1].support_id is None
    async with database.session_factory() as session:
        support = await session.get(SupplierSupportModel, kept.support_id)
        assert support.internal_request_id == created.id
        assert support.last_origin == "solicitud_interna"
        assert (support.kind, support.calc_method) == ("excepcion_margen", "margen_minimo")
        assert support.min_margin_pct == support.supported_margin_pct == D("0.14")
        queued = (
            await session.execute(
                select(DwhOutboxModel.version).where(DwhOutboxModel.entity_id == support.id)
            )
        ).scalars()
        assert list(queued) == [1]

    # The bulk load reads it with its origin, to leave it out of matching and expiring.
    live = await SqlAlchemySupportRepository(database).live_for_suppliers([supplier_id])
    assert [item.internal_request_id for item in live] == [created.id]
    refs = await repo.latest_for_quotation(quotation_id)
    assert refs["SKU-A"].status == "aprobada" and refs["SKU-B"].approved_margin_pct is None
    with pytest.raises(ConflictError):  # already decided
        await repo.reject(created.id, 2, "No", "bi", datetime.now(UTC))


async def test_a_supplier_request_stores_a_negative_minimum_and_its_copy(storage) -> None:
    """2026-11-23: ``kind = proveedor``; the item keeps the guarantee it lowers and may ask a
    negative minimum; approving writes the copy for the customer, which never crosses the
    base guarantee."""

    database, supplier_id, quotation_id = storage
    supports = SqlAlchemySupportRepository(database)
    guarantee = SupportData(
        kind="tactico_cliente",
        calc_method="precio_neto",
        commercial_supplier_id=supplier_id,
        product_sku="SKU-G",
        value_unit="unidad",
        target_margin_pct=D("0.18"),
        supplier_min_margin_pct=D("0.13"),
        customer_nit="800116299",
    )
    base = await supports.create(uuid4(), guarantee, "bi", datetime.now(UTC))
    asked = NewRequest(
        **{
            **new_request(supplier_id, quotation_id).__dict__,
            "kind": "proveedor",
            "items": (
                RequestItem(
                    "SKU-G", D("-0.05"), target_margin_pct=D("0.13"), base_support_id=base.id
                ),
            ),
        }
    )
    repo = SqlAlchemyInternalRequestRepository(database)
    created = await repo.create(asked, uuid4(), "v", datetime.now(UTC))

    assert created.kind == "proveedor"
    assert created.items[0].base_support_id == base.id
    assert created.items[0].requested_margin_pct == D("-0.05")
    day = date(2026, 11, 13)
    copy = SupportData(
        **{
            **guarantee.__dict__,
            "supplier_min_margin_pct": D("-0.05"),
            "valid_from": day,
            "valid_to": day,
        }
    )
    approved = await repo.approve(
        created.id,
        1,
        Approval("unica_vez", day, day, "Puntual", {"SKU-G": D("-0.05")}, {"SKU-G": copy}),
        "bi@jdh.com.co",
        datetime.now(UTC),
    )

    stored = await supports.get(approved.items[0].support_id)
    assert stored.internal_request_id == created.id
    assert stored.data.supplier_min_margin_pct == D("-0.05")
    # Same class, customer and SKU as the base guarantee, but it lies on top: no crossing.
    assert [item.id for item in await supports.overlapping(guarantee)] == [base.id]
