"""Doubles of Nivel de servicio: the warehouse, an in-memory repository and the policies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import NotFoundError
from app.modules.service_level.application.access import ServiceLevelAccess
from app.modules.service_level.application.service import ServiceLevelService, reason_code
from app.modules.service_level.domain.entities import (
    CUSTOMER,
    NO_REASON_LABEL,
    PENDING_DIMENSIONS,
    PRODUCT,
    ZERO,
    Bands,
    Classified,
    ClassRun,
    ClassValue,
    Cuts,
    Group,
    LineFilters,
    LineRow,
    OrderLine,
    Reason,
    Totals,
)
from app.modules.service_level.domain.ports import FilterChoices, StoreResult

DAY = date(2026, 11, 20)
TODAY = date(2026, 11, 26)
NOW = datetime(2026, 11, 26, 17, 0, tzinfo=UTC)


def line(
    key: str,
    *,
    day: date = DAY,
    order: str | None = None,
    co: str = "003",
    sku: str = "1001",
    ordered: str = "10",
    dispatched: str = "10",
    value: str = "1000",
    supplier: tuple[str, str] = ("8", "TEAM FOODS"),
    seller: tuple[str, str] = ("v1", "CASTRILLON GASPAR DANIELA"),
    nit: str = "900100200",
) -> OrderLine:
    asked = Decimal(ordered)
    pending = max(asked - Decimal(dispatched), ZERO)
    amount = Decimal(value)
    return OrderLine(
        line_key=key,
        order_key=order or f"pv-{key}",
        document=f"PV-{order or key}",
        co=co,
        co_name={"003": "CENTRO", "001": "ANTIOQUIA"}.get(co, co),
        order_date=day,
        updated_at=None,
        warehouse_code="BTA9",
        warehouse_name="BODEGA BOGOTA",
        sku=sku,
        description=f"PRODUCTO {sku}",
        supplier_code=supplier[0],
        supplier_name=supplier[1],
        customer_key=f"c-{nit}",
        customer_nit=nit,
        customer_name=f"CLIENTE {nit}",
        customer_branch="001",
        seller_key=seller[0],
        seller_name=seller[1],
        qty_ordered=asked,
        qty_dispatched=Decimal(dispatched),
        qty_pending=pending,
        value_ordered=amount,
        value_pending=amount * pending / asked if asked else ZERO,
    )


class FakeSource:
    def __init__(self, *lines: OrderLine, available: bool = True) -> None:
        self.rows = list(lines)
        self.is_available = available
        self.reads: list[tuple[date, date]] = []

    async def available(self) -> bool:
        return self.is_available

    async def lines(self, since: date, until: date) -> list[OrderLine]:
        self.reads.append((since, until))
        return [row for row in self.rows if since <= row.order_date <= until]


class FakePolicy:
    def __init__(self, manage: bool = False, read: bool = False) -> None:
        self.manage = manage
        self.read = read or manage

    async def is_manager(self, principal) -> bool:
        return self.manage

    async def can_read(self, principal) -> bool:
        return self.read


def _sum(rows: list[LineRow]) -> Totals:
    orders = {row.line.order_key for row in rows}
    pending = [row for row in rows if row.line.is_pending]
    return Totals(
        orders=len(orders),
        orders_pending=len({row.line.order_key for row in pending}),
        lines=len(rows),
        lines_pending=len(pending),
        qty=sum((row.line.qty_ordered for row in rows), ZERO),
        qty_pending=sum((row.line.qty_pending for row in rows), ZERO),
        value=sum((row.line.value_ordered for row in rows), ZERO),
        value_pending=sum((row.line.value_pending for row in rows), ZERO),
    )


def _class_ok(wanted: str | None, value: str | None) -> bool:
    if wanted is None:
        return True
    return value is None if wanted == "none" else value == wanted


def _dimension(row: LineRow, dimension: str) -> tuple:
    line_, reason = row.line, row.reason
    reason_key = (str(reason.id) if reason else None, reason.name if reason else NO_REASON_LABEL)
    return {
        "co": (line_.co, line_.co_name),
        "warehouse": (line_.warehouse_code, line_.warehouse_name),
        "supplier": (line_.supplier_code, line_.supplier_name),
        "seller": (line_.seller_key, line_.seller_name),
        "customer": (line_.customer_nit, line_.customer_name),
        "responsible": (
            reason.responsible if reason else None,
            reason.responsible if reason else NO_REASON_LABEL,
        ),
        "reason": reason_key,
        "product": (line_.sku, line_.description),
        "reason_item": (*reason_key, line_.sku, line_.description),
        "product_class": (row.product_class, row.product_class),
        "customer_class": (row.customer_class, row.customer_class),
    }[dimension]


class FakeRepository:
    """Everything the SQL repository sums, summed in Python."""

    def __init__(self, reasons: Sequence[tuple[str, str]] = ()) -> None:
        self.reason_rows: dict[UUID, Reason] = {}
        for index, (name, responsible) in enumerate(reasons, start=1):
            reason = Reason(uuid4(), reason_code(name), name, responsible, True, index * 10)
            self.reason_rows[reason.id] = reason
        self.stored: dict[str, OrderLine] = {}
        self.voided: set[str] = set()
        self.synced: dict[str, datetime] = {}
        self.line_reasons: dict[str, tuple[UUID, str | None, str, datetime]] = {}
        self.classes: dict[str, list[Classified]] = {}
        self.runs: list[ClassRun] = []

    def reason(self, name: str) -> Reason:
        return next(item for item in self.reason_rows.values() if item.name == name)

    # --- reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        items = [r for r in self.reason_rows.values() if include_inactive or r.is_active]
        return sorted(items, key=lambda r: (r.sort_order, r.name))

    async def get_reason(self, reason_id: UUID) -> Reason | None:
        return self.reason_rows.get(reason_id)

    async def create_reason(self, code: str, name: str, responsible: str) -> Reason:
        last = max((r.sort_order for r in self.reason_rows.values()), default=0)
        reason = Reason(uuid4(), code, name, responsible, True, last + 10)
        self.reason_rows[reason.id] = reason
        return reason

    async def update_reason(self, reason_id, *, name, responsible, is_active) -> Reason:
        reason = self.reason_rows.get(reason_id)
        if reason is None:
            raise NotFoundError("Motivo no encontrado")
        changes = {}
        if name is not None:
            changes.update(name=name, code=reason_code(name))
        if responsible is not None:
            changes["responsible"] = responsible
        if is_active is not None:
            changes["is_active"] = is_active
        self.reason_rows[reason_id] = replace(reason, **changes)
        return self.reason_rows[reason_id]

    async def responsibles(self) -> list[str]:
        return sorted({r.responsible for r in self.reason_rows.values() if r.is_active})

    # --- copy ---

    async def has_lines(self) -> bool:
        return bool(self.stored)

    async def store_lines(self, since, until, lines, now) -> StoreResult:
        if not lines:
            return StoreResult(0, 0, 0)
        for item in lines:
            self.stored[item.line_key] = item
            self.synced[item.line_key] = now
            self.voided.discard(item.line_key)
        removed = voided = 0
        for key, item in list(self.stored.items()):
            if since <= item.order_date <= until and self.synced[key] < now:
                if key in self.line_reasons:
                    if key not in self.voided:
                        self.voided.add(key)
                        voided += 1
                else:
                    del self.stored[key]
                    removed += 1
        return StoreResult(len({item.line_key for item in lines}), removed, voided)

    async def data_as_of(self) -> datetime | None:
        return max(self.synced.values(), default=None)

    # --- reading ---

    def _klass(self, kind: str, co: str, key: str | None) -> str | None:
        for item in self.classes.get(kind, []):
            if item.co == co and item.key == key:
                return item.klass
        return None

    def _row(self, item: OrderLine) -> LineRow:
        given = self.line_reasons.get(item.line_key)
        reason = self.reason_rows.get(given[0]) if given else None
        return LineRow(
            line=item,
            reason=reason,
            note=given[1] if given else None,
            reason_by=given[2] if given else None,
            reason_at=given[3] if given else None,
            product_class=self._klass(PRODUCT, item.co or "", item.sku),
            customer_class=self._klass(CUSTOMER, "", item.customer_nit),
        )

    def _rows(self, filters: LineFilters) -> list[LineRow]:
        rows = []
        for key, item in self.stored.items():
            if key in self.voided or not filters.since <= item.order_date <= filters.until:
                continue
            row = self._row(item)
            checks = (
                (filters.co, item.co),
                (filters.warehouse, item.warehouse_code),
                (filters.supplier, item.supplier_code),
                (filters.seller, item.seller_key),
                (filters.customer, item.customer_nit),
                (filters.reason_id, row.reason.id if row.reason else None),
                (filters.responsible, row.reason.responsible if row.reason else None),
            )
            if any(wanted is not None and wanted != value for wanted, value in checks):
                continue
            if filters.without_reason and row.reason is not None:
                continue
            if filters.pending is not None and item.is_pending != filters.pending:
                continue
            if not _class_ok(filters.product_class, row.product_class):
                continue
            if not _class_ok(filters.customer_class, row.customer_class):
                continue
            text = f"{item.sku} {item.description} {item.document}".lower()
            if filters.q and filters.q.lower() not in text:
                continue
            rows.append(row)
        return rows

    async def page(self, filters, sort, descending, page, size):
        rows = self._rows(filters)
        key = {
            "date": lambda r: (r.line.order_date, r.line.document or ""),
            "value_pending": lambda r: r.line.value_pending,
            "value": lambda r: r.line.value_ordered,
        }.get(sort, lambda r: r.line.line_key)
        rows.sort(key=key, reverse=descending)
        return rows[(page - 1) * size : page * size], len(rows)

    async def totals(self, filters) -> Totals:
        return _sum(self._rows(filters))

    async def groups(self, filters, dimension, sort, descending, limit, offset):
        rows = self._rows(filters)
        if dimension in PENDING_DIMENSIONS:
            rows = [row for row in rows if row.line.is_pending]
        grouped: dict[tuple, list[LineRow]] = defaultdict(list)
        for row in rows:
            grouped[_dimension(row, dimension)].append(row)
        groups = []
        for values, members in grouped.items():
            detail = values[2:] if len(values) > 2 else (None, None)
            groups.append(Group(values[0], values[1], _sum(members), *detail))
        measure = {
            "value": lambda g: g.totals.value,
            "value_pending": lambda g: g.totals.value_pending,
            "key": lambda g: g.key or "",
            "name": lambda g: g.name or "",
        }.get(sort, lambda g: g.totals.value)
        groups.sort(key=measure, reverse=descending)
        end = None if limit is None else offset + limit
        return groups[offset:end], len(groups)

    async def daily(self, filters):
        by_day: dict[date, list[LineRow]] = defaultdict(list)
        for row in self._rows(filters):
            by_day[row.line.order_date].append(row)
        return [(day, _sum(by_day[day])) for day in sorted(by_day)]

    async def choices(self, since, until) -> FilterChoices:
        rows = [i for k, i in self.stored.items() if since <= i.order_date <= until]
        return FilterChoices(
            cos={i.co: i.co_name for i in rows if i.co},
            warehouses={i.warehouse_code: i.warehouse_name for i in rows if i.warehouse_code},
            suppliers={i.supplier_code: i.supplier_name for i in rows if i.supplier_code},
            sellers={i.seller_key: i.seller_name for i in rows if i.seller_key},
        )

    # --- reasons of lines ---

    async def lines_by_key(self, keys) -> dict[str, LineRow]:
        return {
            key: self._row(self.stored[key])
            for key in keys
            if key in self.stored and key not in self.voided
        }

    async def set_reasons(self, keys, reason_id, note, actor, now) -> int:
        for key in keys:
            if reason_id is None:
                self.line_reasons.pop(key, None)
            else:
                self.line_reasons[key] = (reason_id, note, actor, now)
        return len(keys)

    # --- ABCD ---

    async def class_values(self, kind, since, until) -> list[ClassValue]:
        sums: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
        for key, item in self.stored.items():
            if key in self.voided or not since <= item.order_date <= until:
                continue
            if kind == PRODUCT:
                sums[(item.co or "", item.sku)] += item.value_ordered
            elif item.customer_nit:
                sums[("", item.customer_nit)] += item.value_ordered
        return [ClassValue(co, key, value) for (co, key), value in sums.items()]

    async def replace_classes(self, classes: Mapping[str, Sequence[Classified]], run) -> None:
        self.classes = {kind: list(items) for kind, items in classes.items()}
        self.runs.append(run)

    async def latest_class_run(self) -> ClassRun | None:
        return self.runs[-1] if self.runs else None

    async def stored_class_values(self, kind, co) -> list[Decimal]:
        return [item.value for item in self.classes.get(kind, []) if co is None or item.co == co]


def make_service(
    repository: FakeRepository | None = None,
    source: FakeSource | None = None,
    *,
    manage: bool = True,
    read: bool = True,
    today: date = TODAY,
) -> ServiceLevelService:
    return ServiceLevelService(
        repository or FakeRepository(),
        source or FakeSource(),
        ServiceLevelAccess(FakePolicy(manage, read), FakePolicy(read=read)),
        cuts=Cuts(),
        bands=Bands(),
        class_months=2,
        sync_days=45,
        start_date=date(2026, 9, 1),
        max_range_days=370,
        today=lambda: today,
        clock=lambda: NOW,
    )
