"""Opt-in integration on a migrated disposable PostgreSQL database.

Set SUPPORT_REQUEST_TEST_DATABASE_URL to a dedicated database upgraded to head.
Audit rows deliberately persist: the tests verify they cannot be deleted.
"""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Environment, Settings
from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.main import create_app
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.infrastructure.models import (
    SupportJdhEventModel,
    SupportRequestModel,
)
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository
from app.modules.supplier_supports.infrastructure.requests_repository import (
    SqlAlchemySupportRequestRepository,
)
from tests.modules.supplier_supports.conftest import FakeLookup, FakeManagers, FixedCalendar, person

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)
D = Decimal


@pytest.fixture
async def storage():
    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    master = SqlAlchemySupportRepository(database)
    requests = SqlAlchemySupportRequestRepository(database)
    supplier_id = uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(CommercialSupplierModel(id=supplier_id, code=str(supplier_id), name="Test"))
    data = SupportData(
        kind="tactico_cliente",
        calc_method="costo_neto",
        commercial_supplier_id=supplier_id,
        supplier_value=D(80),
        reference_cost=D(100),
        value_unit="caja",
        pack_factor=D(10),
        product_sku="TEST",
        customer_name="Test customer",
    )
    support = await master.create(uuid4(), data, "bi", datetime.now(UTC))
    try:
        yield database, master, requests, support
    finally:
        await database.dispose()


async def propose(requests, support, value="70", actor=None):
    actor = actor or person("sales")
    return await requests.create(
        support.id,
        support.version,
        D(value),
        "Nuevo pacto solicitado",
        actor.user_id,
        actor.email,
        FixedCalendar().today(),
    )


async def test_approval_cancellation_and_immutable_history(storage):
    database, master, requests, support = storage
    pending = await propose(requests, support)
    assert (await master.get(support.id)).data.jdh_net_cost is None
    approved = await requests.decide(pending["id"], 1, 1, "bi", True, None, FixedCalendar().today())
    assert approved["status"] == "aprobada" and approved["decided_by"] == "bi"
    current = await master.get(support.id)
    assert current.data.jdh_net_cost == 70 and current.data.supplier_value == 80
    with pytest.raises(ConflictError):
        await requests.decide(pending["id"], 2, 2, "bi2", True, None, FixedCalendar().today())
    await requests.cancel(support.id, 2, "bi2", "Cancelar JDH")
    cancelled = await master.get(support.id)
    assert cancelled.version == 3 and cancelled.data.jdh_net_cost is None
    assert cancelled.data.supplier_value == 80 and cancelled.data.status == "activo"
    events = await requests.history(support.id)
    assert {e["event_type"] for e in events} >= {"creacion", "solicitada", "aprobada", "cancelada"}
    await requests.cancel(support.id, 2, "bi2", "Reintento")
    assert len(await requests.history(support.id)) == len(events)
    for sql in (
        "UPDATE supplier_support_jdh_events SET reason='alterado' WHERE support_id=:id",
        "DELETE FROM supplier_support_jdh_events WHERE support_id=:id",
        "DELETE FROM supplier_support_requests WHERE support_id=:id",
        "UPDATE supplier_support_requests SET value=1 WHERE support_id=:id",
    ):
        async with database.session_factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(sql), {"id": support.id})
    assert (await requests.get(pending["id"], None))["value"] == 70
    new = await propose(requests, cancelled, "60")
    assert new["status"] == "pendiente"


