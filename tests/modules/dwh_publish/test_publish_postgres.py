"""Opt-in end to end: platform PostgreSQL -> outbox -> publisher -> a scratch "DWH".

Set SUPPORT_REQUEST_TEST_DATABASE_URL to the platform database upgraded to head and
DWH_PUBLISH_TEST_DATABASE_URL (``postgresql://user:pass@host:port/db``) to a scratch database
standing for the warehouse. The test applies ``docs/dwh/plataforma-apoyos.sql`` to it, so the
contract handed to the DWH team and the writer's SQL are checked together.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import select

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.dwh_publish.application.service import (
    DwhPublishService,
    PublishSettings,
    PublishState,
)
from app.modules.dwh_publish.domain.entities import OutboxEvent
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel
from app.modules.dwh_publish.infrastructure.repository import SqlAlchemyOutboxRepository
from app.modules.dwh_publish.infrastructure.writer import WarehouseWriterConnection
from app.modules.supplier_supports.domain.entities import LoadPlan, SupportData
from app.modules.supplier_supports.infrastructure.publication import (
    SUPPORT_TOPIC,
    LoadTopic,
    SupportTopic,
    build_topics,
)
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository
from app.modules.supplier_supports.infrastructure.requests_repository import (
    SqlAlchemySupportRequestRepository,
)
from tests.modules.supplier_supports.conftest import person

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL")
        and os.getenv("DWH_PUBLISH_TEST_DATABASE_URL")
    ),
    reason="Platform and scratch DWH PostgreSQL URLs not set",
)
CONTRACT = Path(__file__).parents[3] / "docs" / "dwh" / "plataforma-apoyos.sql"
D = Decimal


def _writer(port: int | None = None) -> WarehouseWriterConnection:
    url = urlparse(os.environ["DWH_PUBLISH_TEST_DATABASE_URL"])
    return WarehouseWriterConnection(
        host=url.hostname or "127.0.0.1",
        port=port or url.port or 5432,
        database=url.path.lstrip("/"),
        user=url.username or "postgres",
        password=url.password or "",
        ssl="disable",
        connect_timeout=2.0,
    )


@pytest.fixture
async def setup():
    dwh = await asyncpg.connect(os.environ["DWH_PUBLISH_TEST_DATABASE_URL"])
    await dwh.execute(CONTRACT.read_text(encoding="utf-8"))
    await dwh.execute(
        "TRUNCATE plataforma.apoyo_proveedor, plataforma.apoyo_proveedor_historial, "
        "plataforma.carga_apoyos"
    )
    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    supplier_id = uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(
            CommercialSupplierModel(id=supplier_id, code=f"T{supplier_id.hex[:8]}", name="Prueba")
        )
    writer = _writer()

    def service(state: PublishState | None = None, writer_=None) -> DwhPublishService:
        return DwhPublishService(
            repository=SqlAlchemyOutboxRepository(database),
            writer=writer_ or writer,
            topics=build_topics(SupportTopic(database), LoadTopic(database)),
            settings=PublishSettings(enabled=True, batch_size=500, max_attempts=2),
            state=state or PublishState(),
            configured=True,
            rand=lambda: 0.0,
        )

    try:
        yield database, dwh, supplier_id, service
    finally:
        await writer.close()
        await dwh.close()
        await database.dispose()


async def drain(service: DwhPublishService) -> int:
    published = 0
    while True:
        result = await service.publish_once()
        published += result.published
        if result.claimed == 0 or result.unavailable:
            return published


def support(supplier_id, **changes) -> SupportData:
    base = SupportData(
        kind="tactico_cliente",
        calc_method="pct_costo",
        commercial_supplier_id=supplier_id,
        supplier_value=D("0.05"),
        customer_nit="800116299",
        product_sku="1128",
        valid_from=date(2026, 11, 1),
        regionals=("ANTIOQUIA",),
    )
    return replace(base, **changes)


async def test_every_version_reaches_the_dwh_once_and_in_order(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    now = datetime.now(UTC)
    created = await repo.create(uuid4(), support(supplier_id), "bi@jdh.com.co", now)
    edited = await repo.update(
        created.id, replace(created.data, jdh_pct=D("0.02")), 1, "bi@jdh.com.co", now
    )
    await repo.void(edited.id, "Fin del acuerdo", 2, "bi@jdh.com.co", now)

    await drain(service())

    current = await dwh.fetchrow(
        "SELECT version, estado, apoyo_jdh_pct, regionales, origen, proveedor_nombre, datos "
        "FROM plataforma.apoyo_proveedor WHERE id = $1",
        created.id,
    )
    assert current["version"] == 3 and current["estado"] == "anulado"
    assert current["apoyo_jdh_pct"] == D("0.02") and current["regionales"] == ["ANTIOQUIA"]
    assert current["origen"] == "anulacion" and current["proveedor_nombre"] == "Prueba"
    history = await dwh.fetch(
        "SELECT version, origen FROM plataforma.apoyo_proveedor_historial WHERE id = $1 "
        "ORDER BY version",
        created.id,
    )
    assert [(row["version"], row["origen"]) for row in history] == [
        (1, "manual"),
        (2, "edicion"),
        (3, "anulacion"),
    ]
    async with database.session_factory() as session:
        statuses = (
            await session.execute(
                select(DwhOutboxModel.status).where(DwhOutboxModel.entity_id == created.id)
            )
        ).scalars()
        assert set(statuses) == {"publicado"}


async def test_the_jdh_approval_panel_updates_the_dwh_record(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    requests = SqlAlchemySupportRequestRepository(database)
    created = await repo.create(
        uuid4(), support(supplier_id, product_sku="13953"), "bi", datetime.now(UTC)
    )
    actor = person("ventas")
    proposal = await requests.create(
        created.id, 1, D("0.03"), "Cliente pide 3 %", actor.user_id, actor.email, date.today()
    )
    await requests.decide(proposal["id"], 1, 1, "bi@jdh.com.co", True, "Aprobado", date.today())

    await drain(service())

    row = await dwh.fetchrow(
        "SELECT version, apoyo_jdh_pct, origen, modificado_por FROM plataforma.apoyo_proveedor "
        "WHERE id = $1",
        created.id,
    )
    assert row["version"] == 2 and row["apoyo_jdh_pct"] == D("0.03")
    assert row["origen"] == "aprobacion_jdh" and row["modificado_por"] == "bi@jdh.com.co"

    await requests.cancel(created.id, 2, "bi@jdh.com.co", "Se acabó el aporte")
    await drain(service())
    row = await dwh.fetchrow(
        "SELECT version, apoyo_jdh_pct, origen FROM plataforma.apoyo_proveedor WHERE id = $1",
        created.id,
    )
    assert (row["version"], row["apoyo_jdh_pct"], row["origen"]) == (3, None, "cancelacion_jdh")


async def test_a_bulk_load_lands_with_its_trace(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    load_id, new_id = uuid4(), uuid4()
    await repo.apply_load(
        LoadPlan(
            load_id=load_id,
            file_name="Apoyos Proveedor - Prueba.xlsx",
            file_sha256="0" * 64,
            supplier_code="T",
            sheet="Apoyos",
            rows_read=1,
            creates=((new_id, support(supplier_id, product_sku="8749")),),
        ),
        "bi@jdh.com.co",
        datetime.now(UTC),
    )

    await drain(service())

    row = await dwh.fetchrow(
        "SELECT carga_id, origen FROM plataforma.apoyo_proveedor WHERE id = $1", new_id
    )
    assert row["carga_id"] == load_id and row["origen"] == "carga"
    load = await dwh.fetchrow(
        "SELECT archivo, creados FROM plataforma.carga_apoyos WHERE id = $1", load_id
    )
    assert load["archivo"] == "Apoyos Proveedor - Prueba.xlsx" and load["creados"] == 1


async def test_a_retry_or_an_old_event_changes_nothing_and_reconcile_repairs(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    now = datetime.now(UTC)
    created = await repo.create(uuid4(), support(supplier_id, product_sku="15482"), "bi", now)
    await repo.update(created.id, replace(created.data, supplier_value=D("0.07")), 1, "bi", now)
    publisher = service()
    await drain(publisher)

    # The warehouse loses the row (a rebuilt table): the daily reconciliation queues it again.
    await dwh.execute("DELETE FROM plataforma.apoyo_proveedor WHERE id = $1", created.id)
    results = {item.topic: item for item in await publisher.reconcile()}
    assert results[SUPPORT_TOPIC].queued >= 1
    await drain(publisher)
    row = await dwh.fetchrow(
        "SELECT version, valor_proveedor FROM plataforma.apoyo_proveedor WHERE id = $1",
        created.id,
    )
    assert row["version"] == 2 and row["valor_proveedor"] == D("0.07")

    # Version 1 arriving late (an old retry) does not go back in time.
    async with database.session_factory() as session:
        old = (
            await session.execute(
                select(DwhOutboxModel.payload).where(
                    DwhOutboxModel.entity_id == created.id, DwhOutboxModel.version == 1
                )
            )
        ).scalar_one()
    await SqlAlchemyOutboxRepository(database).add([OutboxEvent(SUPPORT_TOPIC, created.id, 1, old)])
    await drain(publisher)
    assert (
        await dwh.fetchval(
            "SELECT version FROM plataforma.apoyo_proveedor WHERE id = $1", created.id
        )
        == 2
    )


async def test_a_rejected_event_does_not_hold_the_rest_and_ends_aside(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    good = await repo.create(
        uuid4(), support(supplier_id, product_sku="9894"), "bi", datetime.now(UTC)
    )
    poison_id = uuid4()
    await SqlAlchemyOutboxRepository(database).add(
        [OutboxEvent(SUPPORT_TOPIC, poison_id, 1, {"id": str(poison_id), "version": 1})]
    )

    publisher = service()
    first = await publisher.publish_once()
    assert first.published >= 1 and first.retried + first.failed >= 1
    assert await dwh.fetchval(
        "SELECT count(*) FROM plataforma.apoyo_proveedor WHERE id = $1", good.id
    )
    # With max_attempts=2 the second rejection sets it aside.
    await SqlAlchemyOutboxRepository(database).retry_failed(None, datetime.now(UTC))
    async with database.session_factory() as session, session.begin():
        model = (
            await session.execute(
                select(DwhOutboxModel).where(DwhOutboxModel.entity_id == poison_id)
            )
        ).scalar_one()
        model.next_attempt_at = datetime.now(UTC)
        model.attempts = 1
    await publisher.publish_once()
    failed = await publisher.failed(10)
    assert any(item.entity_id == poison_id for item in failed)


async def test_an_unreachable_dwh_charges_nothing_and_opens_the_breaker(setup) -> None:
    database, dwh, supplier_id, service = setup
    repo = SqlAlchemySupportRepository(database)
    created = await repo.create(
        uuid4(), support(supplier_id, product_sku="7789"), "bi", datetime.now(UTC)
    )
    state = PublishState()
    down = _writer(port=1)
    try:
        for _ in range(3):
            async with database.session_factory() as session, session.begin():
                for model in (
                    await session.execute(
                        select(DwhOutboxModel).where(DwhOutboxModel.status == "pendiente")
                    )
                ).scalars():
                    model.next_attempt_at = datetime.now(UTC)
            result = await service(state, down).publish_once()
            assert result.unavailable
    finally:
        await down.close()

    assert state.paused_until is not None
    async with database.session_factory() as session:
        attempts = (
            await session.execute(
                select(DwhOutboxModel.attempts).where(DwhOutboxModel.entity_id == created.id)
            )
        ).scalar_one()
    assert attempts == 0
