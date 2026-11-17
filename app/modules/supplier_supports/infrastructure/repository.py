"""PostgreSQL repository for supplier supports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    and_,
    func,
    literal,
    not_,
    or_,
    select,
    true,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS, branches_margin_field
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    ProductModel,
)
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    Issue,
    LoadPlan,
    LoadResult,
    Support,
    SupportCost,
    SupportData,
    SupportRow,
    Tier,
    Trace,
)
from app.modules.supplier_supports.domain.filters import (
    FIELDS,
    Condition,
    Group,
    SearchSpec,
    SortKey,
)
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSupportAttachmentModel,
    SupplierSupportCostModel,
    SupplierSupportLoadModel,
    SupplierSupportModel,
    SupplierSupportTierModel,
)
from app.modules.supplier_supports.infrastructure.publication import queue_load, queue_supports

S = SupplierSupportModel
A = SupplierSupportAttachmentModel
_SCALAR_FIELDS = tuple(
    item.name
    for item in dataclass_fields(SupportData)
    if item.name not in {"issues", "tiers", "regionals", "business_units", "channels"}
)
_ARRAY_FIELDS = ("regionals", "business_units", "channels")

_SUPPORT_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    name: getattr(S, name) for name in FIELDS if "." not in name
}
_JOINED_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "supplier.code": CommercialSupplierModel.code,
    "supplier.name": CommercialSupplierModel.name,
    "product.description": ProductModel.description,
    "product.brand": ProductModel.brand,
    "product.category": ProductModel.category,
    "product.subcategory": ProductModel.subcategory,
    "product.line": ProductModel.product_line,
    "product.manufacturer": ProductModel.manufacturer,
    "product.is_active": ProductModel.is_active,
}
_CUSTOMER_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "customer.channel": CustomerModel.channel,
    "customer.business_unit": CustomerModel.business_unit,
    "customer.business_group": CustomerModel.business_group,
    "customer.city": CustomerModel.city,
    "customer.salesperson": CustomerModel.salesperson_name,
    "customer.is_active": CustomerModel.is_active,
}
_SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    **{name: column for name, column in _SUPPORT_COLUMNS.items() if FIELDS[name].sortable},
    **{name: column for name, column in _JOINED_COLUMNS.items() if FIELDS[name].sortable},
}


def _data(model: SupplierSupportModel, tiers: Sequence[SupplierSupportTierModel]) -> SupportData:
    values: dict[str, Any] = {name: getattr(model, name) for name in _SCALAR_FIELDS}
    for name in _ARRAY_FIELDS:
        values[name] = tuple(getattr(model, name) or ())
    values["issues"] = tuple(
        Issue(item["code"], item.get("detail"), item.get("field")) for item in model.issues or ()
    )
    values["tiers"] = tuple(
        Tier(tier.threshold_type, tier.from_value, tier.to_value, tier.value)
        for tier in sorted(tiers, key=lambda value: value.position)
    )
    return SupportData(**values)


def _support(model: SupplierSupportModel, tiers: Sequence[SupplierSupportTierModel]) -> Support:
    return Support(
        id=model.id,
        data=_data(model, tiers),
        void_reason=model.void_reason,
        voided_at=model.voided_at,
        created_by=model.created_by,
        updated_by=model.updated_by,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        last_origin=model.last_origin,
        last_load_id=model.last_load_id,
        internal_request_id=model.internal_request_id,
    )


def _apply(model: SupplierSupportModel, data: SupportData) -> None:
    for name in _SCALAR_FIELDS:
        setattr(model, name, getattr(data, name))
    for name in _ARRAY_FIELDS:
        setattr(model, name, list(getattr(data, name)))
    model.issues = [issue.as_dict() for issue in data.issues]


def _new_model(
    support_id: UUID, data: SupportData, actor: str, now: datetime, trace: Trace
) -> SupplierSupportModel:
    model = SupplierSupportModel(
        id=support_id,
        created_by=actor,
        updated_by=actor,
        version=1,
        created_at=now,
        updated_at=now,
        last_origin=trace.origin,
        last_load_id=trace.load_id,
    )
    _apply(model, data)
    return model


def _stamp(model: SupplierSupportModel, actor: str, now: datetime, trace: Trace) -> None:
    """Next version of a support: who, when and what wrote it."""

    model.version += 1
    model.updated_by = actor
    model.updated_at = now
    model.last_origin = trace.origin
    model.last_load_id = trace.load_id


def _tier_models(support_id: UUID, tiers: Sequence[Tier]) -> list[SupplierSupportTierModel]:
    return [
        SupplierSupportTierModel(
            id=uuid4(),
            support_id=support_id,
            position=index,
            threshold_type=tier.threshold_type,
            from_value=tier.from_value,
            to_value=tier.to_value,
            value=tier.value,
        )
        for index, tier in enumerate(tiers)
    ]


async def _costs(session: Any, ids: Sequence[UUID]) -> dict[UUID, SupportCost]:
    """The DWH cost of these supports, as the daily job left it."""

    if not ids:
        return {}
    rows = await session.execute(
        select(SupplierSupportCostModel).where(SupplierSupportCostModel.support_id.in_(ids))
    )
    return {
        cost.support_id: SupportCost(
            support_id=cost.support_id,
            product_sku=cost.product_sku,
            plan_key=cost.plan_key,
            plan_label=cost.plan_label,
            unit_cost=cost.unit_cost,
            source=cost.source,
            quote_cost=cost.quote_cost,
            inventory_cost=cost.inventory_cost,
            snapshot_date=cost.snapshot_date,
            computed_at=cost.computed_at,
        )
        for cost in rows.scalars()
    }


def _in_force(table: Any, day: date) -> ColumnElement[bool]:
    return and_(
        or_(table.valid_from.is_(None), table.valid_from <= day),
        or_(table.valid_to.is_(None), table.valid_to >= day),
    )


def _same(left: Any, right: Any) -> ColumnElement[bool]:
    return func.coalesce(left, "") == func.coalesce(right, "")


def _conflict_clause(other: Any) -> ColumnElement[bool]:
    """``other`` is a live support over the same scope and period as ``S``."""

    return and_(
        other.id != S.id,
        other.status != "anulado",
        S.status != "anulado",
        # Plain equalities (NULL folded to an empty value) so PostgreSQL can hash-join the
        # self-join; IS NOT DISTINCT FROM would force a nested loop over every pair.
        _same(other.commercial_supplier_id.cast(Text), S.commercial_supplier_id.cast(Text)),
        other.kind == S.kind,
        _same(other.program, S.program),
        _same(other.customer_nit, S.customer_nit),
        _same(other.product_sku, S.product_sku),
        _same(other.supplier_sku, S.supplier_sku),
        _same(other.warehouse, S.warehouse),
        other.regionals == S.regionals,
        # Two supports with no dates at all cannot be told apart in time (successive cost
        # updates of the same line), and a support scoped to nothing is a supplier-wide
        # program: neither is a conflict.
        or_(
            S.valid_from.is_not(None),
            S.valid_to.is_not(None),
            other.valid_from.is_not(None),
            other.valid_to.is_not(None),
        ),
        or_(
            S.customer_nit.is_not(None),
            S.product_sku.is_not(None),
            S.supplier_sku.is_not(None),
            S.warehouse.is_not(None),
        ),
        or_(other.valid_from.is_(None), S.valid_to.is_(None), other.valid_from <= S.valid_to),
        or_(S.valid_from.is_(None), other.valid_to.is_(None), S.valid_from <= other.valid_to),
    )


def _lower(column: Any) -> Any:
    return func.lower(func.coalesce(column, ""))


def _leaf(column: Any, kind: str, condition: Condition) -> ColumnElement[bool]:
    op, value = condition.op, condition.value
    if op == "is_null":
        return column.is_(None)
    if op == "not_null":
        return column.is_not(None)
    if kind == "array":
        if op == "overlaps":
            return column.overlap(value)
        if op == "contains_all":
            return column.contains(value)
        size = func.coalesce(func.cardinality(column), 0)
        return size == 0 if op == "is_empty" else size > 0
    if kind == "issues":
        if op == "overlaps":
            return or_(*[column.contains([{"code": code}]) for code in value])
        size = func.jsonb_array_length(column)
        return size == 0 if op == "is_empty" else size > 0
    if kind == "text":
        if op == "eq":
            return _lower(column) == value.lower()
        if op == "neq":
            return _lower(column) != value.lower()
        if op == "in":
            return _lower(column).in_([item.lower() for item in value])
        if op == "nin":
            return not_(_lower(column).in_([item.lower() for item in value]))
        if op == "contains":
            return column.icontains(value, autoescape=True)
        return column.istartswith(value, autoescape=True)
    if kind == "bool":
        return column.is_(value)
    if op == "eq":
        return column == value
    if op == "neq":
        return column.is_distinct_from(value)
    if op == "in":
        return column.in_(value)
    if op == "nin":
        return not_(column.in_(value))
    if op == "gt":
        return column > value
    if op == "gte":
        return column >= value
    if op == "lt":
        return column < value
    if op == "lte":
        return column <= value
    return column.between(value[0], value[1])


def _compile(node: Condition | Group) -> ColumnElement[bool]:
    if isinstance(node, Group):
        parts = [_compile(item) for item in node.items]
        if not parts:
            return true()
        return and_(*parts) if node.match == "all" else or_(*parts)
    kind = FIELDS[node.field].type
    if node.field in _CUSTOMER_COLUMNS:
        inner = _leaf(_CUSTOMER_COLUMNS[node.field], kind, node)
        return select(literal(1)).where(CustomerModel.nit == S.customer_nit, inner).exists()
    if node.field == "issues.code":
        return _leaf(S.issues, kind, node)
    column = (
        _JOINED_COLUMNS[node.field]
        if node.field in _JOINED_COLUMNS
        else _SUPPORT_COLUMNS[node.field]
    )
    return _leaf(column, kind, node)


def _free_text(term: str) -> ColumnElement[bool]:
    return or_(
        *[
            column.icontains(term, autoescape=True)
            for column in (
                S.program,
                S.customer_name,
                S.customer_nit,
                S.product_sku,
                S.supplier_sku,
                S.brand,
                S.notes,
                S.conditions,
                CommercialSupplierModel.name,
                ProductModel.description,
            )
        ]
    )


def _order(keys: Sequence[SortKey]) -> list[Any]:
    order: list[Any] = []
    for key in keys:
        column = _SORT_COLUMNS[key.field]
        order.append(column.desc().nulls_last() if key.descending else column.asc().nulls_last())
    if not keys:
        order.append(S.valid_from.desc().nulls_last())
    order.append(S.id)
    return order


def _base() -> Select[Any]:
    return (
        select(
            S,
            CommercialSupplierModel.code,
            CommercialSupplierModel.name,
            ProductModel.description,
            ProductModel.brand,
            ProductModel.pack_factor,
            *(getattr(ProductModel, name) for name in MARGIN_FIELDS),
        )
        .select_from(S)
        .outerjoin(CommercialSupplierModel, CommercialSupplierModel.id == S.commercial_supplier_id)
        .outerjoin(ProductModel, ProductModel.id == S.product_id)
    )


async def customer_margin_fields(session: Any, nits: Sequence[str]) -> dict[str, str]:
    """The margin list of each customer NIT (majority of its active branches)."""

    unique = sorted({nit for nit in nits if nit})
    if not unique:
        return {}
    rows = (
        await session.execute(
            select(
                CustomerModel.nit,
                CustomerModel.channel,
                CustomerModel.billing_center_code,
                CustomerModel.sales_team_class,
                CustomerModel.is_active,
            ).where(CustomerModel.nit.in_(unique))
        )
    ).all()
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.nit, []).append(row)
    return {nit: branches_margin_field(branches) for nit, branches in grouped.items()}


def _rows(
    results: Sequence[Any],
    tiers: dict[UUID, list[Any]],
    conflicts: dict[UUID, list[UUID]],
    attachments: dict[UUID, int],
    costs: dict[UUID, SupportCost],
    margin_fields: dict[str, str],
) -> list[SupportRow]:
    rows = []
    for model, code, name, description, brand, pack, *margins in results:
        rows.append(
            SupportRow(
                support=_support(model, tiers.get(model.id, [])),
                supplier_code=code,
                supplier_name=name,
                product_description=description,
                product_brand=brand,
                conflicts=tuple(conflicts.get(model.id, ())),
                attachment_count=attachments.get(model.id, 0),
                dwh_cost=costs.get(model.id),
                product_margins=dict(zip(MARGIN_FIELDS, margins, strict=True)),
                product_pack_factor=pack,
                customer_margin_field=margin_fields.get(model.customer_nit or ""),
            )
        )
    return rows


class SqlAlchemySupportRepository:
    """Store supports with one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def _tiers(self, session: Any, ids: Sequence[UUID]) -> dict[UUID, list[Any]]:
        grouped: dict[UUID, list[SupplierSupportTierModel]] = {}
        if not ids:
            return grouped
        rows = await session.execute(
            select(SupplierSupportTierModel).where(SupplierSupportTierModel.support_id.in_(ids))
        )
        for tier in rows.scalars():
            grouped.setdefault(tier.support_id, []).append(tier)
        return grouped

    async def create(
        self,
        support_id: UUID,
        data: SupportData,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
    ) -> Support:
        """Insert a support and its tiers, and queue its first version for the DWH."""

        model = _new_model(support_id, data, actor, now, trace or Trace("manual"))
        if data.status == "anulado":
            model.voided_at = now
        tiers = _tier_models(support_id, data.tiers)
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.flush()
                session.add_all(tiers)
                await session.flush()
                await queue_supports(session, [model])
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el apoyo (referencia duplicada)") from exc
            return _support(model, tiers)

    async def create_many(
        self,
        items: Sequence[tuple[UUID, SupportData]],
        actor: str,
        now: datetime,
        trace: Trace | None = None,
    ) -> list[Support]:
        """Insert several supports and their tiers in one transaction: all or none."""

        origin = trace or Trace("lote")
        models = [
            (
                _new_model(support_id, data, actor, now, origin),
                _tier_models(support_id, data.tiers),
            )
            for support_id, data in items
        ]
        async with self.database.session_factory() as session:
            session.add_all([model for model, _ in models])
            try:
                await session.flush()
                session.add_all([tier for _, tiers in models for tier in tiers])
                await session.flush()
                await queue_supports(session, [model for model, _ in models])
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el lote (referencia duplicada)") from exc
            return [_support(model, tiers) for model, tiers in models]

    async def get(self, support_id: UUID) -> Support | None:
        """One support with its tiers."""

        async with self.database.session_factory() as session:
            model = await session.get(SupplierSupportModel, support_id)
            if model is None:
                return None
            tiers = (await self._tiers(session, [support_id])).get(support_id, [])
            return _support(model, tiers)

    async def get_row(self, support_id: UUID) -> SupportRow | None:
        """One support with joined names and its conflicts."""

        async with self.database.session_factory() as session:
            result = (await session.execute(_base().where(S.id == support_id))).first()
            if result is None:
                return None
            ids = [support_id]
            return _rows(
                [result],
                await self._tiers(session, ids),
                await self._conflict_ids(session, ids),
                await self._attachment_counts(session, ids),
                await _costs(session, ids),
                await customer_margin_fields(session, [result[0].customer_nit]),
            )[0]

    async def update(
        self,
        support_id: UUID,
        data: SupportData,
        expected_version: int,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
    ) -> Support:
        """Replace a support's data when its version still matches."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(
                    select(SupplierSupportModel).where(S.id == support_id).with_for_update()
                )
            ).scalar_one_or_none()
            if model is None:
                raise NotFoundError("Apoyo no encontrado")
            if model.version != expected_version:
                raise ConflictError("El apoyo cambió desde que lo abriste; recárgalo")
            tiers = await self._replace(session, model, data, actor, now, trace or Trace("edicion"))
            try:
                await queue_supports(session, [model])
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el apoyo (referencia duplicada)") from exc
            return _support(model, tiers)

    async def _replace(
        self,
        session: Any,
        model: SupplierSupportModel,
        data: SupportData,
        actor: str,
        now: datetime,
        trace: Trace,
    ) -> list[SupplierSupportTierModel]:
        """Write new data on a locked support: next version, new tiers (flushed)."""

        _apply(model, data)
        _stamp(model, actor, now, trace)
        old = await self._tiers(session, [model.id])
        for tier in old.get(model.id, []):
            await session.delete(tier)
        await session.flush()
        tiers = _tier_models(model.id, data.tiers)
        session.add_all(tiers)
        await session.flush()
        return tiers

    async def void(
        self,
        support_id: UUID,
        reason: str,
        expected_version: int,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
    ) -> Support:
        """Mark a support as voided when its version still matches."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(
                    select(SupplierSupportModel).where(S.id == support_id).with_for_update()
                )
            ).scalar_one_or_none()
            if model is None:
                raise NotFoundError("Apoyo no encontrado")
            if model.version != expected_version:
                raise ConflictError("El apoyo cambió desde que lo abriste; recárgalo")
            model.status = "anulado"
            model.void_reason = reason
            model.voided_at = now
            _stamp(model, actor, now, trace or Trace("anulacion"))
            await session.flush()
            await queue_supports(session, [model])
            await session.commit()
            tiers = (await self._tiers(session, [support_id])).get(support_id, [])
            return _support(model, tiers)

    async def live_for_suppliers(self, supplier_ids: Sequence[UUID]) -> list[Support]:
        """Supports of these suppliers that are not voided, with their tiers."""

        if not supplier_ids:
            return []
        async with self.database.session_factory() as session:
            models = list(
                (
                    await session.execute(
                        select(S).where(
                            S.commercial_supplier_id.in_(list(supplier_ids)),
                            S.status != "anulado",
                        )
                    )
                ).scalars()
            )
            tiers = await self._tiers(session, [model.id for model in models])
            return [_support(model, tiers.get(model.id, [])) for model in models]

    async def apply_load(self, plan: LoadPlan, actor: str, now: datetime) -> LoadResult:
        """Write a bulk load in one transaction; a support changed meanwhile aborts it."""

        trace = Trace("carga", plan.load_id)
        load = SupplierSupportLoadModel(
            id=plan.load_id,
            file_name=plan.file_name[:300],
            file_sha256=plan.file_sha256,
            supplier_code=plan.supplier_code,
            sheet=plan.sheet,
            actor=actor,
            created_at=now,
            rows_read=plan.rows_read,
            created=len(plan.creates),
            updated=len(plan.updates),
            unchanged=plan.unchanged,
            missing=plan.missing,
            expired=len(plan.expirations),
        )
        async with self.database.session_factory() as session:
            try:
                session.add(load)
                await session.flush()
                written: list[SupplierSupportModel] = []
                for support_id, data in plan.creates:
                    model = _new_model(support_id, data, actor, now, trace)
                    session.add(model)
                    written.append(model)
                await session.flush()
                session.add_all(
                    [
                        tier
                        for support_id, data in plan.creates
                        for tier in _tier_models(support_id, data.tiers)
                    ]
                )
                locked = await self._lock(
                    session,
                    [support_id for support_id, _, _ in plan.updates]
                    + [support_id for support_id, _, _ in plan.expirations],
                )
                for support_id, version, data in plan.updates:
                    model = self._expect(locked, support_id, version)
                    await self._replace(session, model, data, actor, now, trace)
                    written.append(model)
                for support_id, version, last_day in plan.expirations:
                    model = self._expect(locked, support_id, version)
                    model.valid_to = last_day
                    _stamp(model, actor, now, Trace("vencimiento", plan.load_id))
                    written.append(model)
                await session.flush()
                await queue_supports(session, written, not_before=plan.publish_not_before)
                await queue_load(session, load)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar la carga (referencia duplicada)") from exc
        return LoadResult(
            load_id=plan.load_id,
            created=tuple(support_id for support_id, _ in plan.creates),
            updated=tuple(support_id for support_id, _, _ in plan.updates),
            expired=tuple(support_id for support_id, _, _ in plan.expirations),
        )

    @staticmethod
    async def _lock(session: Any, ids: Sequence[UUID]) -> dict[UUID, SupplierSupportModel]:
        if not ids:
            return {}
        rows = await session.execute(
            select(S).where(S.id.in_(list(ids))).order_by(S.id).with_for_update()
        )
        return {model.id: model for model in rows.scalars()}

    @staticmethod
    def _expect(
        locked: dict[UUID, SupplierSupportModel], support_id: UUID, version: int
    ) -> SupplierSupportModel:
        model = locked.get(support_id)
        if model is None or model.version != version or model.status == "anulado":
            raise ConflictError(
                "Un apoyo de la carga cambió mientras se procesaba; vuelve a simular la carga"
            )
        return model

    @staticmethod
    def _conditions(spec: SearchSpec) -> list[ColumnElement[bool]]:
        """The WHERE conditions of a search, shared by the page and the id listing."""

        conditions: list[ColumnElement[bool]] = [_compile(spec.group)]
        if not spec.include_voided and spec.state != "anulado":
            conditions.append(S.status != "anulado")
        validity = (
            spec.state if spec.state in {"vigente", "vencido", "programado"} else spec.validity
        )
        if spec.state == "anulado":
            conditions.append(S.status == "anulado")
        elif spec.state == "inactivo":
            conditions.append(and_(S.status != "anulado", S.is_enabled.is_(False)))
        elif spec.state is not None:
            # vigente / vencido / programado: a switched-off support is "inactivo" instead.
            conditions.append(and_(S.status != "anulado", S.is_enabled.is_(True)))
        if spec.needs_review is not None:
            review = S.status == "pendiente_revision"
            conditions.append(review if spec.needs_review else not_(review))
        if validity == "vigente":
            conditions.append(_in_force(S, spec.active_on))
        elif validity == "vencido":
            conditions.append(and_(S.valid_to.is_not(None), S.valid_to < spec.active_on))
        elif validity == "programado":
            conditions.append(and_(S.valid_from.is_not(None), S.valid_from > spec.active_on))
        if spec.has_issues is not None:
            size = func.jsonb_array_length(S.issues)
            conditions.append(size > 0 if spec.has_issues else size == 0)
        if spec.has_conflicts is not None:
            other = aliased(SupplierSupportModel)
            clause = select(literal(1)).where(_conflict_clause(other)).exists()
            conditions.append(clause if spec.has_conflicts else not_(clause))
        if spec.effective_only:
            winner = aliased(SupplierSupportModel)
            keys = (
                winner.commercial_supplier_id,
                winner.customer_nit,
                winner.product_sku,
                winner.kind,
                winner.program,
            )
            effective = (
                select(winner.id)
                .where(
                    winner.status != "anulado",
                    winner.is_enabled.is_(True),
                    _in_force(winner, spec.active_on),
                )
                .distinct(*keys)
                .order_by(*keys, winner.valid_from.desc().nulls_last(), winner.created_at.desc())
            )
            conditions.append(S.id.in_(effective))
        if spec.q:
            conditions.append(_free_text(spec.q))
        if spec.has_attachments is not None:
            link = select(literal(1)).where(A.support_id == S.id).exists()
            conditions.append(link if spec.has_attachments else not_(link))
        return conditions

    async def search_ids(self, spec: SearchSpec, limit: int) -> list[UUID]:
        """Ids of the supports a search matches, up to ``limit``, in the search's order."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(S.id)
                .select_from(S)
                .outerjoin(
                    CommercialSupplierModel, CommercialSupplierModel.id == S.commercial_supplier_id
                )
                .outerjoin(ProductModel, ProductModel.id == S.product_id)
                .where(*self._conditions(spec))
                .order_by(*_order(spec.sort))
                .limit(limit)
            )
            return [support_id for (support_id,) in rows.all()]

    async def search(self, spec: SearchSpec) -> tuple[list[SupportRow], int]:
        """Filter, sort and page supports; also returns the total of matches."""

        conditions = self._conditions(spec)
        async with self.database.session_factory() as session:
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(S)
                    .outerjoin(
                        CommercialSupplierModel,
                        CommercialSupplierModel.id == S.commercial_supplier_id,
                    )
                    .outerjoin(ProductModel, ProductModel.id == S.product_id)
                    .where(*conditions)
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    _base()
                    .where(*conditions)
                    .order_by(*_order(spec.sort))
                    .offset((spec.page - 1) * spec.size)
                    .limit(spec.size)
                )
            ).all()
            ids = [row[0].id for row in rows]
            built = _rows(
                rows,
                await self._tiers(session, ids),
                await self._conflict_ids(session, ids),
                await self._attachment_counts(session, ids),
                await _costs(session, ids),
                await customer_margin_fields(session, [row[0].customer_nit for row in rows]),
            )
        return built, int(total)

    async def _attachment_counts(self, session: Any, ids: Sequence[UUID]) -> dict[UUID, int]:
        if not ids:
            return {}
        rows = await session.execute(
            select(A.support_id, func.count()).where(A.support_id.in_(ids)).group_by(A.support_id)
        )
        return {support_id: int(count) for support_id, count in rows.all()}

    async def _conflict_ids(self, session: Any, ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        found: dict[UUID, list[UUID]] = {}
        if not ids:
            return found
        other = aliased(SupplierSupportModel)
        rows = await session.execute(
            select(S.id, other.id)
            .select_from(S)
            .join(other, _conflict_clause(other))
            .where(S.id.in_(ids))
        )
        for support_id, other_id in rows.all():
            found.setdefault(support_id, []).append(other_id)
        return found

    async def get_by_source_key(self, source_key: str) -> Support | None:
        """The support loaded from a workbook row, if any."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(S).where(S.source_key == source_key))
            ).scalar_one_or_none()
            if model is None:
                return None
            tiers = (await self._tiers(session, [model.id])).get(model.id, [])
            return _support(model, tiers)

    async def existing_source_keys(self, keys: Sequence[str]) -> set[str]:
        """Which of the given import keys already exist."""

        if not keys:
            return set()
        async with self.database.session_factory() as session:
            rows = await session.execute(select(S.source_key).where(S.source_key.in_(keys)))
            return {key for (key,) in rows.all()}

    async def conflicts(self, support_id: UUID) -> list[Conflict]:
        """Supports overlapping this one over the same scope."""

        other = aliased(SupplierSupportModel)
        async with self.database.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(other)
                        .select_from(S)
                        .join(other, _conflict_clause(other))
                        .where(S.id == support_id)
                        .order_by(other.valid_from.desc().nulls_last(), other.id)
                    )
                )
                .scalars()
                .all()
            )
        return [
            Conflict(
                id=row.id,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                program=row.program,
                supplier_value=row.supplier_value,
                calc_method=row.calc_method,
                status=row.status,
            )
            for row in rows
        ]

    async def scope_values(self) -> dict[str, list[str]]:
        """Distinct regionals, business units, channels and programs already used."""

        async with self.database.session_factory() as session:
            result: dict[str, list[str]] = {}
            for name in _ARRAY_FIELDS:
                column = getattr(S, name)
                rows = await session.execute(
                    select(func.unnest(column).label("value")).distinct().order_by("value")
                )
                result[name] = [value for (value,) in rows.all()]
            programs = await session.execute(
                select(S.program).where(S.program.is_not(None)).distinct().order_by(S.program)
            )
            result["programs"] = [value for (value,) in programs.all()]
        return result


# Other repositories of the module that create supports in their own transaction.
new_support_model = _new_model

__all__ = ["SqlAlchemySupportRepository", "new_support_model"]
