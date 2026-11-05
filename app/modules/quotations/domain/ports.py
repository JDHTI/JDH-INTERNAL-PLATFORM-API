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
    TemplateRow,
    ValueTaxKey,
)
from app.modules.quotations.domain.export import PriceListExport
from app.modules.quotations.domain.pricing import StockPlan


class WarehouseSource(Protocol):
    """Live reads of the data warehouse (``consumo``)."""

    async def branches(self, nit: str) -> list[Branch]: ...

    async def price_list_customers(self, price_list_id: str) -> int | None: ...

    async def last_sales(self, nit: str, months: int) -> list[LastSale]: ...

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]: ...

    async def price_list_exists(self, price_list_id: str) -> bool: ...

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
        visible_to: tuple[UUID, Sequence[str]] | None = None,
    ) -> QuotationPage: ...

    async def save(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        expected_version: int,
        replace_lines: bool = False,
        inserted: Sequence[QuotationLine] = (),
        removed: Sequence[UUID] = (),
    ) -> QuotationDetail: ...

    async def delete(self, quotation_id: UUID) -> bool: ...


class SalesScope(Protocol):
    """The customers a person works on (the salespeople module's ``SalesScope``)."""

    scope: str
    seller_codes: tuple[str, ...]
    supervisor_codes: tuple[str, ...]
    # Salesperson codes; ``None`` means every customer.
    codes: tuple[str, ...] | None

    @property
    def is_all(self) -> bool: ...

    def allows(self, salesperson_code: str | None) -> bool: ...


class SalesScopeResolver(Protocol):
    """Decides each person's sales scope from their role and salesperson links."""

    async def scope_for(self, principal: Principal) -> SalesScope: ...


class ManagerPolicy(Protocol):
    """Who quotes (commercial area) or who validates (approvers); admins are both."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    def today(self) -> date: ...


class ExportWriter(Protocol):
    """Turns a price list update into a file."""

    def write(self, export: PriceListExport) -> bytes: ...


class TemplateFile(Protocol):
    """The quotation as a workbook the advisor edits in bulk, and its reading back."""

    def write(self, detail: QuotationDetail) -> bytes: ...

    def read(self, filename: str, content: bytes) -> list[TemplateRow]: ...

    def quotation_id(self, content: bytes) -> UUID | None: ...
