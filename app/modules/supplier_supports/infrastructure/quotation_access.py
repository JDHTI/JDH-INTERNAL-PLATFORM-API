"""The quoter as the internal support requests see it.

The quotation is read through :class:`QuotationService` itself, so its read permission and the
caller's sales scope apply exactly as in the quoter.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.quotations.application.service import QuotationService
from app.modules.quotations.domain import offer
from app.modules.quotations.domain.entities import QuotationLine
from app.modules.supplier_supports.domain.internal_requests import QuotedCustomer, QuotedLine

QUOTATION_KIND = "cotizacion"


def _line(line: QuotationLine) -> QuotedLine:
    with_price = line.new_price is not None
    return QuotedLine(
        sku=line.sku,
        description=line.description,
        unit=line.unit,
        supplier_code=line.supplier_code,
        supplier_name=line.supplier_name,
        cost=line.cost,
        quoted_price=offer.offer_price(line),
        # The margin the quoted price leaves, with the supports in force when there are.
        margin_pct=(
            (line.new_margin_with_support_pct or line.new_margin_pct)
            if with_price
            else (line.current_margin_with_support_pct or line.current_margin_pct)
        ),
        min_margin_pct=line.min_margin_pct,
        is_local=line.is_local,
        support_id=line.support.support_id if line.support else None,
        support_calc_method=line.support.calc_method if line.support else None,
        supplier_min_margin_pct=line.support.supplier_min_margin_pct if line.support else None,
    )


class QuoterAccess:
    """Reads a quotation with the caller's permission and tells whether the caller quotes."""

    def __init__(self, quotations: QuotationService) -> None:
        """Create it.

        Args:
            quotations (QuotationService): The quoter's service.
        """

        self.quotations = quotations

    async def quotation(self, principal: Principal, quotation_id: UUID) -> QuotedCustomer:
        """The quotation and its lines (404/403 as in the quoter)."""

        detail = await self.quotations.get(principal, quotation_id)
        header = detail.quotation
        created = header.created_at.date().isoformat()
        return QuotedCustomer(
            id=header.id,
            kind=header.kind,
            version=header.version,
            label=f"Cotización {header.customer_name or header.customer_nit} · {created}",
            customer_nit=None if header.is_temporary_customer else header.customer_nit,
            customer_name=header.customer_name,
            lines={line.sku: _line(line) for line in detail.lines},
        )

    async def can_quote(self, principal: Principal) -> bool:
        """Whether the caller manages ``cotizador`` (customer quotations)."""

        return await self.quotations.can_manage(principal, QUOTATION_KIND)


__all__ = ["QuoterAccess"]
