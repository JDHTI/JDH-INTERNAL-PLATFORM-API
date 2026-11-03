"""The quoter's reads of the platform copy of the commercial masters."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import Integer, cast, func, or_, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    ProductModel,
)
from app.modules.quotations.domain.entities import CustomerOption, CustomerPage, ProductMaster

_CHUNK = 1000
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
                            *(getattr(ProductModel, name) for name in MARGIN_FIELDS),
                        ).where(ProductModel.sku.in_(unique[start : start + _CHUNK]))
                    )
                ).all()
                for sku, description, unit, vat, pack, supplier, liquor, *margins in rows:
                    result[sku] = ProductMaster(
                        sku=sku,
                        description=description,
                        unit=unit,
                        vat_pct=vat,
                        pack_factor=pack,
                        supplier_code=supplier,
                        margins=dict(zip(MARGIN_FIELDS, margins, strict=True)),
                        liquor_tax=liquor,
                    )
        return result

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
                )
                for row in rows
            ),
            int(total),
            page,
            size,
        )


__all__ = ["SqlAlchemyQuotationMasterSource"]
