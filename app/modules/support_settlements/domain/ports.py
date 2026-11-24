"""Ports of the support settlement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.identity.domain.principal import Principal
from app.modules.support_settlements.domain.entities import (
    CustomerBranch,
    Product,
    Quote,
    SaleLine,
    Settlement,
    SettlementResult,
    SettlementSupport,
)


class Policy(Protocol):
    """A permission: who manages (and reads) the settlement."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class LeaderPolicy(Protocol):
    """Only the area's leader (and platform administrators) reopens a closed month."""

    async def is_manager(self, principal: Principal) -> bool: ...


class SettlementMasters(Protocol):
    """The platform's own data: supports, products and customers."""

    async def supports(self, start: date, following: date) -> list[SettlementSupport]: ...

    async def products(
        self, supplier_codes: Sequence[str], skus: Sequence[str]
    ) -> dict[str, Product]: ...

    async def customers(self, customer_keys: Sequence[str]) -> dict[str, CustomerBranch]: ...


class SalesSource(Protocol):
    """The month's sales and the supplier quotations, from the warehouse."""

    async def sales(
        self, start: date, following: date, product_keys: Sequence[str]
    ) -> list[SaleLine]: ...

    async def quotes(
        self, start: date, following: date, product_keys: Sequence[str]
    ) -> dict[str, list[Quote]]: ...


class SettlementRepository(Protocol):
    """Stored settlements: the header, its references, lines and supports applied."""

    async def get(self, period: str) -> Settlement | None: ...

    async def list(self) -> list[Settlement]: ...

    async def replace(
        self,
        period: str,
        result: SettlementResult,
        actor: str,
        now: datetime,
        *,
        close: bool = False,
    ) -> Settlement: ...

    async def reopen(self, period: str, actor: str, now: datetime) -> Settlement: ...

    async def suppliers(self, settlement_id: Any) -> list[Mapping[str, Any]]: ...

    async def references(
        self, settlement_id: Any, supplier_code: str | None
    ) -> list[Mapping[str, Any]]: ...

    async def lines(
        self,
        settlement_id: Any,
        *,
        supplier_code: str | None,
        sku: str | None,
        nit: str | None,
        status: str | None,
        page: int,
        size: int,
    ) -> tuple[list[Mapping[str, Any]], int]: ...

    async def breakdown(
        self, settlement_id: Any, by: str, supplier_code: str | None
    ) -> list[Mapping[str, Any]]: ...

    async def export_rows(
        self, settlement_id: Any, supplier_code: str | None
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]: ...


__all__ = [
    "LeaderPolicy",
    "Policy",
    "SalesSource",
    "SettlementMasters",
    "SettlementRepository",
]
