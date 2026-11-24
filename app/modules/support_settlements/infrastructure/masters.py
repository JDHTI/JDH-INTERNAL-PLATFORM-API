"""The platform's own data a settlement needs: supports, products and customer branches."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import and_, exists, or_, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    CustomerModel,
    ProductModel,
)
from app.modules.supplier_supports.infrastructure.models import (
    InternalSupportRequestItemModel,
    SupplierSupportModel,
    SupplierSupportTierModel,
)
from app.modules.support_settlements.domain.entities import (
    CustomerBranch,
    Product,
    SettlementSupport,
)
from app.modules.support_settlements.domain.rules import SETTLED_KINDS

S = SupplierSupportModel
_CHUNK = 5000


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SqlAlchemySettlementMasters:
    """Supports, products and customers from PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def supports(self, start: date, following: date) -> list[SettlementSupport]:
        """The supports that may pay something in the month: the settled classes, funded by
        the supplier, not voided, switched on, in force some day of the month.

        Supports in review come too: they are listed, never claimed.
        """

        tiers = exists().where(SupplierSupportTierModel.support_id == S.id)
        replaced = (
            select(InternalSupportRequestItemModel.base_support_id)
            .where(InternalSupportRequestItemModel.support_id == S.id)
            .limit(1)
            .scalar_subquery()
        )
        query = (
            select(
                S,
                CommercialSupplierModel.code,
                CommercialSupplierModel.name,
                tiers.label("has_tiers"),
                replaced.label("replaces"),
            )
            .outerjoin(
                CommercialSupplierModel, CommercialSupplierModel.id == S.commercial_supplier_id
            )
            .where(
                S.kind.in_(SETTLED_KINDS),
                S.status != "anulado",
                S.is_enabled.is_(True),
                S.funding != "jdh",
                or_(S.valid_from.is_(None), S.valid_from < following),
                or_(S.valid_to.is_(None), S.valid_to >= start),
            )
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        return [
            SettlementSupport(
                id=model.id,
                version=model.version,
                supplier_code=_text(code),
                supplier_name=_text(name),
                kind=model.kind,
                calc_method=model.calc_method,
                status=model.status,
                customer_nit=_text(model.customer_nit),
                customer_branch_id=_text(model.customer_branch_id),
                customer_group=_text(model.customer_group),
                group_wide=bool(model.group_wide),
                product_sku=_text(model.product_sku),
                regionals=tuple(model.regionals or ()),
                business_units=tuple(model.business_units or ()),
                channels=tuple(model.channels or ()),
                warehouse=_text(model.warehouse),
                promo_warehouses=bool(model.promo_warehouses),
                supplier_value=model.supplier_value,
                value_unit=model.value_unit,
                pack_factor=model.pack_factor,
                target_margin_pct=model.target_margin_pct,
                supported_margin_pct=model.supported_margin_pct,
                supplier_min_margin_pct=model.supplier_min_margin_pct,
                min_quantity=model.min_quantity,
                valid_from=model.valid_from,
                valid_to=model.valid_to,
                funding=model.funding,
                settlement_method=model.settlement_method,
                has_tiers=bool(has_tiers),
                internal_request_id=model.internal_request_id,
                replaces_support_id=replaces,
            )
            for model, code, name, has_tiers, replaces in rows
        ]

    async def products(
        self, supplier_codes: Sequence[str], skus: Sequence[str]
    ) -> dict[str, Product]:
        """The products of these suppliers and these references."""

        conditions = []
        if supplier_codes:
            conditions.append(ProductModel.commercial_supplier_code.in_(list(supplier_codes)))
        if skus:
            conditions.append(ProductModel.sku.in_(list(skus)))
        if not conditions:
            return {}
        query = select(
            ProductModel.sku,
            ProductModel.producto_key,
            ProductModel.description,
            ProductModel.commercial_supplier_code,
            ProductModel.pack_factor,
            ProductModel.inventory_unit,
            *(getattr(ProductModel, name) for name in MARGIN_FIELDS),
        ).where(or_(*conditions))
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        return {
            row.sku: Product(
                sku=row.sku,
                producto_key=row.producto_key,
                description=row.description,
                supplier_code=_text(row.commercial_supplier_code),
                pack_factor=row.pack_factor,
                inventory_unit=row.inventory_unit,
                margins={name: getattr(row, name) for name in MARGIN_FIELDS},
            )
            for row in rows
        }

    async def customers(self, customer_keys: Sequence[str]) -> dict[str, CustomerBranch]:
        """Customer branches by ``cliente_key`` (the key of the sales lines)."""

        unique = list(dict.fromkeys(key for key in customer_keys if key))
        result: dict[str, CustomerBranch] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(unique), _CHUNK):
                rows = (
                    await session.execute(
                        select(CustomerModel).where(
                            and_(CustomerModel.cliente_key.in_(unique[start : start + _CHUNK]))
                        )
                    )
                ).scalars()
                for model in rows:
                    result[model.cliente_key] = CustomerBranch(
                        customer_key=model.cliente_key,
                        nit=model.nit,
                        branch_id=_text(model.branch_id),
                        name=_text(model.legal_name),
                        channel=_text(model.channel),
                        business_unit=_text(model.business_unit),
                        billing_center=_text(model.billing_center),
                        billing_center_code=_text(model.billing_center_code),
                        sales_team_class=_text(model.sales_team_class),
                        business_group=_text(model.business_group),
                        city=_text(model.city),
                    )
        return result


__all__ = ["SqlAlchemySettlementMasters"]
