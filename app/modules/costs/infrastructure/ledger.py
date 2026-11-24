"""The history of increases of the products, as Costos writes into it.

The history belongs to ``commercial_masters`` (the quoter reads it); Costos goes through that
module's repository, never its table.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.modules.commercial_masters.domain.price_increases import (
    SOURCE_REQUEST,
    PriceIncreaseValues,
)
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.costs.domain.ports import LedgerEntry


def ledger_values(entry: LedgerEntry) -> PriceIncreaseValues:
    """An approved line as a row of the history: notified, pending in SIESA."""

    return PriceIncreaseValues(
        sku=entry.sku,
        notified_on=entry.notified_on,
        system_updated_on=None,
        increased_on=entry.increased_on,
        supplier_sku=entry.supplier_sku,
        ean=entry.ean,
        regional=entry.regional,
        icui_pct=entry.icui_pct,
        ibua=entry.ibua,
        old_cost_with_tax=entry.old_cost_with_tax,
        old_cost=entry.old_cost,
        new_cost=entry.new_cost,
        new_cost_with_tax=entry.new_cost_with_tax,
        variation_amount=entry.variation_amount,
        variation_pct=entry.variation_pct,
        status=entry.status,
        observations=entry.observations,
        margin_pct=entry.margin_pct,
        price=entry.price,
        notified=True,
        siesa_changed=False,
        source=SOURCE_REQUEST,
        request_line_id=entry.line_id,
    )


class CommercialIncreaseLedger:
    """Writes approved lines into ``product_price_increases``."""

    def __init__(self, repository: SqlAlchemyPriceIncreaseRepository) -> None:
        """Create the ledger.

        Args:
            repository (SqlAlchemyPriceIncreaseRepository): The history's repository.
        """

        self.repository = repository

    async def margins(self, skus: Sequence[str]) -> dict[str, Decimal | None]:
        """The «Lista 1» margin of each reference (the product master's)."""

        return await self.repository.list_margins(list(skus))

    async def record(
        self, entries: Sequence[LedgerEntry], actor: str, now: datetime
    ) -> dict[UUID, UUID]:
        """Upsert the lines by reference, regional, increase date and notification date.

        Returns:
            dict[UUID, UUID]: Request line → the first history row it became (a ``RESTO``
            line becomes one per regional).
        """

        values = [ledger_values(entry) for entry in entries]
        await self.repository.upsert(values, actor, now)
        ids = await self.repository.ids_by_key([value.key for value in values])
        first: dict[UUID, UUID] = {}
        for entry, value in zip(entries, values, strict=True):
            first.setdefault(entry.line_id, ids[value.key])
        return first


__all__ = ["CommercialIncreaseLedger", "ledger_values"]
