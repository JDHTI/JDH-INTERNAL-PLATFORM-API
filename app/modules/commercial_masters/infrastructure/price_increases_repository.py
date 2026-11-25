"""PostgreSQL repository of the price increase history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Text,
    and_,
    any_,
    case,
    false,
    func,
    literal,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.price_increase_analysis import AnalysisRow
from app.modules.commercial_masters.domain.price_increases import (
    EARLIEST_DATE,
    VALUE_FIELDS,
    FilterOptions,
    MonthOption,
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreasePage,
    PriceIncreaseValues,
    Scope,
    SupplierOption,
)
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    PriceIncreaseModel,
    ProductModel,
)
from app.modules.commercial_masters.infrastructure.siesa_load_repository import open_rows

P = PriceIncreaseModel
_CHUNK = 1000
_FIELDS = ("sku", *VALUE_FIELDS)


def _values(row: Any) -> PriceIncreaseValues:
    return PriceIncreaseValues(**{name: getattr(row, name) for name in _FIELDS})


def _JOINED():  # noqa: N802 - a table expression, built per query
    return P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
        CommercialSupplierModel,
        CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
    )


_COLUMNS = (
    P,
    ProductModel.description,
    CommercialSupplierModel.name,
    ProductModel.category,
    ProductModel.id,
)


def _increase(model, description, supplier, category, product_id, open_load=None):
    return PriceIncrease(
        id=model.id,
        values=_values(model),
        updated_by=model.updated_by,
        updated_at=model.updated_at,
        description=description,
        supplier_name=supplier,
        category=category,
        is_known_product=product_id is not None,
        open_load=open_load,
    )


# The regional as ``regional_key`` reads it: trimmed, single spaces, upper case.
_REGIONAL = func.upper(
    func.regexp_replace(func.btrim(func.coalesce(P.regional, "")), r"\s+", " ", "g")
)


def _in_scope(scope: Scope):
    """The rows :meth:`Scope.holds`: its references, national or of its regionals."""

    if not scope.skus:
        return false()
    return and_(
        P.sku == any_(literal(sorted(scope.skus), ARRAY(Text))),
        or_(
            _REGIONAL.in_(("", "NACIONAL")),
            _REGIONAL.in_(sorted(scope.regionals)) if scope.regionals else false(),
        ),
    )


class SqlAlchemyPriceIncreaseRepository:
    """Rows keyed by ``row_key``; an upload inserts or updates them in chunks."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def existing(self, keys: Sequence[str]) -> dict[str, PriceIncreaseValues]:
        """The stored values of these keys."""

        found: dict[str, PriceIncreaseValues] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(keys), _CHUNK):
                chunk = list(keys[start : start + _CHUNK])
                rows = await session.execute(select(P).where(P.row_key.in_(chunk)))
                for (model,) in rows:
                    found[model.row_key] = _values(model)
        return found

    async def known_skus(self, skus: Sequence[str]) -> set[str]:
        """The references the product master has."""

        known: set[str] = set()
        async with self.database.session_factory() as session:
            for start in range(0, len(skus), _CHUNK):
                chunk = list(skus[start : start + _CHUNK])
                rows = await session.scalars(
                    select(ProductModel.sku).where(ProductModel.sku.in_(chunk))
                )
                known.update(rows)
        return known

    async def ids_by_key(self, keys: Sequence[str]) -> dict[str, UUID]:
        """The identifier of each stored row key."""

        found: dict[str, UUID] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(keys), _CHUNK):
                chunk = list(keys[start : start + _CHUNK])
                rows = await session.execute(select(P.row_key, P.id).where(P.row_key.in_(chunk)))
                found.update({key: row_id for key, row_id in rows})
        return found

    async def list_margins(self, skus: Sequence[str]) -> dict[str, Decimal | None]:
        """The «Lista 1» margin of the references the product master has."""

        margins: dict[str, Decimal | None] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(skus), _CHUNK):
                chunk = list(skus[start : start + _CHUNK])
                rows = await session.execute(
                    select(ProductModel.sku, ProductModel.margin_list).where(
                        ProductModel.sku.in_(chunk)
                    )
                )
                margins.update({sku: margin for sku, margin in rows})
        return margins

    async def upsert(self, rows: Sequence[PriceIncreaseValues], actor: str, now: datetime) -> None:
        """Insert new keys and overwrite the values of existing ones, in one transaction."""

        if not rows:
            return
        records = [
            {
                "id": uuid4(),
                "row_key": row.key,
                "updated_by": actor,
                "updated_at": now,
                **asdict(row),
            }
            for row in rows
        ]
        statement = insert(P)
        statement = statement.on_conflict_do_update(
            index_elements=[P.row_key],
            set_={
                name: statement.excluded[name]
                for name in (*VALUE_FIELDS, "sku", "updated_by", "updated_at")
            },
        )
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(records), _CHUNK):
                await session.execute(statement, records[start : start + _CHUNK])

    def _query(self, filters: PriceIncreaseFilters):
        conditions = []
        if filters.sku:
            conditions.append(P.sku == filters.sku.strip())
        if filters.status:
            conditions.append(P.status == filters.status)
        if filters.pending is True:
            conditions.append(P.system_updated_on.is_(None))
        elif filters.pending is False:
            conditions.append(P.system_updated_on.is_not(None))
        if filters.supplier:
            conditions.append(ProductModel.commercial_supplier_code == filters.supplier)
        if filters.scope is not None:
            conditions.append(_in_scope(filters.scope))
        if filters.q and filters.q.strip():
            term = filters.q.strip()
            conditions.append(
                or_(
                    P.sku.startswith(term, autoescape=True),
                    ProductModel.description.icontains(term, autoescape=True),
                )
            )
        joined = P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
            CommercialSupplierModel,
            CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
        )
        return joined, and_(*conditions) if conditions else None

    async def page(self, filters: PriceIncreaseFilters, page: int, size: int) -> PriceIncreasePage:
        """The rows of the filter, the latest increase first, with the counts by status."""

        joined, where = self._query(filters)
        opened = open_rows().subquery()
        listing = select(*_COLUMNS, opened.c.number).select_from(
            joined.outerjoin(opened, opened.c.increase_id == P.id)
        )
        counting = select(
            func.count(),
            *(func.count(case((P.status == status, 1))) for status in ("subio", "bajo", "estable")),
            func.count(case((P.system_updated_on.is_(None), 1))),
        ).select_from(joined)
        if where is not None:
            listing = listing.where(where)
            counting = counting.where(where)
        listing = (
            listing.order_by(
                P.increased_on.desc().nulls_last(),
                P.notified_on.desc().nulls_last(),
                P.occurrence.desc(),
                P.sku,
                P.id,
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        async with self.database.session_factory() as session:
            total, up, down, flat, pending = (await session.execute(counting)).one()
            rows = (await session.execute(listing)).all()
        items = tuple(_increase(*row) for row in rows)
        counts = {"subio": up, "bajo": down, "estable": flat, "pendientes": pending}
        return PriceIncreasePage(items, int(total), page, size, counts)

    async def ids_for(self, filters: PriceIncreaseFilters, limit: int) -> list[UUID]:
        """The identifiers of the filter's rows, the latest increase first, up to ``limit``."""

        joined, where = self._query(filters)
        statement = select(P.id).select_from(joined)
        if where is not None:
            statement = statement.where(where)
        statement = statement.order_by(P.increased_on.desc().nulls_last(), P.id).limit(limit)
        async with self.database.session_factory() as session:
            return list(await session.scalars(statement))

    async def by_ids(self, ids: Sequence[UUID]) -> list[PriceIncrease]:
        """These rows with their product's data (the missing ones are left out)."""

        opened = open_rows().subquery()
        found: list[PriceIncrease] = []
        async with self.database.session_factory() as session:
            for start in range(0, len(ids), _CHUNK):
                chunk = list(ids[start : start + _CHUNK])
                statement = (
                    select(*_COLUMNS, opened.c.number)
                    .select_from(_JOINED().outerjoin(opened, opened.c.increase_id == P.id))
                    .where(P.id.in_(chunk))
                )
                found.extend(_increase(*row) for row in (await session.execute(statement)).all())
        return found

    async def version(self) -> tuple[int, datetime | None]:
        """Rows and last change: when either moves, the analysis is worked out again."""

        async with self.database.session_factory() as session:
            count, last = (
                await session.execute(select(func.count(), func.max(P.updated_at)))
            ).one()
        return int(count), last

    async def analysis_rows(self) -> list[AnalysisRow]:
        """Every dated row with only what the analysis reads, and the product's description,
        supplier and category. The whole history: the rows before a period give the cost it
        started at."""

        statement = (
            select(
                P.sku,
                P.increased_on,
                P.notified_on,
                P.status,
                P.variation_pct,
                P.old_cost,
                P.old_cost_with_tax,
                P.new_cost,
                P.new_cost_with_tax,
                P.system_updated_on.is_(None),
                ProductModel.description,
                CommercialSupplierModel.name,
                ProductModel.category,
                ProductModel.commercial_supplier_code,
                P.regional,
                P.occurrence,
            )
            .select_from(
                P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
                    CommercialSupplierModel,
                    CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
                )
            )
            .where(P.increased_on.is_not(None))
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [AnalysisRow(*row) for row in rows]

    async def filter_options(self) -> FilterOptions:
        """The commercial suppliers with references in the history (by name) and the months
        with increases taking effect (the latest first, from the year 2000)."""

        references = func.count(func.distinct(P.sku))
        suppliers = (
            select(CommercialSupplierModel.code, CommercialSupplierModel.name, references)
            .select_from(
                P.__table__.join(ProductModel, ProductModel.sku == P.sku).join(
                    CommercialSupplierModel,
                    CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
                )
            )
            .group_by(CommercialSupplierModel.code, CommercialSupplierModel.name)
            .order_by(CommercialSupplierModel.name, CommercialSupplierModel.code)
        )
        # A literal format, not a bound one: GROUP BY must repeat the very same expression.
        month = func.to_char(P.increased_on, literal_column("'YYYY-MM'"))
        months = (
            select(month, func.count(), references)
            .where(P.increased_on >= EARLIEST_DATE)
            .group_by(month)
            .order_by(month.desc())
        )
        async with self.database.session_factory() as session:
            supplier_rows = (await session.execute(suppliers)).all()
            month_rows = (await session.execute(months)).all()
        return FilterOptions(
            suppliers=tuple(
                SupplierOption(code, name, int(count)) for code, name, count in supplier_rows
            ),
            months=tuple(
                MonthOption(value, int(changes), int(count)) for value, changes, count in month_rows
            ),
        )

    async def set_system_date(
        self, increase_id: UUID, day: date | None, actor: str, now: datetime
    ) -> None:
        """Store when SIESA got the increase (``None`` clears it).

        Raises:
            NotFoundError: The row does not exist.
        """

        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                update(P)
                .where(P.id == increase_id)
                .values(system_updated_on=day, updated_by=actor, updated_at=now)
            )
            if result.rowcount == 0:
                raise NotFoundError("Alza no encontrada")

    async def get(self, increase_id: UUID) -> PriceIncrease:
        """One row with its product's data.

        Raises:
            NotFoundError: The row does not exist.
        """

        found = await self.by_ids([increase_id])
        if not found:
            raise NotFoundError("Alza no encontrada")
        return found[0]


__all__ = ["SqlAlchemyPriceIncreaseRepository"]
