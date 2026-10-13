"""Read-only access to the supports master and the commercial masters."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, or_, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    ProductModel,
)
from app.modules.order_validation.domain.entities import Agreement, CustomerScope, ProductInfo
from app.modules.supplier_supports.infrastructure.models import SupplierSupportModel

S = SupplierSupportModel
# `source` of the supports imported from the workbook's `Condiciones Especiales` table.
SPECIAL_CONDITIONS_SOURCE = "Condiciones Especiales%"
_CHUNK = 5000


class SqlAlchemyAgreementSource:
    """Candidate supports, product margins and client scope from PostgreSQL."""

    def __init__(self, database: Database) -> None:
        """Create the source.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def agreements(
        self, supplier_codes: Sequence[str], skus: Sequence[str], nits: Sequence[str]
    ) -> list[Agreement]:
        """Supports that may cover the lines of a file, whatever their dates.

        Voided and switched-off supports are left out. Dates are not filtered here: a
        support out of force still tells the person why the line is pending. Supports with
        no supplier (margin exceptions whose supplier did not match) come in by SKU.
        """

        if not supplier_codes and not skus:
            return []
        customer = or_(S.customer_nit.is_(None), S.customer_nit.in_(list(nits)))
        product = or_(S.product_sku.is_(None), S.product_sku.in_(list(skus)))
        query = (
            select(S, CommercialSupplierModel.code)
            .outerjoin(
                CommercialSupplierModel, CommercialSupplierModel.id == S.commercial_supplier_id
            )
            .where(
                S.status != "anulado",
                S.is_enabled.is_(True),
                # The special conditions of the workbook stay documented, not applied: some
                # cannot be automated (docs/condiciones-especiales.md).
                or_(S.source.is_(None), ~S.source.ilike(SPECIAL_CONDITIONS_SOURCE)),
                customer,
                or_(
                    and_(CommercialSupplierModel.code.in_(list(supplier_codes)), product),
                    and_(S.commercial_supplier_id.is_(None), S.product_sku.in_(list(skus))),
                ),
            )
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        return [
            Agreement(
                id=model.id,
                supplier_code=code,
                kind=model.kind,
                calc_method=model.calc_method,
                status=model.status,
                program=model.program,
                customer_nit=model.customer_nit,
                product_sku=model.product_sku,
                regionals=tuple(model.regionals or ()),
                business_units=tuple(model.business_units or ()),
                channels=tuple(model.channels or ()),
                warehouse=model.warehouse,
                supplier_value=model.supplier_value,
                target_margin_pct=model.target_margin_pct,
                min_margin_pct=model.min_margin_pct,
                list_margin_pct=model.list_margin_pct,
                reference_cost=model.reference_cost,
                reference_list_price=model.reference_list_price,
                min_quantity=model.min_quantity,
                valid_from=model.valid_from,
                valid_to=model.valid_to,
            )
            for model, code in rows
        ]

    async def products(self, skus: Sequence[str]) -> dict[str, ProductInfo]:
        """Margin by channel and liquor tax of each product."""

        if not skus:
            return {}
        result: dict[str, ProductInfo] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(skus), _CHUNK):
                rows = (
                    await session.execute(
                        select(
                            ProductModel.sku,
                            ProductModel.liquor_tax,
                            *(getattr(ProductModel, name) for name in MARGIN_FIELDS),
                        ).where(ProductModel.sku.in_(list(skus[start : start + _CHUNK])))
                    )
                ).all()
                for sku, tax, *margins in rows:
                    result[sku] = ProductInfo(
                        sku=sku,
                        margins=dict(zip(MARGIN_FIELDS, margins, strict=True)),
                        liquor_tax=tax,
                    )
        return result

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerScope]:
        """Channel, business unit and regional of each client, merged across branches."""

        if not nits:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        CustomerModel.nit,
                        CustomerModel.channel,
                        CustomerModel.business_unit,
                        CustomerModel.billing_center,
                    ).where(CustomerModel.nit.in_(list(nits)))
                )
            ).all()
        merged: dict[str, tuple[set[str], set[str], set[str]]] = {}
        for nit, channel, unit, regional in rows:
            channels, units, regionals = merged.setdefault(nit, (set(), set(), set()))
            for target, value in ((channels, channel), (units, unit), (regionals, regional)):
                if value:
                    target.add(value)
        return {
            nit: CustomerScope(
                nit=nit,
                channels=frozenset(channels),
                business_units=frozenset(units),
                regionals=frozenset(regionals),
            )
            for nit, (channels, units, regionals) in merged.items()
        }


__all__ = ["SqlAlchemyAgreementSource"]
