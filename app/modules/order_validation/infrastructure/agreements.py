"""Read-only access to the supports master and the commercial masters."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import and_, or_, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
)
from app.modules.order_validation.domain.entities import Agreement, CustomerScope
from app.modules.supplier_supports.infrastructure.models import SupplierSupportModel

S = SupplierSupportModel


class SqlAlchemyAgreementSource:
    """Candidate supports, supplier list margins and client scope from PostgreSQL."""

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

    async def supplier_margins(self, codes: Sequence[str]) -> dict[str, Decimal | None]:
        """List margin of each commercial supplier (``None`` when not set)."""

        if not codes:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        CommercialSupplierModel.code, CommercialSupplierModel.list_margin_pct
                    ).where(CommercialSupplierModel.code.in_(list(codes)))
                )
            ).all()
        return {code: margin for code, margin in rows}

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
