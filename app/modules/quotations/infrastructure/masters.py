"""The quoter's reads of the platform copy of the commercial masters."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Integer, cast, func, or_, select, update

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS
from app.modules.commercial_masters.domain.price_increases import (
    PriceIncreaseValues,
    pending_cost,
)
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    PriceIncreaseModel,
    ProductModel,
)
from app.modules.quotations.domain.entities import (
    CustomerBranchOption,
    CustomerOption,
    CustomerPage,
    GroupInfo,
    GroupPage,
    ProductMaster,
)

_CHUNK = 1000
_INCREASE_FIELDS = ("sku", "system_updated_on", "new_cost", "new_cost_with_tax")
_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def _plain(column):
    return func.translate(func.lower(column), "áéíóúüñ", "aeiouun")


class SqlAlchemyQuotationMasterSource:
    """Products, supplier names and customers from the local master copy."""

    def __init__(self, database: Database) -> None:
        """Create the source.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def products(self, skus: Sequence[str]) -> dict[str, ProductMaster]:
        """Description, unit, VAT, pack, supplier and margins of each SKU."""

        result: dict[str, ProductMaster] = {}
        unique = list(dict.fromkeys(skus))
        async with self.database.session_factory() as session:
            for start in range(0, len(unique), _CHUNK):
                rows = (
                    await session.execute(
                        select(
                            ProductModel.sku,
                            ProductModel.description,
                            ProductModel.inventory_unit,
                            ProductModel.sales_vat_pct,
                            ProductModel.pack_factor,
                            ProductModel.commercial_supplier_code,
                            ProductModel.liquor_tax,
                            ProductModel.liquor_tax_coast,
                            *(getattr(ProductModel, name) for name in MARGIN_FIELDS),
                        ).where(ProductModel.sku.in_(unique[start : start + _CHUNK]))
                    )
                ).all()
                for sku, description, unit, vat, pack, supplier, liquor, coast, *margins in rows:
                    result[sku] = ProductMaster(
                        sku=sku,
                        description=description,
                        unit=unit,
                        vat_pct=vat,
                        pack_factor=pack,
                        supplier_code=supplier,
                        margins=dict(zip(MARGIN_FIELDS, margins, strict=True)),
                        liquor_tax=liquor,
                        liquor_tax_coast=coast,
                    )
        return result

    async def pending_increases(self, skus: Sequence[str]) -> dict[str, Decimal]:
        """The new cost of each SKU whose latest price increase SIESA does not have yet.

        The latest is the one that takes effect last (then the last notified); it is pending
        when it has no system update date. A loaded latest increase leaves the SKU out, even
        if an older one was never marked.
        """

        result: dict[str, Decimal] = {}
        unique = list(dict.fromkeys(skus))
        async with self.database.session_factory() as session:
            for start in range(0, len(unique), _CHUNK):
                rows = (
                    await session.execute(
                        select(*(getattr(PriceIncreaseModel, name) for name in _INCREASE_FIELDS))
                        .where(PriceIncreaseModel.sku.in_(unique[start : start + _CHUNK]))
                        .distinct(PriceIncreaseModel.sku)
                        .order_by(
                            PriceIncreaseModel.sku,
                            PriceIncreaseModel.increased_on.desc().nulls_last(),
                            PriceIncreaseModel.notified_on.desc().nulls_last(),
                            # Two increases with the same dates: the later row of the workbook.
                            PriceIncreaseModel.occurrence.desc(),
                            PriceIncreaseModel.updated_at.desc(),
                        )
                    )
                ).all()
                for row in rows:
                    values = PriceIncreaseValues(**dict(zip(_INCREASE_FIELDS, row, strict=True)))
                    cost = pending_cost(values)
                    if cost is not None:
                        result[values.sku] = cost
        return result

    async def increase_dates(self, skus: Sequence[str]) -> dict[str, date]:
        """The date of each SKU's latest price increase (when it takes effect, else when it
        was notified), to show on its line (2026-11-23). SKUs without one are left out."""

        result: dict[str, date] = {}
        unique = list(dict.fromkeys(skus))
        async with self.database.session_factory() as session:
            for start in range(0, len(unique), _CHUNK):
                rows = (
                    await session.execute(
                        select(
                            PriceIncreaseModel.sku,
                            PriceIncreaseModel.increased_on,
                            PriceIncreaseModel.notified_on,
                        )
                        .where(PriceIncreaseModel.sku.in_(unique[start : start + _CHUNK]))
                        .distinct(PriceIncreaseModel.sku)
                        .order_by(
                            PriceIncreaseModel.sku,
                            PriceIncreaseModel.increased_on.desc().nulls_last(),
                            PriceIncreaseModel.notified_on.desc().nulls_last(),
                            PriceIncreaseModel.updated_at.desc(),
                        )
                    )
                ).all()
                for sku, increased_on, notified_on in rows:
                    when = increased_on or notified_on
                    if when is not None:
                        result[sku] = when
        return result

    async def set_liquor_taxes(
        self, values: Mapping[str, tuple[Decimal, Decimal, str | None]], actor: str
    ) -> int:
        """Write the liquor tax (Nacional, Costa and tax group) of products of the margin
        master, as a bulk change loaded in SIESA left them; products not in the master are
        skipped. The group is kept when none is given.

        Returns:
            int: Products written.
        """

        if not values:
            return 0
        now = datetime.now(UTC)
        written = 0
        async with self.database.session_factory() as session, session.begin():
            for sku, (national, coast, group) in values.items():
                fields: dict[str, object] = {
                    "liquor_tax": national,
                    "liquor_tax_coast": coast,
                    "margin_updated_by": actor,
                    "margin_updated_at": now,
                }
                if group:
                    fields["liquor_tax_group"] = group
                result = await session.execute(
                    update(ProductModel).where(ProductModel.sku == sku).values(**fields)
                )
                written += result.rowcount or 0
        return written

    async def supplier_names(self, codes: Sequence[str]) -> dict[str, str]:
        """Name of each commercial supplier code."""

        if not codes:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(CommercialSupplierModel.code, CommercialSupplierModel.name).where(
                        CommercialSupplierModel.code.in_(list(dict.fromkeys(codes)))
                    )
                )
            ).all()
        return {code: name for code, name in rows}

    async def customers(
        self, seller_codes: Sequence[str] | None, q: str | None, page: int, size: int
    ) -> CustomerPage:
        """Customers merged by NIT, active ones first.

        Args:
            seller_codes (Sequence[str] | None): Only customers with a branch of these
                salespeople; ``None`` for all.
            q (str | None): NIT prefix (dots, spaces and a separated check digit allowed) or
                words of the name in any order, without accents.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            CustomerPage: The page.
        """

        conditions = []
        if seller_codes is not None:
            if not seller_codes:
                return CustomerPage((), 0, page, size)
            conditions.append(CustomerModel.salesperson_code.in_(list(seller_codes)))
        if q:
            text = q.strip()
            if re.fullmatch(r"(?=.*\d)[\d\s.,\-]+", text):
                base = re.sub(r"-\s*\d\s*$", "", text)
                digits = clean_nit(base) or clean_nit(text) or ""
                conditions.append(CustomerModel.nit.startswith(digits, autoescape=True))
            else:
                for token in text.lower().translate(_ACCENTS).split():
                    conditions.append(
                        or_(
                            _plain(CustomerModel.legal_name).contains(token, autoescape=True),
                            _plain(CustomerModel.establishment_name).contains(
                                token, autoescape=True
                            ),
                            # The business group: one word finds every customer of the group.
                            _plain(CustomerModel.business_group).contains(token, autoescape=True),
                        )
                    )
        active = func.sum(cast(CustomerModel.is_active, Integer))
        grouped = (
            select(
                CustomerModel.nit.label("nit"),
                func.max(CustomerModel.legal_name).label("name"),
                func.max(CustomerModel.channel_code).label("channel_code"),
                func.max(CustomerModel.channel).label("channel"),
                func.max(CustomerModel.salesperson_code).label("salesperson_code"),
                func.max(CustomerModel.salesperson_name).label("salesperson_name"),
                func.max(CustomerModel.business_group).label("business_group"),
                func.count().label("branch_count"),
                active.label("active_count"),
            )
            .where(*conditions)
            .group_by(CustomerModel.nit)
            .subquery()
        )
        async with self.database.session_factory() as session:
            total = (await session.execute(select(func.count()).select_from(grouped))).scalar_one()
            rows = (
                await session.execute(
                    select(grouped)
                    .order_by(
                        (grouped.c.active_count > 0).desc(),
                        grouped.c.name.asc().nulls_last(),
                        grouped.c.nit,
                    )
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
        return CustomerPage(
            tuple(
                CustomerOption(
                    nit=row.nit,
                    name=row.name,
                    channel_code=row.channel_code,
                    channel=row.channel,
                    salesperson_code=row.salesperson_code,
                    salesperson_name=row.salesperson_name,
                    branch_count=int(row.branch_count),
                    active_branch_count=int(row.active_count or 0),
                    business_group=row.business_group,
                )
                for row in rows
            ),
            int(total),
            page,
            size,
        )

    async def business_groups(
        self, seller_codes: Sequence[str] | None, q: str | None, page: int, size: int
    ) -> GroupPage:
        """Business groups with an active customer of these salespeople (``None``: all),
        by name; each with its active customers (NITs) and branches, the whole group's."""

        group = CustomerModel.business_group
        conditions = [group.is_not(None), func.trim(group) != "", CustomerModel.is_active.is_(True)]
        if seller_codes is not None:
            if not seller_codes:
                return GroupPage((), 0, page, size)
            mine = select(CustomerModel.business_group).where(
                CustomerModel.salesperson_code.in_(list(seller_codes))
            )
            conditions.append(group.in_(mine))
        for token in (q or "").lower().translate(_ACCENTS).split():
            conditions.append(_plain(group).contains(token, autoescape=True))
        grouped = (
            select(
                group.label("name"),
                func.count(func.distinct(CustomerModel.nit)).label("customers"),
                func.count().label("branches"),
            )
            .where(*conditions)
            .group_by(group)
            .subquery()
        )
        async with self.database.session_factory() as session:
            total = (await session.execute(select(func.count()).select_from(grouped))).scalar_one()
            rows = (
                await session.execute(
                    select(grouped).order_by(grouped.c.name).offset((page - 1) * size).limit(size)
                )
            ).all()
        return GroupPage(
            tuple(
                GroupInfo(name=row.name, customers=int(row.customers), branches=int(row.branches))
                for row in rows
            ),
            int(total),
            page,
            size,
        )

    async def customer_branches(self, nit: str) -> list[CustomerBranchOption]:
        """The branches of a customer, active first, by branch id (2026-11-24)."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        CustomerModel.branch_id,
                        CustomerModel.branch_name,
                        CustomerModel.establishment_name,
                        CustomerModel.city,
                        CustomerModel.channel,
                        CustomerModel.salesperson_name,
                        CustomerModel.salesperson_code,
                        CustomerModel.is_active,
                    )
                    .where(CustomerModel.nit == nit, CustomerModel.branch_id.is_not(None))
                    .order_by(CustomerModel.is_active.desc(), CustomerModel.branch_id)
                )
            ).all()
        return [
            CustomerBranchOption(
                branch_id=row.branch_id,
                branch_name=row.branch_name,
                establishment_name=row.establishment_name,
                city=row.city,
                channel=row.channel,
                salesperson_name=row.salesperson_name,
                is_active=bool(row.is_active),
                salesperson_code=row.salesperson_code,
            )
            for row in rows
        ]

    async def group_nits(self, group: str) -> list[str]:
        """The NITs of a business group with an active branch."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(CustomerModel.nit)
                .where(
                    CustomerModel.business_group == group,
                    CustomerModel.is_active.is_(True),
                )
                .distinct()
                .order_by(CustomerModel.nit)
            )
            return [nit for (nit,) in rows.all()]


__all__ = ["SqlAlchemyQuotationMasterSource"]
