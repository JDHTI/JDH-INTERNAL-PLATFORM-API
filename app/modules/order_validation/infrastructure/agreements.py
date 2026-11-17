"""Read-only access to the supports master and the commercial masters."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sqlalchemy import and_, or_, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS, customer_margin_field
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
# More NITs than this are not sent as parameters: customer supports are filtered in Python.
_MAX_NITS = 2000


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
        # A price list of thousands of customers (list 1: 22 thousand NITs) would pass
        # PostgreSQL's 32,767 parameters per query: its customer supports are filtered here.
        many = len(nits) > _MAX_NITS
        wanted = set(nits)
        customer = [] if many else [or_(S.customer_nit.is_(None), S.customer_nit.in_(list(nits)))]
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
                *customer,
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
            if not many or model.customer_nit is None or model.customer_nit in wanted
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
        unique = list(dict.fromkeys(nits))
        rows = []
        async with self.database.session_factory() as session:
            # In chunks: a price list can have tens of thousands of customers.
            for start in range(0, len(unique), _CHUNK):
                rows += (
                    await session.execute(
                        select(
                            CustomerModel.nit,
                            CustomerModel.channel,
                            CustomerModel.business_unit,
                            CustomerModel.billing_center,
                            CustomerModel.billing_center_code,
                            CustomerModel.sales_team_class,
                            CustomerModel.branch_id,
                            CustomerModel.business_group,
                            CustomerModel.is_active,
                        ).where(CustomerModel.nit.in_(unique[start : start + _CHUNK]))
                    )
                ).all()
        by_nit: dict[str, list] = {}
        for row in rows:
            by_nit.setdefault(row.nit, []).append(row)
        return {nit: customer_scope(nit, branches) for nit, branches in by_nit.items()}


def _majority(values: Sequence[str | None]) -> str | None:
    counts = Counter(value for value in values if value)
    if not counts:
        return None
    return max(sorted(counts), key=lambda value: counts[value])


def customer_scope(nit: str, branches: Sequence) -> CustomerScope:
    """Merge the branches of a client: every channel, unit and regional counts for the scope;
    the margin list follows the majority of the active branches (C.O., team, channel).
    """

    active = [branch for branch in branches if branch.is_active] or list(branches)
    field_name = customer_margin_field(
        _majority([branch.channel for branch in active]),
        _majority([branch.billing_center_code for branch in active]),
        _majority([branch.sales_team_class for branch in active]),
    )
    return CustomerScope(
        nit=nit,
        channels=frozenset(b.channel for b in branches if b.channel),
        business_units=frozenset(b.business_unit for b in branches if b.business_unit),
        regionals=frozenset(b.billing_center for b in branches if b.billing_center),
        margin_field=field_name,
        branches=frozenset(b.branch_id for b in branches if b.branch_id),
        business_group=_majority([branch.business_group for branch in branches]),
    )


__all__ = ["SqlAlchemyAgreementSource"]
