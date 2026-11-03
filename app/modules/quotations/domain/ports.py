"""Ports of the quoter."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.entities import Agreement, CustomerScope
from app.modules.quotations.domain.entities import (
    Branch,
    CostInfo,
    CustomerPage,
    ItemInfo,
    LastSale,
    ListPrice,
    ProductMaster,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    Seller,
    ValueTaxKey,
)
from app.modules.quotations.domain.export import PriceListExport


class WarehouseSource(Protocol):
    """Live reads of the data warehouse (``consumo``)."""

    async def branches(self, nit: str) -> list[Branch]: ...

    async def price_list_customers(self, price_list_id: str) -> int | None: ...

    async def last_sales(self, nit: str, months: int) -> list[LastSale]: ...

    async def costs(
        self, skus: Sequence[str], operation_center: str | None
    ) -> dict[str, CostInfo]: ...

    async def list_prices(
        self, price_list_id: str, skus: Sequence[str]
    ) -> dict[str, ListPrice]: ...

    async def value_tax_keys(self, skus: Sequence[str]) -> dict[str, ValueTaxKey]: ...

    async def sellers(self, email: str) -> list[Seller]: ...

    async def items(self, skus: Sequence[str]) -> dict[str, ItemInfo]: ...


class MasterSource(Protocol):
    """The platform's copy of the commercial masters."""

    async def products(self, skus: Sequence[str]) -> dict[str, ProductMaster]: ...

    async def supplier_names(self, codes: Sequence[str]) -> dict[str, str]: ...

    async def customers(
        self, seller_codes: Sequence[str] | None, q: str | None, page: int, size: int
    ) -> CustomerPage: ...


class SupportSource(Protocol):
    """The supports master and the customers' scope (shared with the order validator)."""

    async def agreements(
        self, supplier_codes: Sequence[str], skus: Sequence[str], nits: Sequence[str]
    ) -> list[Agreement]: ...

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerScope]: ...


class QuotationRepository(Protocol):
    """Quotations and their lines."""

    async def create(
        self, quotation: Quotation, lines: Sequence[QuotationLine]
    ) -> QuotationDetail: ...

    async def get(self, quotation_id: UUID) -> QuotationDetail | None: ...

    async def list(
        self,
        *,
        created_by_id: UUID | None,
        status: str | None,
        customer_nit: str | None,
        q: str | None,
        page: int,
        size: int,
    ) -> QuotationPage: ...

    async def save(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        expected_version: int,
        replace_lines: bool = False,
    ) -> QuotationDetail: ...

    async def delete(self, quotation_id: UUID) -> bool: ...


class ManagerPolicy(Protocol):
    async def is_manager(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    def today(self) -> date: ...


class ExportWriter(Protocol):
    """Turns a price list update into a file."""

    def write(self, export: PriceListExport) -> bytes: ...
