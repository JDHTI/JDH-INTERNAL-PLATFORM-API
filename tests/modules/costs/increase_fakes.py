"""In-memory doubles of the supplier increase ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import NotFoundError
from app.modules.costs.domain.increases import (
    REQUEST_STATUSES,
    IncreaseRequest,
    IncreaseRequestPage,
    RequestLine,
    SupplierItem,
    with_coverage,
    with_tax,
)
from app.modules.costs.domain.ports import LedgerEntry

D = Decimal


def item(
    sku: str,
    old_cost: str | None = "1000",
    icui: str | None = None,
    ibua: str | None = None,
    description: str | None = None,
) -> SupplierItem:
    old = None if old_cost is None else D(old_cost)
    fraction = None if icui is None else D(icui)
    per_unit = None if ibua is None else D(ibua)
    return SupplierItem(
        sku=sku,
        supplier_sku=f"0{sku}",
        ean=f"770{sku}",
        nit="890928577",
        supplier_label="50 - ASCENDER S.A.",
        description=description or f"Producto {sku}",
        pack_factor=D("6"),
        state="Activo",
        category="ALIMENTOS",
        tax_group="1614",
        icui_pct=fraction,
        ibua=per_unit,
        old_cost=old,
        old_cost_with_tax=with_tax(old, fraction, per_unit),
    )


class FakeItems:
    def __init__(self, *items: SupplierItem, code: str = "50") -> None:
        self.code = code
        self.list = list(items)

    async def items(self, supplier_code: str, skus=None) -> list[SupplierItem]:
        if supplier_code != self.code:
            return []
        return [i for i in self.list if skus is None or i.sku in skus]


class FakeLedger:
    def __init__(self, margins: Mapping[str, Decimal | None] | None = None) -> None:
        self.margin_by_sku = dict(margins or {})
        self.entries: list[LedgerEntry] = []

    async def margins(self, skus: Sequence[str]) -> dict[str, Decimal | None]:
        return {sku: self.margin_by_sku.get(sku) for sku in skus}

    async def record(self, entries, actor: str, now: datetime) -> dict[UUID, UUID]:
        self.entries.extend(entries)
        first: dict[UUID, UUID] = {}
        for entry in entries:
            first.setdefault(entry.line_id, uuid4())
        return first


class FakeIncreaseRepository:
    def __init__(self) -> None:
        self.requests: dict[UUID, IncreaseRequest] = {}

    async def create(self, request: IncreaseRequest) -> None:
        self.requests[request.id] = request

    async def page(self, status, supplier, page, size) -> IncreaseRequestPage:
        items = [r for r in self.requests.values() if not supplier or r.supplier_code == supplier]
        counts = {value: sum(1 for r in items if r.status == value) for value in REQUEST_STATUSES}
        chosen = [r for r in items if not status or r.status == status]
        chosen.sort(key=lambda r: r.created_at, reverse=True)
        start = (page - 1) * size
        return IncreaseRequestPage(
            tuple(chosen[start : start + size]), len(chosen), page, size, counts
        )

    async def get(self, request_id: UUID, with_lines: bool = True) -> IncreaseRequest | None:
        request = self.requests.get(request_id)
        if request is None:
            return None
        lines = tuple(with_coverage(request.lines))
        return replace(
            request,
            line_count=len(lines),
            accepted_count=sum(1 for line in lines if line.accepted),
            lines=lines if with_lines else (),
        )

    async def update_line(self, request_id: UUID, line: RequestLine, now) -> None:
        request = self.requests[request_id]
        if not any(item.id == line.id for item in request.lines):
            raise NotFoundError("Línea no encontrada")
        lines = tuple(line if item.id == line.id else item for item in request.lines)
        self.requests[request_id] = replace(request, lines=lines)

    async def decide(self, request_id, status, actor, now, note, ledger_ids) -> None:
        request = self.requests[request_id]
        lines = tuple(replace(line, ledger_id=ledger_ids.get(line.id)) for line in request.lines)
        self.requests[request_id] = replace(
            request,
            status=status,
            decided_by=actor,
            decided_at=now,
            decision_note=note,
            lines=lines,
        )