async def test_concurrent_approvals_and_explicit_rereview(storage):
    _, master, requests, support = storage
    first, second = await propose(requests, support), await propose(requests, support, "65")
    results = await asyncio.gather(
        requests.decide(first["id"], 1, 1, "bi1", True, None, FixedCalendar().today()),
        requests.decide(second["id"], 1, 1, "bi2", True, None, FixedCalendar().today()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert (await master.get(support.id)).version == 2
    loser = first if isinstance(results[0], ConflictError) else second
    assert (await requests.get(loser["id"], None))["status"] == "pendiente"
    await requests.decide(loser["id"], 1, 2, "bi3", True, None, FixedCalendar().today())
    assert (await master.get(support.id)).version == 3


async def test_failed_audit_rolls_back_decision_and_master(storage, monkeypatch):
    _, master, requests, support = storage
    pending = await propose(requests, support)
    before = await requests.history(support.id)

    def invalid_event(session, support_id, request_id, event_type, actor, reason, old, new):
        session.add(
            SupportJdhEventModel(
                id=uuid4(),
                support_id=support_id,
                request_id=uuid4(),
                event_type=event_type,
                actor=actor,
            )
        )

    monkeypatch.setattr(requests, "_event", invalid_event)
    with pytest.raises(DBAPIError):
        await requests.decide(pending["id"], 1, 1, "bi", True, None, FixedCalendar().today())
    current = await master.get(support.id)
    assert current.version == 1 and current.data.jdh_net_cost is None
    assert (await requests.get(pending["id"], None))["status"] == "pendiente"
    assert len(await requests.history(support.id)) == len(before)


async def test_percentage_is_shared_without_changing_supplier(storage):
    _, master, requests, support = storage
    support = await master.update(
        support.id,
        replace(support.data, calc_method="pct_costo", supplier_value=D("0.1")),
        1,
        "bi",
        datetime.now(UTC),
    )
    first = await propose(requests, support, "0.05")
    second = await propose(requests, support, "0.07")
    results = await asyncio.gather(
        requests.decide(first["id"], 1, 2, "bi1", True, None, FixedCalendar().today()),
        requests.decide(first["id"], 1, 2, "bi2", True, None, FixedCalendar().today()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    current = await master.get(support.id)
    assert current.data.jdh_pct == D("0.05") and current.data.supplier_value == D("0.1")
    assert (await requests.get(second["id"], None))["status"] == "pendiente"
    assert (await requests.get(first["id"], None))["snapshot"]["jdh_pct"] is None


async def test_stale_ineligible_and_rejection_do_not_change_master(storage):
    _, master, requests, support = storage
    pending = await propose(requests, support)
    changed = await master.update(
        support.id, replace(support.data, jdh_net_cost=D(75)), 1, "admin", datetime.now(UTC)
    )
    with pytest.raises(ConflictError):
        await requests.decide(pending["id"], 1, 1, "bi", True, None, FixedCalendar().today())
    rejected = await requests.decide(
        pending["id"], 1, None, "bi", False, "No autorizado", FixedCalendar().today()
    )
    assert rejected["status"] == "rechazada"
    assert (await master.get(support.id)).data.jdh_net_cost == 75
    assert any(
        e["event_type"] == "actualizacion_maestro" for e in await requests.history(support.id)
    )
    expired = await master.update(
        support.id,
        replace(changed.data, valid_to=FixedCalendar().today() - timedelta(days=1)),
        2,
        "admin",
        datetime.now(UTC),
    )
    with pytest.raises(ConflictError):
        await propose(requests, expired)


async def test_http_permissions_mine_and_real_workflow(storage):
    database, master, requests, support = storage
    sales, bi = person("sales"), person("bi")
    policy, requester = FakeManagers(["bi"]), FakeManagers(["sales"])
    service = SupportRequestService(requests, policy, requester, FixedCalendar())
    supports = SupplierSupportService(master, FakeLookup(), policy, FixedCalendar())
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: sales
    with (
        app.state.container.v1_container.supplier_support_request_service.override(service),
        app.state.container.v1_container.supplier_support_service.override(supports),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            root = "/api/v1"
            url = f"{root}/supplier-supports/{support.id}"
            response = await client.post(
                url + "/requests", json={"version": 1, "value": "70", "reason": "Solicitud"}
            )
            assert response.status_code == 201, response.text
            request_id = response.json()["id"]
            request_url = f"{root}/supplier-support-requests/{request_id}"
            assert (await client.get(request_url)).json()["support"][
                "supplier_value"
            ] == "80.000000"
            assert (
                await client.post(
                    request_url + "/approve", json={"version": 1, "support_version": 1}
                )
            ).status_code == 403
            assert (await client.delete(url + "/jdh-support")).status_code == 422
            app.dependency_overrides[get_current_principal] = lambda: person("other")
            assert (await client.get(request_url)).status_code == 403
            app.dependency_overrides[get_current_principal] = lambda: bi
            assert (await client.get(request_url + "?mine=true")).status_code == 404
            mine = await client.get(root + "/supplier-support-requests?mine=true")
            assert mine.status_code == 200 and mine.json()["total"] == 0
            response = await client.post(
                request_url + "/approve", json={"version": 1, "support_version": 1}
            )
            assert response.status_code == 200, response.text
            assert response.json()["value"] == "70.000000"
            detail = (await client.get(url)).json()
            assert detail["jdh_net_cost"] == "70.000000"
            assert detail["derived"]["net_cost"] == "70.000000"
            history = await client.get(url + "/jdh-history")
            assert history.status_code == 200 and len(history.json()) >= 3
            response = await client.request(
                "DELETE", url + "/jdh-support", json={"version": 2, "reason": "Cancelar"}
            )
            assert response.status_code == 200 and response.json()["jdh_net_cost"] is None
    async with database.session_factory() as session:
        model = await session.scalar(
            select(SupportRequestModel).where(SupportRequestModel.id == request_id)
        )
        assert model.status == "aprobada"
