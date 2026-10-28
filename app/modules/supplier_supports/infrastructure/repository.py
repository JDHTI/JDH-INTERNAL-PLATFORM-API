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
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    ProductModel,
)
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    Issue,
    Support,
    SupportData,
    SupportRow,
    Tier,
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
    SupplierSupportModel,
    SupplierSupportTierModel,
)

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
    )


def _apply(model: SupplierSupportModel, data: SupportData) -> None:
    for name in _SCALAR_FIELDS:
        setattr(model, name, getattr(data, name))
    for name in _ARRAY_FIELDS:
        setattr(model, name, list(getattr(data, name)))
    model.issues = [issue.as_dict() for issue in data.issues]


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
        )
        .select_from(S)
        .outerjoin(CommercialSupplierModel, CommercialSupplierModel.id == S.commercial_supplier_id)
        .outerjoin(ProductModel, ProductModel.id == S.product_id)
    )


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
        self, support_id: UUID, data: SupportData, actor: str, now: datetime
    ) -> Support:
        """Insert a support and its tiers."""

        model = SupplierSupportModel(
            id=support_id,
            created_by=actor,
            updated_by=actor,
            version=1,
            created_at=now,
            updated_at=now,
        )
        _apply(model, data)
        if data.status == "anulado":
            model.voided_at = now
        tiers = _tier_models(support_id, data.tiers)
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.flush()
                session.add_all(tiers)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el apoyo (referencia duplicada)") from exc
            return _support(model, tiers)

    async def create_many(
        self, items: Sequence[tuple[UUID, SupportData]], actor: str, now: datetime
    ) -> list[Support]:
        """Insert several supports and their tiers in one transaction: all or none."""

        models = []
        for support_id, data in items:
            model = SupplierSupportModel(
                id=support_id,
                created_by=actor,
                updated_by=actor,
                version=1,
                created_at=now,
                updated_at=now,
            )
            _apply(model, data)
            models.append((model, _tier_models(support_id, data.tiers)))
        async with self.database.session_factory() as session:
            session.add_all([model for model, _ in models])
            try:
                await session.flush()
                session.add_all([tier for _, tiers in models for tier in tiers])
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
            model, code, name, description, brand = result
            tiers = (await self._tiers(session, [support_id])).get(support_id, [])
            conflicts = await self._conflict_ids(session, [support_id])
            attachments = await self._attachment_counts(session, [support_id])
            return SupportRow(
                support=_support(model, tiers),
                supplier_code=code,
                supplier_name=name,
                product_description=description,
                product_brand=brand,
                conflicts=tuple(conflicts.get(support_id, ())),
                attachment_count=attachments.get(support_id, 0),
            )

    async def update(
        self, support_id: UUID, data: SupportData, expected_version: int, actor: str, now: datetime
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
            _apply(model, data)
            model.version += 1
            model.updated_by = actor
            model.updated_at = now
            old = await self._tiers(session, [support_id])
            for tier in old.get(support_id, []):
                await session.delete(tier)
            await session.flush()
            tiers = _tier_models(support_id, data.tiers)
            session.add_all(tiers)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el apoyo (referencia duplicada)") from exc
            return _support(model, tiers)

    async def void(
        self, support_id: UUID, reason: str, expected_version: int, actor: str, now: datetime
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
            model.version += 1
            model.updated_by = actor
            model.updated_at = now
            await session.commit()
            tiers = (await self._tiers(session, [support_id])).get(support_id, [])
            return _support(model, tiers)

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
            tiers = await self._tiers(session, ids)
            conflicts = await self._conflict_ids(session, ids)
            attachments = await self._attachment_counts(session, ids)
        return (
            [
                SupportRow(
                    support=_support(model, tiers.get(model.id, [])),
                    supplier_code=code,
                    supplier_name=name,
                    product_description=description,
                    product_brand=brand,
                    conflicts=tuple(conflicts.get(model.id, ())),
                    attachment_count=attachments.get(model.id, 0),
                )
                for model, code, name, description, brand in rows
            ],
            int(total),
        )

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


__all__ = ["SqlAlchemySupportRepository"]
