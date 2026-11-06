"""In-memory ports for the retained orders validator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.modules.order_validation.application.service import OrderValidationService
from app.modules.order_validation.domain.entities import (
    Agreement,
    Batch,
    BatchPage,
    CustomerScope,
    LineResult,
    ManualApproval,
    OrderLine,
    ProductInfo,
    StoredLine,
)
from app.modules.order_validation.domain.rules import summarize_batch
from app.modules.order_validation.infrastructure.order_file import SpreadsheetOrderReader
from tests.modules.supplier_supports.conftest import FakeManagers, FixedCalendar, person

MANAGER = "comercial@jdh.com.co"


def agreement(**values) -> Agreement:
    """A support of supplier 359 for every client and SKU unless told otherwise."""

    base = {
        "id": uuid4(),
        "supplier_code": "359",
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "status": "activo",
    }
    return Agreement(**{**base, **values})


class FakeAgreementSource:
    """Supports, product margins and clients kept in memory."""

    def __init__(self) -> None:
        self.items: list[Agreement] = []
        self.catalog: dict[str, ProductInfo] = {}
        self.clients: dict[str, CustomerScope] = {}

    async def agreements(self, supplier_codes, skus, nits) -> list[Agreement]:
        return [
            item
            for item in self.items
            if (item.supplier_code in supplier_codes or item.supplier_code is None)
            and (item.product_sku is None or item.product_sku in skus)
            and (item.customer_nit is None or item.customer_nit in nits)
        ]

    async def products(self, skus) -> dict[str, ProductInfo]:
        return {sku: self.catalog[sku] for sku in skus if sku in self.catalog}

    async def customers(self, nits) -> dict[str, CustomerScope]:
        return {nit: self.clients[nit] for nit in nits if nit in self.clients}


class FakeBatchRepository:
    """Keeps files and lines in dicts."""

    def __init__(self) -> None:
        self.batches: dict[UUID, Batch] = {}
        self.lines_by_batch: dict[UUID, list[StoredLine]] = {}
        self.approved: dict[UUID, dict[str, ManualApproval]] = {}

    def _counted(self, batch: Batch, lines: Sequence[StoredLine]) -> Batch:
        total, orders, ready, ready_orders, released_orders = summarize_batch(
            ((item.line, item.result) for item in lines),
            self.approved.get(batch.id, {}),
            batch.margin_threshold,
        )
        return replace(
            batch,
            line_count=total,
            order_count=orders,
            ready_line_count=ready,
            ready_order_count=ready_orders,
            released_order_count=released_orders,
        )

    async def create(self, batch_id, file_name, actor, now, lines, margin_threshold) -> Batch:
        stored = [StoredLine(uuid4(), line, result) for line, result in lines]
        batch = Batch(batch_id, file_name, actor, now, actor, now, 0, 0, 0, 0, margin_threshold)
        self.lines_by_batch[batch_id] = stored
        self.batches[batch_id] = self._counted(batch, stored)
        return self.batches[batch_id]

    async def get(self, batch_id: UUID) -> Batch | None:
        return self.batches.get(batch_id)

    async def list(self, page: int, size: int) -> BatchPage:
        items = sorted(self.batches.values(), key=lambda item: item.uploaded_at, reverse=True)
        return BatchPage(items[(page - 1) * size : page * size], len(items), page, size)

    async def lines(self, batch_id: UUID) -> list[StoredLine]:
        return list(self.lines_by_batch.get(batch_id, []))

    async def save_results(
        self,
        batch_id: UUID,
        results: Sequence[tuple[UUID, LineResult]],
        actor: str,
        now: datetime,
        margin_threshold: Decimal,
    ) -> Batch:
        if batch_id not in self.batches:
            raise NotFoundError("Validación no encontrada")
        by_id = dict(results)
        stored = [
            replace(item, result=by_id.get(item.id, item.result))
            for item in self.lines_by_batch[batch_id]
        ]
        self.lines_by_batch[batch_id] = stored
        batch = replace(
            self.batches[batch_id],
            validated_by=actor,
            validated_at=now,
            margin_threshold=margin_threshold,
        )
        self.batches[batch_id] = self._counted(batch, stored)
        return self.batches[batch_id]

    async def set_threshold(self, batch_id: UUID, margin_threshold: Decimal) -> Batch | None:
        if batch_id not in self.batches:
            return None
        batch = replace(self.batches[batch_id], margin_threshold=margin_threshold)
        self.batches[batch_id] = self._counted(batch, self.lines_by_batch[batch_id])
        return self.batches[batch_id]

    async def approvals(self, batch_id: UUID) -> dict[str, ManualApproval]:
        return dict(self.approved.get(batch_id, {}))

    async def approve(self, batch_id: UUID, approvals: Sequence[ManualApproval]) -> Batch:
        if batch_id not in self.batches:
            raise NotFoundError("Validación no encontrada")
        current = self.approved.setdefault(batch_id, {})
        if any(item.document in current for item in approvals):
            raise ConflictError("ya aprobado")
        current.update({item.document: item for item in approvals})
        self.batches[batch_id] = self._counted(
            self.batches[batch_id], self.lines_by_batch[batch_id]
        )
        return self.batches[batch_id]

    async def remove_approval(self, batch_id: UUID, document: str) -> bool:
        if self.approved.get(batch_id, {}).pop(document, None) is None:
            return False
        self.batches[batch_id] = self._counted(
            self.batches[batch_id], self.lines_by_batch[batch_id]
        )
        return True

    async def delete(self, batch_id: UUID) -> bool:
        self.lines_by_batch.pop(batch_id, None)
        self.approved.pop(batch_id, None)
        return self.batches.pop(batch_id, None) is not None


def line(**values) -> OrderLine:
    """A retained line of supplier 359 at 10 % margin."""

    base = {
        "row_number": 2,
        "document": "PV-1",
        "customer_nit": "900315138",
        "sku": "18294",
        "supplier_code": "359",
        "supplier_name": "LOCERIA COLOMBIANA S.A.",
        "unit_price": Decimal("100000"),
        "quantity": Decimal("10"),
        "margin_pct": Decimal("0.10"),
        "channel": "HOTELES Y CLUBES",
    }
    return OrderLine(**{**base, **values})


@pytest.fixture
def source() -> FakeAgreementSource:
    return FakeAgreementSource()


@pytest.fixture
def batches() -> FakeBatchRepository:
    return FakeBatchRepository()


@pytest.fixture
def service(batches, source) -> OrderValidationService:
    return OrderValidationService(
        repository=batches,
        agreements=source,
        reader=SpreadsheetOrderReader(),
        managers=FakeManagers([MANAGER]),
        calendar=FixedCalendar(),
        max_upload_bytes=1024 * 1024,
        max_lines=1000,
    )


@pytest.fixture
def manager():
    return person(MANAGER)
