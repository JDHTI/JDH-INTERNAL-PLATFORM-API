"""PostgreSQL repository of Nivel de servicio: every figure is summed in SQL over the copy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Text, and_, cast, delete, distinct, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import aliased

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.service_level.domain.entities import (
    CUSTOMER,
    NO_REASON_LABEL,
    PENDING_DIMENSIONS,
    PRODUCT,
    Classified,
    ClassRun,
    ClassValue,
    Group,
    LineFilters,
    LineRow,
    OrderLine,
    Reason,
    Totals,
)
from app.modules.service_level.domain.ports import FilterChoices, StoreResult
from app.modules.service_level.infrastructure.models import (
    ServiceLevelClassModel,
    ServiceLevelClassRunModel,
    ServiceLevelLineModel,
    ServiceLevelLineReasonModel,
    ServiceLevelReasonModel,
)

L = ServiceLevelLineModel
LR = ServiceLevelLineReasonModel
R = ServiceLevelReasonModel
K = ServiceLevelClassModel
RUN = ServiceLevelClassRunModel
PC = aliased(K, name="product_class")
CC = aliased(K, name="customer_class")
_CHUNK = 1000
_PENDING = L.qty_pending > 0
# Every column the job copies; ``line_key`` is the key.
_LINE_FIELDS = (
    "order_key",
    "document",
    "co",
    "co_name",
    "order_date",
    "erp_updated_at",
    "warehouse_code",
    "warehouse_name",
    "sku",
    "description",
    "supplier_code",
    "supplier_name",
    "customer_key",
    "customer_nit",
    "customer_name",
    "customer_branch",
    "seller_key",
    "seller_name",
    "qty_ordered",
    "qty_dispatched",
    "qty_pending",
    "value_ordered",
    "value_pending",
)

_AGGREGATES = (
    func.count(distinct(L.order_key)).label("orders"),
    func.count(distinct(L.order_key)).filter(_PENDING).label("orders_pending"),
    func.count().label("lines"),
    func.count().filter(_PENDING).label("lines_pending"),
    func.coalesce(func.sum(L.qty_ordered), 0).label("qty"),
    func.coalesce(func.sum(L.qty_pending), 0).label("qty_pending"),
    func.coalesce(func.sum(L.value_ordered), 0).label("value"),
    func.coalesce(func.sum(L.value_pending), 0).label("value_pending"),
)

_LINE_SORTS: dict[str, tuple[Any, ...]] = {
    "date": (L.order_date, L.document),
    "document": (L.document,),
    "co": (L.co, L.order_date),
    "sku": (L.sku, L.order_date),
    "supplier": (L.supplier_name, L.order_date),
    "customer": (L.customer_name, L.order_date),
    "seller": (L.seller_name, L.order_date),
    "qty_pending": (L.qty_pending,),
    "value_pending": (L.value_pending,),
    "value": (L.value_ordered,),
}

_REASON_KEY = cast(LR.reason_id, Text)
# Grouping → (key, name, detail key, detail name). Names come through ``max`` so the key alone
# groups (a supplier renamed mid-period still is one row).
_DIMENSIONS: dict[str, tuple[Any, Any, Any, Any]] = {
    "co": (L.co, func.max(L.co_name), None, None),
    "warehouse": (L.warehouse_code, func.max(L.warehouse_name), None, None),
    "supplier": (L.supplier_code, func.max(L.supplier_name), None, None),
    "seller": (L.seller_key, func.max(L.seller_name), None, None),
    "customer": (L.customer_nit, func.max(L.customer_name), None, None),
    "responsible": (R.responsible, func.coalesce(R.responsible, NO_REASON_LABEL), None, None),
    "reason": (_REASON_KEY, func.coalesce(func.max(R.name), NO_REASON_LABEL), None, None),
    "product": (L.sku, func.max(L.description), None, None),
    "reason_item": (
        _REASON_KEY,
        func.coalesce(func.max(R.name), NO_REASON_LABEL),
        L.sku,
        func.max(L.description),
    ),
    "product_class": (PC.abcd, PC.abcd, None, None),
    "customer_class": (CC.abcd, CC.abcd, None, None),
}


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _join(statement):
    return (
        statement.outerjoin(LR, LR.line_key == L.line_key)
        .outerjoin(R, R.id == LR.reason_id)
        .outerjoin(
            PC,
            and_(PC.kind == PRODUCT, PC.co == func.coalesce(L.co, ""), PC.key == L.sku),
        )
        .outerjoin(CC, and_(CC.kind == CUSTOMER, CC.co == "", CC.key == L.customer_nit))
    )


def _class_condition(column, value: str | None):
    if value is None:
        return None
    return column.is_(None) if value == "none" else column == value


def _conditions(filters: LineFilters) -> list[Any]:
    conditions: list[Any] = [
        L.voided.is_(False),
        L.order_date >= filters.since,
        L.order_date <= filters.until,
    ]
    equal = (
        (L.co, filters.co),
        (L.warehouse_code, filters.warehouse),
        (L.supplier_code, filters.supplier),
        (L.seller_key, filters.seller),
        (L.customer_nit, filters.customer),
        (R.responsible, filters.responsible),
        (LR.reason_id, filters.reason_id),
    )
    conditions.extend(column == value for column, value in equal if value is not None)
    if filters.without_reason:
        conditions.append(LR.reason_id.is_(None))
    if filters.q and filters.q.strip():
        like = _like(filters.q.strip())
        conditions.append(
            or_(
                L.sku.ilike(like, escape="\\"),
                L.description.ilike(like, escape="\\"),
                L.document.ilike(like, escape="\\"),
            )
        )
    for column, value in ((PC.abcd, filters.product_class), (CC.abcd, filters.customer_class)):
        condition = _class_condition(column, value)
        if condition is not None:
            conditions.append(condition)
    if filters.pending is True:
        conditions.append(_PENDING)
    elif filters.pending is False:
        conditions.append(L.qty_pending <= 0)
    return conditions


def _totals(row: Any) -> Totals:
    return Totals(
        orders=int(row.orders or 0),
        orders_pending=int(row.orders_pending or 0),
        lines=int(row.lines or 0),
        lines_pending=int(row.lines_pending or 0),
        qty=Decimal(row.qty or 0),
        qty_pending=Decimal(row.qty_pending or 0),
        value=Decimal(row.value or 0),
        value_pending=Decimal(row.value_pending or 0),
    )


def _reason(model: ServiceLevelReasonModel | None) -> Reason | None:
    if model is None:
        return None
    return Reason(
        id=model.id,
        code=model.code,
        name=model.name,
        responsible=model.responsible,
        is_active=model.is_active,
        sort_order=model.sort_order,
    )


def _line(model: ServiceLevelLineModel) -> OrderLine:
    return OrderLine(
        line_key=model.line_key,
        updated_at=model.erp_updated_at,
        **{name: getattr(model, name) for name in _LINE_FIELDS if name != "erp_updated_at"},
    )


def _values(line: OrderLine, now: datetime) -> dict[str, Any]:
    values = {name: getattr(line, name) for name in _LINE_FIELDS if name != "erp_updated_at"}
    values.update(
        line_key=line.line_key, erp_updated_at=line.updated_at, voided=False, synced_at=now
    )
    return values


def _run(model: ServiceLevelClassRunModel) -> ClassRun:
    return ClassRun(
        id=model.id,
        period_from=model.period_from,
        period_to=model.period_to,
        computed_at=model.computed_at,
        computed_by=model.computed_by,
        counts=dict(model.counts or {}),
    )


class SqlAlchemyServiceLevelRepository:
    """The copied order lines, their reasons, the reasons' catalog and the ABCD classes."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): The platform database.
        """

        self.database = database

    # --- reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        statement = select(R).order_by(R.sort_order, R.name)
        if not include_inactive:
            statement = statement.where(R.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_reason(model) for model in await session.scalars(statement)]  # type: ignore[misc]

    async def get_reason(self, reason_id: UUID) -> Reason | None:
        async with self.database.session_factory() as session:
            return _reason(await session.get(R, reason_id))

    async def create_reason(self, code: str, name: str, responsible: str) -> Reason:
        reason_id = uuid4()
        async with self.database.session_factory() as session, session.begin():
            last = await session.scalar(select(func.coalesce(func.max(R.sort_order), 0)))
            session.add(
                R(
                    id=reason_id,
                    code=code,
                    name=name,
                    responsible=responsible,
                    is_active=True,
                    sort_order=int(last or 0) + 10,
                )
            )
        return await self.get_reason(reason_id)  # type: ignore[return-value]

    async def update_reason(
        self,
        reason_id: UUID,
        *,
        name: str | None,
        responsible: str | None,
        is_active: bool | None,
    ) -> Reason:
        """Change the given fields of a reason (a new name changes its code too).

        Raises:
            NotFoundError: No such reason.
        """

        async with self.database.session_factory() as session, session.begin():
            model = await session.get(R, reason_id)
            if model is None:
                raise NotFoundError("Motivo no encontrado")
            if name is not None:
                model.name = name
                model.code = "-".join(
                    "".join(c.lower() if c.isalnum() else " " for c in name).split()
                )[:80]
            if responsible is not None:
                model.responsible = responsible
            if is_active is not None:
                model.is_active = is_active
            model.updated_at = func.now()
        return await self.get_reason(reason_id)  # type: ignore[return-value]

    async def responsibles(self) -> list[str]:
        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(distinct(R.responsible)).where(R.is_active.is_(True)).order_by(R.responsible)
            )
            return list(rows)

    # --- the copy of the warehouse ---

    async def has_lines(self) -> bool:
        async with self.database.session_factory() as session:
            return bool(await session.scalar(select(exists().where(L.line_key.is_not(None)))))

    async def store_lines(
        self, since: date, until: date, lines: Sequence[OrderLine], now: datetime
    ) -> StoreResult:
        """Upsert the lines read for the days, in one transaction.

        The days' lines not read again go away, or stay voided when they carry a reason. An
        empty read touches nothing (a warehouse being rebuilt).
        """

        if not lines:
            return StoreResult(0, 0, 0)
        rows = [_values(line, now) for line in {line.line_key: line for line in lines}.values()]
        updated = [*_LINE_FIELDS, "voided", "synced_at"]
        in_days = and_(L.order_date >= since, L.order_date <= until, L.synced_at < now)
        with_reason = select(LR.line_key)
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(rows), _CHUNK):
                statement = pg_insert(L).values(rows[start : start + _CHUNK])
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[L.line_key],
                        set_={name: statement.excluded[name] for name in updated},
                    )
                )
            removed = await session.execute(
                delete(L).where(in_days, L.line_key.not_in(with_reason))
            )
            voided = await session.execute(
                update(L)
                .where(in_days, L.voided.is_(False), L.line_key.in_(with_reason))
                .values(voided=True)
            )
        return StoreResult(len(rows), removed.rowcount or 0, voided.rowcount or 0)

    async def data_as_of(self) -> datetime | None:
        async with self.database.session_factory() as session:
            return await session.scalar(select(func.max(L.synced_at)))

    # --- reading ---

    async def page(
        self, filters: LineFilters, sort: str, descending: bool, page: int, size: int
    ) -> tuple[list[LineRow], int]:
        conditions = _conditions(filters)
        order = [
            column.desc().nulls_last() if descending else column.asc().nulls_last()
            for column in _LINE_SORTS[sort]
        ]
        statement = (
            _join(
                select(
                    L,
                    R,
                    LR.note,
                    LR.assigned_by,
                    LR.assigned_at,
                    PC.abcd.label("product_class"),
                    CC.abcd.label("customer_class"),
                )
            )
            .where(*conditions)
            .order_by(*order, L.line_key)
            .limit(size)
            .offset((page - 1) * size)
        )
        count = _join(select(func.count()).select_from(L)).where(*conditions)
        async with self.database.session_factory() as session:
            total = int(await session.scalar(count) or 0)
            rows = (await session.execute(statement)).all()
        return [self._row(row) for row in rows], total

    @staticmethod
    def _row(row: Any) -> LineRow:
        return LineRow(
            line=_line(row[0]),
            reason=_reason(row[1]),
            note=row.note,
            reason_by=row.assigned_by,
            reason_at=row.assigned_at,
            product_class=row.product_class,
            customer_class=row.customer_class,
        )

    async def totals(self, filters: LineFilters) -> Totals:
        statement = _join(select(*_AGGREGATES).select_from(L)).where(*_conditions(filters))
        async with self.database.session_factory() as session:
            return _totals((await session.execute(statement)).one())

    async def groups(
        self,
        filters: LineFilters,
        dimension: str,
        sort: str,
        descending: bool,
        limit: int | None,
        offset: int,
    ) -> tuple[list[Group], int]:
        key, name, detail_key, detail_name = _DIMENSIONS[dimension]
        conditions = _conditions(filters)
        if dimension in PENDING_DIMENSIONS:
            conditions.append(_PENDING)
        keys = [key] if detail_key is None else [key, detail_key]
        columns = [key.label("key"), name.label("name")]
        if detail_key is not None:
            columns += [detail_key.label("detail_key"), detail_name.label("detail_name")]
        value = func.coalesce(func.sum(L.value_ordered), 0)
        pending = func.coalesce(func.sum(L.value_pending), 0)
        measure = {
            "value": value,
            "value_pending": pending,
            "qty_pending": func.coalesce(func.sum(L.qty_pending), 0),
            "ns_value": 1 - pending / func.nullif(value, 0),
            "name": name,
            "key": key,
        }[sort]
        ordered = measure.desc().nulls_last() if descending else measure.asc().nulls_last()
        statement = (
            _join(select(*columns, *_AGGREGATES).select_from(L))
            .where(*conditions)
            .group_by(*keys)
            .order_by(ordered, *[column.asc().nulls_last() for column in keys])
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        counted = _join(select(*keys).select_from(L)).where(*conditions).group_by(*keys).subquery()
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
            total = int(await session.scalar(select(func.count()).select_from(counted)) or 0)
        groups = [
            Group(
                key=None if row.key is None else str(row.key),
                name=row.name,
                totals=_totals(row),
                detail_key=getattr(row, "detail_key", None),
                detail_name=getattr(row, "detail_name", None),
            )
            for row in rows
        ]
        return groups, total

    async def daily(self, filters: LineFilters) -> list[tuple[date, Totals]]:
        statement = (
            _join(select(L.order_date, *_AGGREGATES).select_from(L))
            .where(*_conditions(filters))
            .group_by(L.order_date)
            .order_by(L.order_date)
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [(row.order_date, _totals(row)) for row in rows]

    async def choices(self, since: date, until: date) -> FilterChoices:
        period = (L.voided.is_(False), L.order_date >= since, L.order_date <= until)

        async def pairs(session, code, name) -> dict[str, str]:
            rows = await session.execute(
                select(code, func.max(name))
                .where(*period, code.is_not(None))
                .group_by(code)
                .order_by(code)
            )
            return {str(row[0]): row[1] or str(row[0]) for row in rows}

        async with self.database.session_factory() as session:
            return FilterChoices(
                cos=await pairs(session, L.co, L.co_name),
                warehouses=await pairs(session, L.warehouse_code, L.warehouse_name),
                suppliers=await pairs(session, L.supplier_code, L.supplier_name),
                sellers=await pairs(session, L.seller_key, L.seller_name),
            )

    # --- reasons of the lines ---

    async def lines_by_key(self, keys: Sequence[str]) -> dict[str, LineRow]:
        statement = _join(
            select(
                L,
                R,
                LR.note,
                LR.assigned_by,
                LR.assigned_at,
                PC.abcd.label("product_class"),
                CC.abcd.label("customer_class"),
            )
        ).where(L.line_key.in_(list(keys)), L.voided.is_(False))
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return {row[0].line_key: self._row(row) for row in rows}

    async def set_reasons(
        self,
        keys: Sequence[str],
        reason_id: UUID | None,
        note: str | None,
        actor: str,
        now: datetime,
    ) -> int:
        async with self.database.session_factory() as session, session.begin():
            if reason_id is None:
                result = await session.execute(delete(LR).where(LR.line_key.in_(list(keys))))
                return result.rowcount or 0
            statement = pg_insert(LR).values(
                [
                    {
                        "line_key": key,
                        "reason_id": reason_id,
                        "note": note,
                        "assigned_by": actor,
                        "assigned_at": now,
                    }
                    for key in keys
                ]
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[LR.line_key],
                    set_={
                        name: statement.excluded[name]
                        for name in ("reason_id", "note", "assigned_by", "assigned_at")
                    },
                )
            )
        return len(keys)

    # --- ABCD ---

    async def class_values(self, kind: str, since: date, until: date) -> list[ClassValue]:
        period = (L.voided.is_(False), L.order_date >= since, L.order_date <= until)
        value = func.sum(L.value_ordered)
        if kind == PRODUCT:
            co = func.coalesce(L.co, "")
            statement = select(co, L.sku, value).where(*period).group_by(co, L.sku)
        else:
            statement = (
                select(L.customer_nit, value)
                .where(*period, L.customer_nit.is_not(None))
                .group_by(L.customer_nit)
            )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        if kind == PRODUCT:
            return [ClassValue(row[0], row[1], Decimal(row[2] or 0)) for row in rows]
        return [ClassValue("", row[0], Decimal(row[1] or 0)) for row in rows]

    async def replace_classes(
        self, classes: Mapping[str, Sequence[Classified]], run: ClassRun
    ) -> None:
        rows = [
            {
                "kind": kind,
                "co": item.co,
                "key": item.key,
                "value": item.value,
                "share": round(item.share, 12),
                "cumulative": round(item.cumulative, 12),
                "abcd": item.klass,
            }
            for kind, items in classes.items()
            for item in items
        ]
        async with self.database.session_factory() as session, session.begin():
            await session.execute(delete(K))
            for start in range(0, len(rows), _CHUNK):
                await session.execute(insert(K), rows[start : start + _CHUNK])
            session.add(
                RUN(
                    id=run.id,
                    period_from=run.period_from,
                    period_to=run.period_to,
                    computed_at=run.computed_at,
                    computed_by=run.computed_by,
                    counts=run.counts,
                )
            )

    async def latest_class_run(self) -> ClassRun | None:
        async with self.database.session_factory() as session:
            model = await session.scalar(select(RUN).order_by(RUN.computed_at.desc()).limit(1))
        return None if model is None else _run(model)

    async def stored_class_values(self, kind: str, co: str | None) -> list[Decimal]:
        statement = select(K.value).where(K.kind == kind)
        if co is not None:
            statement = statement.where(K.co == co)
        async with self.database.session_factory() as session:
            return [Decimal(value) for value in await session.scalars(statement)]


__all__ = ["SqlAlchemyServiceLevelRepository"]
