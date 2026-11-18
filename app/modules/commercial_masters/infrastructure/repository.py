"""PostgreSQL repository for the commercial masters."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, bindparam, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Base, Database
from app.modules.commercial_masters.domain.employee_prices import EmployeePriceProduct
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    CommercialSupplierNit,
    Customer,
    CustomerRef,
    LegalSupplier,
    Page,
    Product,
    ProductMargins,
    ProductRef,
    SupplierProductCode,
    TargetMargin,
    clean_nit,
)
from app.modules.commercial_masters.domain.margins import (
    EMPLOYEE_MARGIN_FIELD,
    MARGIN_FIELDS,
    branches_margin_field,
    margin_for,
)
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CommercialSupplierNitModel,
    CustomerModel,
    ProductModel,
    ProductSupplierModel,
    SupplierModel,
    SupplierProductCodeModel,
)
from app.modules.masters.infrastructure.models import MasterDefinitionModel

# Rows of an executemany upsert; SQLAlchemy splits it under asyncpg's parameter cap.
_UPSERT_CHUNK = 1000
IMPORT_ACTOR = "importacion"
# Columns whose change stamps who set the margins and when.
_STAMPED = frozenset({*MARGIN_FIELDS, EMPLOYEE_MARGIN_FIELD, "margin_notes"})
_SYNC_TABLES: dict[str, tuple[type[Base], str]] = {
    "commercial_suppliers": (CommercialSupplierModel, "update"),
    "suppliers": (SupplierModel, "update"),
    "customers": (CustomerModel, "update"),
    "products": (ProductModel, "update"),
    "product_suppliers": (ProductSupplierModel, "delete"),
}


def _like(query: str) -> str:
    return query


def _commercial_supplier(
    model: CommercialSupplierModel, nits: Sequence[CommercialSupplierNitModel] = ()
) -> CommercialSupplier:
    return CommercialSupplier(
        id=model.id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        product_count=model.product_count,
        active_product_count=model.active_product_count,
        synced_at=model.synced_at,
        nits=[
            CommercialSupplierNit(
                nit=nit.nit,
                product_links=nit.product_links,
                is_primary=nit.is_primary,
                confirmed=nit.confirmed,
            )
            for nit in sorted(nits, key=lambda value: (-value.is_primary, -value.product_links))
        ],
    )


def _legal_supplier(model: SupplierModel) -> LegalSupplier:
    return LegalSupplier(
        id=model.id,
        nit=model.nit,
        branch_id=model.branch_id,
        legal_name=model.legal_name,
        branch_name=model.branch_name,
        supplier_class=model.supplier_class,
        supplier_type=model.supplier_type,
        city=model.city,
        payment_condition=model.payment_condition,
        state=model.state,
        branch_state=model.branch_state,
        is_active=model.is_active,
    )


def _customer(model: CustomerModel) -> Customer:
    return Customer(
        id=model.id,
        nit=model.nit,
        branch_id=model.branch_id,
        legal_name=model.legal_name,
        branch_name=model.branch_name,
        establishment_name=model.establishment_name,
        channel=model.channel,
        business_unit=model.business_unit,
        business_group=model.business_group,
        billing_center=model.billing_center,
        logistics_zone=model.logistics_zone,
        department=model.department,
        city=model.city,
        salesperson_name=model.salesperson_name,
        state=model.state,
        branch_state=model.branch_state,
        is_active=model.is_active,
    )


def _product_ref(row: ProductModel) -> ProductRef:
    return ProductRef(
        id=row.id,
        sku=row.sku,
        description=row.description,
        brand=row.brand,
        commercial_supplier_code=row.commercial_supplier_code,
        is_active=row.is_active,
        pack_factor=row.pack_factor if row.pack_factor and row.pack_factor > 0 else None,
        avg_unit_cost=row.avg_unit_cost,
        supplier_sku=(row.supplier_sku or "").strip() or None,
    )


def _product(model: ProductModel) -> Product:
    return Product(
        id=model.id,
        sku=model.sku,
        supplier_sku=model.supplier_sku,
        description=model.description,
        state=model.state,
        is_active=model.is_active,
        commercial_supplier_code=model.commercial_supplier_code,
        manufacturer=model.manufacturer,
        brand=model.brand,
        category=model.category,
        subcategory=model.subcategory,
        product_line=model.product_line,
        pack_factor=model.pack_factor,
        inventory_unit=model.inventory_unit,
        main_barcode=model.main_barcode,
        product_subline=model.product_subline,
        product_group=model.product_group,
        margins=ProductMargins(
            list=model.margin_list,
            wholesale=model.margin_wholesale,
            mass_food=model.margin_mass_food,
            market=model.margin_market,
            salsamentarias=model.margin_salsamentarias,
            employee=model.margin_employee,
            notes=model.margin_notes,
            source=model.margin_source,
            updated_by=model.margin_updated_by,
            updated_at=model.margin_updated_at,
        ),
        liquor_tax=model.liquor_tax,
        liquor_tax_coast=model.liquor_tax_coast,
        liquor_tax_group=model.liquor_tax_group,
    )


def _page_bounds(page: int, size: int) -> tuple[int, int]:
    return (page - 1) * size, size


class SqlAlchemyCommercialMasterRepository:
    """Sync and read the commercial masters, one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # ------------------------------------------------------------------ sync

    async def _upsert(
        self,
        model: type[Base],
        rows: Sequence[Mapping[str, Any]],
        conflict: Sequence[str],
        synced_at: datetime | None,
        keep: Sequence[str] = (),
    ) -> int:
        if not rows:
            return 0
        columns = [column.name for column in model.__table__.columns]  # type: ignore[attr-defined]
        async with self.database.session_factory() as session:
            for start in range(0, len(rows), _UPSERT_CHUNK):
                chunk = rows[start : start + _UPSERT_CHUNK]
                values = []
                for row in chunk:
                    value = {**row, "id": uuid4()}
                    if synced_at is not None and "synced_at" in columns:
                        value["synced_at"] = synced_at
                    values.append(value)
                statement = insert(model)
                skip = {"id", "created_at", *conflict, *keep}
                updates = {
                    name: statement.excluded[name]
                    for name in values[0]
                    if name in columns and name not in skip
                }
                if "updated_at" in columns:
                    updates["updated_at"] = func.now()
                await session.execute(
                    statement.on_conflict_do_update(index_elements=list(conflict), set_=updates),
                    values,
                )
            await session.commit()
        return len(rows)

    async def upsert_suppliers(self, rows: Sequence[Mapping[str, Any]], synced_at: datetime) -> int:
        """Upsert legal suppliers by ``proveedor_key``."""

        return await self._upsert(SupplierModel, rows, ["proveedor_key"], synced_at)

    async def upsert_customers(self, rows: Sequence[Mapping[str, Any]], synced_at: datetime) -> int:
        """Upsert customers by ``cliente_key``."""

        return await self._upsert(CustomerModel, rows, ["cliente_key"], synced_at)

    async def upsert_products(self, rows: Sequence[Mapping[str, Any]], synced_at: datetime) -> int:
        """Upsert products by ``producto_key``."""

        return await self._upsert(ProductModel, rows, ["producto_key"], synced_at)

    async def upsert_product_suppliers(
        self, rows: Sequence[Mapping[str, Any]], synced_at: datetime
    ) -> int:
        """Upsert the purchase bridge by ``(producto_key, proveedor_key)``."""

        return await self._upsert(
            ProductSupplierModel, rows, ["producto_key", "proveedor_key"], synced_at
        )

    async def upsert_commercial_suppliers(
        self, rows: Sequence[Mapping[str, Any]], synced_at: datetime
    ) -> int:
        """Upsert commercial suppliers by code, keeping the hand-set list margin."""

        return await self._upsert(
            CommercialSupplierModel,
            rows,
            ["code"],
            synced_at,
        )

    async def replace_commercial_supplier_nits(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Refresh suggested NITs; confirmed pairs are left exactly as a person set them."""

        if not rows:
            return 0
        async with self.database.session_factory() as session:
            ids = {
                code: supplier_id
                for code, supplier_id in (
                    await session.execute(
                        select(CommercialSupplierModel.code, CommercialSupplierModel.id)
                    )
                ).all()
            }
            wanted = {(ids[row["code"]], row["nit"]): row for row in rows if row["code"] in ids}
            existing = (await session.execute(select(CommercialSupplierNitModel))).scalars().all()
            for model in existing:
                key = (model.commercial_supplier_id, model.nit)
                if key in wanted:
                    row = wanted.pop(key)
                    model.product_links = row["product_links"]
                    if not model.confirmed:
                        model.is_primary = row["is_primary"]
                elif not model.confirmed:
                    await session.delete(model)
            for (supplier_id, nit), row in wanted.items():
                session.add(
                    CommercialSupplierNitModel(
                        id=uuid4(),
                        commercial_supplier_id=supplier_id,
                        nit=nit,
                        product_links=row["product_links"],
                        is_primary=row["is_primary"],
                        confirmed=False,
                    )
                )
            await session.commit()
        return len(rows)

    async def update_unit_costs(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Set the current average unit cost of products by SKU."""

        if not rows:
            return 0
        table = ProductModel.__table__
        statement = (
            update(table)
            .where(table.c.sku == bindparam("b_sku"))
            .values(avg_unit_cost=bindparam("b_cost"), avg_unit_cost_date=bindparam("b_date"))
        )
        params = [
            {
                "b_sku": row["sku"],
                "b_cost": row["avg_unit_cost"],
                "b_date": row["avg_unit_cost_date"],
            }
            for row in rows
        ]
        async with self.database.session_factory() as session:
            for start in range(0, len(params), _UPSERT_CHUNK):
                await session.execute(statement, params[start : start + _UPSERT_CHUNK])
            await session.commit()
        return len(rows)

    async def deactivate_unseen(self, table: str, synced_at: datetime) -> int:
        """Deactivate (or delete, for the bridge) rows the last sync did not see."""

        model, mode = _SYNC_TABLES[table]
        stale = model.synced_at < synced_at  # type: ignore[attr-defined]
        async with self.database.session_factory() as session:
            if mode == "delete":
                result = await session.execute(delete(model).where(stale))
            else:
                result = await session.execute(
                    update(model)
                    .where(stale, model.is_active.is_(True))  # type: ignore[attr-defined]
                    .values(is_active=False)
                )
            await session.commit()
        return result.rowcount or 0

    async def touch_master_definition(self, slug: str, synced_at: datetime) -> None:
        """Stamp the matching generic master definition with the sync time."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(MasterDefinitionModel)
                .where(MasterDefinitionModel.slug == slug)
                .values(last_synced_at=synced_at)
            )
            await session.commit()

    # ----------------------------------------------------------------- reads

    async def _page(self, query: Select[Any], count: Select[Any], page: int, size: int) -> Any:
        offset, limit = _page_bounds(page, size)
        async with self.database.session_factory() as session:
            total = (await session.execute(count)).scalar_one()
            rows = (await session.execute(query.offset(offset).limit(limit))).scalars().all()
        return rows, int(total)

    async def list_commercial_suppliers(
        self, query: str | None, is_active: bool | None, page: int, size: int
    ) -> Page[CommercialSupplier]:
        """Page commercial suppliers by code/name and active flag."""

        conditions = []
        if query:
            conditions.append(
                or_(
                    CommercialSupplierModel.code == query,
                    CommercialSupplierModel.name.icontains(query, autoescape=True),
                )
            )
        if is_active is not None:
            conditions.append(CommercialSupplierModel.is_active.is_(is_active))
        select_rows = (
            select(CommercialSupplierModel)
            .where(*conditions)
            .order_by(
                CommercialSupplierModel.is_active.desc(),
                CommercialSupplierModel.name,
                CommercialSupplierModel.code,
            )
        )
        count = select(func.count()).select_from(CommercialSupplierModel).where(*conditions)
        rows, total = await self._page(select_rows, count, page, size)
        return Page([_commercial_supplier(row) for row in rows], total, page, size)

    async def get_commercial_supplier(self, code: str) -> CommercialSupplier | None:
        """One commercial supplier with its NITs."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(
                    select(CommercialSupplierModel).where(CommercialSupplierModel.code == code)
                )
            ).scalar_one_or_none()
            if model is None:
                return None
            nits = (
                (
                    await session.execute(
                        select(CommercialSupplierNitModel).where(
                            CommercialSupplierNitModel.commercial_supplier_id == model.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return _commercial_supplier(model, nits)

    async def update_commercial_supplier(
        self,
        code: str,
        fields: Mapping[str, Any],
        confirmed_nits: Sequence[str] | None,
        primary_nit: str | None,
    ) -> CommercialSupplier | None:
        """Set the list margin/notes and confirm NITs (optionally choosing the primary)."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(
                    select(CommercialSupplierModel).where(CommercialSupplierModel.code == code)
                )
            ).scalar_one_or_none()
            if model is None:
                return None
            for name, value in fields.items():
                setattr(model, name, value)
            if confirmed_nits is not None:
                wanted = set(confirmed_nits)
                existing = (
                    (
                        await session.execute(
                            select(CommercialSupplierNitModel).where(
                                CommercialSupplierNitModel.commercial_supplier_id == model.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                known = {nit.nit for nit in existing}
                for nit in existing:
                    nit.confirmed = nit.nit in wanted
                    if primary_nit is not None:
                        nit.is_primary = nit.nit == primary_nit
                for nit in sorted(wanted - known):
                    session.add(
                        CommercialSupplierNitModel(
                            id=uuid4(),
                            commercial_supplier_id=model.id,
                            nit=nit,
                            confirmed=True,
                            is_primary=primary_nit == nit,
                        )
                    )
            await session.commit()
        return await self.get_commercial_supplier(code)

    async def list_legal_suppliers(
        self,
        query: str | None,
        nit: str | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> Page[LegalSupplier]:
        """Page legal suppliers."""

        conditions = []
        if query:
            conditions.append(SupplierModel.legal_name.icontains(query, autoescape=True))
        if nit:
            conditions.append(SupplierModel.nit == nit)
        if is_active is not None:
            conditions.append(SupplierModel.is_active.is_(is_active))
        select_rows = (
            select(SupplierModel)
            .where(*conditions)
            .order_by(
                SupplierModel.is_active.desc(),
                SupplierModel.legal_name,
                SupplierModel.nit,
                SupplierModel.branch_id,
            )
        )
        count = select(func.count()).select_from(SupplierModel).where(*conditions)
        rows, total = await self._page(select_rows, count, page, size)
        return Page([_legal_supplier(row) for row in rows], total, page, size)

    async def list_customers(
        self,
        query: str | None,
        nit: str | None,
        channel: str | None,
        business_unit: str | None,
        business_group: str | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> Page[Customer]:
        """Page customers by text, NIT, channel, unit and group."""

        conditions = []
        if query:
            if re.fullmatch(r"(?=.*\d)[\d\s.,\-]+", query):
                # NITs in the warehouse have no check digit. Accept punctuation and
                # an explicitly separated check digit without guessing bare digits.
                base = re.sub(r"-\s*\d\s*$", "", query)
                conditions.append(
                    CustomerModel.nit.startswith(
                        clean_nit(base) or clean_nit(query), autoescape=True
                    )
                )
            else:
                # Names may omit a middle surname or arrive in another word order.
                # Translate accents without requiring a PostgreSQL extension.
                def plain(column):
                    return func.translate(func.lower(column), "áéíóúüñ", "aeiouun")

                for token in query.lower().translate(str.maketrans("áéíóúüñ", "aeiouun")).split():
                    conditions.append(
                        or_(
                            plain(CustomerModel.legal_name).contains(token, autoescape=True),
                            plain(CustomerModel.branch_name).contains(token, autoescape=True),
                            plain(CustomerModel.business_group).contains(token, autoescape=True),
                        )
                    )
        if nit:
            conditions.append(CustomerModel.nit == nit)
        if channel:
            conditions.append(CustomerModel.channel == channel)
        if business_unit:
            conditions.append(CustomerModel.business_unit == business_unit)
        if business_group:
            conditions.append(CustomerModel.business_group == business_group)
        if is_active is not None:
            conditions.append(CustomerModel.is_active.is_(is_active))
        select_rows = (
            select(CustomerModel)
            .where(*conditions)
            .order_by(
                CustomerModel.is_active.desc(),
                CustomerModel.legal_name,
                CustomerModel.nit,
                CustomerModel.branch_id,
            )
        )
        count = select(func.count()).select_from(CustomerModel).where(*conditions)
        rows, total = await self._page(select_rows, count, page, size)
        return Page([_customer(row) for row in rows], total, page, size)

    async def list_products(
        self,
        query: str | None,
        brand: str | None,
        category: str | None,
        commercial_supplier_code: str | None,
        is_active: bool | None,
        page: int,
        size: int,
        has_margin: bool | None = None,
    ) -> Page[Product]:
        """Page products by text/SKU, brand, category and commercial supplier."""

        conditions = []
        if query:
            conditions.append(
                or_(
                    ProductModel.sku == query,
                    ProductModel.description.icontains(query, autoescape=True),
                )
            )
        if brand:
            conditions.append(ProductModel.brand == brand)
        if category:
            conditions.append(ProductModel.category == category)
        if commercial_supplier_code:
            conditions.append(ProductModel.commercial_supplier_code == commercial_supplier_code)
        if is_active is not None:
            conditions.append(ProductModel.is_active.is_(is_active))
        if has_margin is not None:
            defined = or_(*(getattr(ProductModel, name).is_not(None) for name in MARGIN_FIELDS))
            conditions.append(defined if has_margin else ~defined)
        select_rows = (
            select(ProductModel)
            .where(*conditions)
            .order_by(ProductModel.is_active.desc(), ProductModel.sku, ProductModel.id)
        )
        count = select(func.count()).select_from(ProductModel).where(*conditions)
        rows, total = await self._page(select_rows, count, page, size)
        return Page([_product(row) for row in rows], total, page, size)

    # --------------------------------------------------------------- lookups

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]:
        """Products by exact SKU."""

        if not skus:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                (await session.execute(select(ProductModel).where(ProductModel.sku.in_(skus))))
                .scalars()
                .all()
            )
        return {row.sku: _product_ref(row) for row in rows}

    async def products_by_supplier_sku(
        self, supplier_id: UUID, supplier_skus: Sequence[str]
    ) -> dict[str, list[ProductRef]]:
        """JDH products of a supplier's own SKUs (upper-cased, trimmed).

        The supplier codes table (``supplier_product_codes``) first; else the product master's
        ``supplier_sku`` among the supplier's products. Several products for one SKU are all
        returned, so the caller can tell an ambiguous code.
        """

        wanted = sorted({sku.strip().upper() for sku in supplier_skus if sku and sku.strip()})
        if not wanted:
            return {}
        found: dict[str, dict[UUID, ProductModel]] = {}
        async with self.database.session_factory() as session:
            coded = await session.execute(
                select(func.upper(SupplierProductCodeModel.supplier_sku), ProductModel)
                .join(ProductModel, ProductModel.id == SupplierProductCodeModel.product_id)
                .where(
                    SupplierProductCodeModel.commercial_supplier_id == supplier_id,
                    func.upper(SupplierProductCodeModel.supplier_sku).in_(wanted),
                )
            )
            for sku, product in coded.all():
                found.setdefault(sku, {})[product.id] = product
            code = select(CommercialSupplierModel.code).where(
                CommercialSupplierModel.id == supplier_id
            )
            missing = [sku for sku in wanted if sku not in found]
            if missing:
                listed = await session.execute(
                    select(func.upper(func.trim(ProductModel.supplier_sku)), ProductModel).where(
                        ProductModel.commercial_supplier_code == code.scalar_subquery(),
                        func.upper(func.trim(ProductModel.supplier_sku)).in_(missing),
                    )
                )
                for sku, product in listed.all():
                    found.setdefault(sku, {})[product.id] = product
            # A code typed as a number in Excel loses its leading zeros (0133 arrives as 133):
            # what is still missing matches with the zeros left out on both sides.
            loose = {sku.lstrip("0"): sku for sku in wanted if sku not in found and sku.lstrip("0")}
            if loose:
                coded_key = func.ltrim(
                    func.upper(func.trim(SupplierProductCodeModel.supplier_sku)), "0"
                )
                listed_key = func.ltrim(func.upper(func.trim(ProductModel.supplier_sku)), "0")
                coded = await session.execute(
                    select(coded_key, ProductModel)
                    .join(ProductModel, ProductModel.id == SupplierProductCodeModel.product_id)
                    .where(
                        SupplierProductCodeModel.commercial_supplier_id == supplier_id,
                        coded_key.in_(list(loose)),
                    )
                )
                listed = await session.execute(
                    select(listed_key, ProductModel).where(
                        ProductModel.commercial_supplier_code == code.scalar_subquery(),
                        listed_key.in_(list(loose)),
                    )
                )
                for key, product in (*coded.all(), *listed.all()):
                    found.setdefault(loose[key], {})[product.id] = product
        return {
            sku: [_product_ref(product) for product in products.values()]
            for sku, products in found.items()
        }

    async def business_group_members(self, groups: Sequence[str]) -> dict[str, list[tuple]]:
        """``{group: [(nit, legal name), ...]}`` of the active customers of each group."""

        names = sorted({group.strip() for group in groups if group and group.strip()})
        if not names:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(
                    CustomerModel.business_group,
                    CustomerModel.nit,
                    func.max(CustomerModel.legal_name),
                )
                .where(
                    func.upper(CustomerModel.business_group).in_([n.upper() for n in names]),
                    CustomerModel.is_active.is_(True),
                )
                .group_by(CustomerModel.business_group, CustomerModel.nit)
                .order_by(CustomerModel.nit)
            )
        by_upper = {name.upper(): name for name in names}
        members: dict[str, list[tuple]] = {}
        for group, nit, name in rows.all():
            members.setdefault(by_upper[group.upper()], []).append((nit, name))
        return members

    async def business_groups(self, query: str, limit: int) -> list[tuple[str, int]]:
        """Business groups whose name contains the text, with their active NITs."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(CustomerModel.business_group, func.count(func.distinct(CustomerModel.nit)))
                .where(
                    CustomerModel.business_group.is_not(None),
                    CustomerModel.business_group.icontains(query.strip(), autoescape=True),
                    CustomerModel.is_active.is_(True),
                )
                .group_by(CustomerModel.business_group)
                .order_by(func.count(func.distinct(CustomerModel.nit)).desc())
                .limit(limit)
            )
        return [(group, int(count)) for group, count in rows.all()]

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        """Commercial supplier ids by code."""

        if not codes:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(CommercialSupplierModel.code, CommercialSupplierModel.id).where(
                    CommercialSupplierModel.code.in_(codes)
                )
            )
            return {code: supplier_id for code, supplier_id in rows.all()}

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]:
        """Commercial suppliers whose name contains the text, best match first."""

        async with self.database.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(CommercialSupplierModel)
                        .where(CommercialSupplierModel.name.icontains(query, autoescape=True))
                        .order_by(
                            CommercialSupplierModel.active_product_count.desc(),
                            CommercialSupplierModel.code,
                        )
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
        return [_commercial_supplier(row) for row in rows]

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        """``(code, name)`` by commercial supplier id."""

        if not ids:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(
                    CommercialSupplierModel.id,
                    CommercialSupplierModel.code,
                    CommercialSupplierModel.name,
                ).where(CommercialSupplierModel.id.in_(ids))
            )
            return {supplier_id: (code, name) for supplier_id, code, name in rows.all()}

    async def existing_customer_nits(self, nits: Sequence[str]) -> dict[str, str]:
        """``{nit: legal name}`` of NITs that exist among the customers."""

        if not nits:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(CustomerModel.nit, func.min(CustomerModel.legal_name))
                .where(CustomerModel.nit.in_(nits))
                .group_by(CustomerModel.nit)
            )
            return {nit: name or "" for nit, name in rows.all()}

    async def customers_by_nit(self, nits: Sequence[str]) -> dict[str, CustomerRef]:
        """Name, business group and branch ids of each NIT that exists among the customers."""

        if not nits:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(
                    CustomerModel.nit,
                    CustomerModel.legal_name,
                    CustomerModel.business_group,
                    CustomerModel.branch_id,
                ).where(CustomerModel.nit.in_(nits))
            )
            found: dict[str, dict[str, Any]] = {}
            for nit, name, group, branch in rows.all():
                item = found.setdefault(nit, {"names": set(), "groups": set(), "branches": set()})
                if name:
                    item["names"].add(name)
                if group:
                    item["groups"].add(group)
                if branch:
                    item["branches"].add(branch)
        return {
            nit: CustomerRef(
                nit=nit,
                name=min(item["names"], default=""),
                business_group=min(item["groups"], default=None),
                branches=tuple(sorted(item["branches"])),
            )
            for nit, item in found.items()
        }

    async def target_margins(self, nit: str | None, skus: Sequence[str]) -> dict[str, TargetMargin]:
        """The optimal margin of each product on the customer's margin list (the quoter's
        target): the majority of the customer's branches decides the list; without a known
        customer, Food service."""

        wanted = sorted({sku for sku in skus if sku})
        if not wanted:
            return {}
        async with self.database.session_factory() as session:
            field_name = "margin_list"
            if nit:
                branches = (
                    await session.execute(
                        select(
                            CustomerModel.channel,
                            CustomerModel.billing_center_code,
                            CustomerModel.sales_team_class,
                            CustomerModel.is_active,
                        ).where(CustomerModel.nit == nit)
                    )
                ).all()
                if branches:
                    field_name = branches_margin_field(branches)
            products = (
                (await session.execute(select(ProductModel).where(ProductModel.sku.in_(wanted))))
                .scalars()
                .all()
            )
        return {
            product.sku: TargetMargin(
                sku=product.sku,
                description=product.description,
                commercial_supplier_code=product.commercial_supplier_code,
                margin_field=field_name,
                margin_pct=margin_for(
                    {name: getattr(product, name) for name in MARGIN_FIELDS}, field_name
                ),
                unit=product.inventory_unit,
            )
            for product in products
        }

    async def get_product(self, sku: str) -> Product | None:
        """One product by SKU, with its margins."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(ProductModel).where(ProductModel.sku == sku))
            ).scalar_one_or_none()
        return None if model is None else _product(model)

    async def update_product(
        self, sku: str, changes: Mapping[str, Any], actor: str, now: datetime
    ) -> Product | None:
        """Set the given columns of a product; stamps who changed a margin."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(ProductModel).where(ProductModel.sku == sku))
            ).scalar_one_or_none()
            if model is None:
                return None
            for name, value in changes.items():
                setattr(model, name, value)
            if any(name in _STAMPED for name in changes):
                model.margin_source = "manual"
                model.margin_updated_by = actor
                model.margin_updated_at = now
            await session.commit()
            return _product(model)

    async def margins_by_sku(self, skus: Sequence[str]) -> dict[str, Product]:
        """Products by exact SKU, to compare their margins with a file."""

        result: dict[str, Product] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(skus), _UPSERT_CHUNK):
                chunk = list(skus[start : start + _UPSERT_CHUNK])
                models = (
                    (await session.execute(select(ProductModel).where(ProductModel.sku.in_(chunk))))
                    .scalars()
                    .all()
                )
                result.update({model.sku: _product(model) for model in models})
        return result

    async def bulk_update_products(
        self, rows: Sequence[Mapping[str, Any]], actor: str, now: datetime
    ) -> int:
        """Set the same columns on many products, each row carrying its ``sku``.

        Every row must carry the same keys. When any of them is a margin, the margin
        source and author are stamped (``importacion`` when ``actor`` is the importer).
        """

        if not rows:
            return 0
        columns = [name for name in rows[0] if name != "sku"]
        stamp = any(name in _STAMPED for name in columns)
        values: dict[str, Any] = {name: bindparam(f"v_{name}") for name in columns}
        if stamp:
            source = "importacion" if actor == IMPORT_ACTOR else "manual"
            values.update(margin_source=source, margin_updated_by=actor, margin_updated_at=now)
        # Core statement on the table: with the entity, a list of parameters would make the
        # ORM look for primary keys (its bulk-by-primary-key mode).
        table = ProductModel.__table__
        statement = update(table).where(table.c.sku == bindparam("b_sku")).values(**values)
        payload = [
            {"b_sku": row["sku"], **{f"v_{name}": row[name] for name in columns}} for row in rows
        ]
        async with self.database.session_factory() as session:
            for start in range(0, len(payload), _UPSERT_CHUNK):
                await session.execute(statement, payload[start : start + _UPSERT_CHUNK])
            await session.commit()
        return len(rows)

    async def products_with_margins(self) -> list[Product]:
        """Every product, active first, to export the margin template."""

        async with self.database.session_factory() as session:
            models = (
                (
                    await session.execute(
                        select(ProductModel).order_by(
                            ProductModel.is_active.desc(), ProductModel.sku
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_product(model) for model in models]

    async def employee_price_products(self) -> list[EmployeePriceProduct]:
        """Every active product with what the employee price list needs, by SKU."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        ProductModel.sku,
                        ProductModel.description,
                        ProductModel.brand,
                        ProductModel.inventory_unit,
                        ProductModel.sales_vat_pct,
                        ProductModel.margin_employee,
                        ProductModel.is_active,
                    )
                    .where(ProductModel.is_active.is_(True))
                    .order_by(ProductModel.sku)
                )
            ).all()
        return [
            EmployeePriceProduct(
                sku=row.sku,
                description=row.description,
                brand=row.brand,
                inventory_unit=row.inventory_unit,
                vat_pct=row.sales_vat_pct,
                margin=row.margin_employee,
                is_active=row.is_active,
            )
            for row in rows
        ]

    async def upsert_supplier_product_codes(
        self, rows: Sequence[SupplierProductCode]
    ) -> tuple[int, list[SupplierProductCode]]:
        """Upsert supplier SKUs; returns the count and the rows whose supplier or SKU is unknown."""

        if not rows:
            return 0, []
        async with self.database.session_factory() as session:
            suppliers = {
                code: supplier_id
                for code, supplier_id in (
                    await session.execute(
                        select(CommercialSupplierModel.code, CommercialSupplierModel.id)
                    )
                ).all()
            }
            products = {
                sku: product_id
                for sku, product_id in (
                    await session.execute(
                        select(ProductModel.sku, ProductModel.id).where(
                            ProductModel.sku.in_({row.product_sku for row in rows})
                        )
                    )
                ).all()
            }
            values: dict[tuple[UUID, str, UUID], dict[str, Any]] = {}
            unresolved: list[SupplierProductCode] = []
            for row in rows:
                supplier_id = suppliers.get(row.commercial_supplier_code)
                product_id = products.get(row.product_sku)
                if supplier_id is None or product_id is None:
                    unresolved.append(row)
                    continue
                values[(supplier_id, row.supplier_sku, product_id)] = {
                    "id": uuid4(),
                    "commercial_supplier_id": supplier_id,
                    "supplier_sku": row.supplier_sku,
                    "product_id": product_id,
                    "pack_factor": row.pack_factor,
                    "kg_per_unit": row.kg_per_unit,
                    "notes": row.notes,
                }
            if values:
                statement = insert(SupplierProductCodeModel)
                await session.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_supplier_product_codes_triple",
                        set_={
                            "pack_factor": statement.excluded.pack_factor,
                            "kg_per_unit": statement.excluded.kg_per_unit,
                            "notes": statement.excluded.notes,
                        },
                    ),
                    list(values.values()),
                )
            await session.commit()
        return len(values), unresolved


__all__ = ["SqlAlchemyCommercialMasterRepository"]
