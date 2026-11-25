"""Ports of the quoter."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.entities import Agreement, CustomerScope
from app.modules.quotations.domain.entities import (
    BillingCenter,
    Branch,
    CenterWarehouse,
    CostInfo,
    CustomerBranchOption,
    CustomerPage,
    GroupPage,
    ItemInfo,
    LastSale,
    ListItem,
    ListPrice,
    MonthlySale,
    PriceListInfo,
    PriceListLoad,
    PriceVersion,
    ProductMaster,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    Seller,
    SellerContact,
    StagedItem,
    StockRow,
    ValueTaxKey,
)
from app.modules.quotations.domain.export import PriceListExport
from app.modules.quotations.domain.offer import OfferDocument
from app.modules.quotations.domain.pricing import StockPlan


class WarehouseSource(Protocol):
    """Live reads of the data warehouse (``consumo``)."""

    async def branches(self, nit: str) -> list[Branch]: ...

    async def price_list_customers(self, price_list_id: str) -> int | None: ...

    async def last_sales(
        self, nit: str, months: int, branch_id: str | None = None
    ) -> list[LastSale]:
        """The customer's last sales; with ``branch_id``, that branch's only."""
        ...

    async def group_branches(self, nits: Sequence[str]) -> list[Branch]:
        """Every branch of these NITs, each with its ``nit`` (a business group)."""
        ...

    async def group_last_sales(self, nits: Sequence[str], months: int) -> list[LastSale]:
        """:meth:`last_sales` over every branch of these NITs (a business group)."""
        ...

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]: ...

    async def price_list_exists(self, price_list_id: str) -> bool: ...

    async def price_list_ids(self) -> list[str]:
        """Every list code SIESA has (as of yesterday's load)."""
        ...

    async def stock(
        self, skus: Sequence[str], warehouses: Sequence[str], centers: Sequence[str] = ()
    ) -> list[StockRow]:
        """Stock by warehouse of the SKUs in these warehouses and centers (only with some)."""
        ...

    async def list_prices(
        self, price_list_id: str, skus: Sequence[str]
    ) -> dict[str, ListPrice]: ...

    async def value_tax_keys(self, skus: Sequence[str]) -> dict[str, ValueTaxKey]: ...

    async def sellers(self, email: str) -> list[Seller]: ...

    async def items(self, skus: Sequence[str]) -> dict[str, ItemInfo]: ...

    async def seller_contact(self, code: str) -> SellerContact | None: ...

    async def billing_centers(self) -> list[BillingCenter]: ...

    async def center_warehouses(self) -> list[CenterWarehouse]: ...

    async def price_lists(self, q: str, limit: int = 50) -> list[PriceListInfo]: ...

    async def price_list(self, code: str) -> PriceListInfo | None: ...

    async def list_branches(self, price_list_id: str) -> list[Branch]: ...

    async def list_items(self, price_list_id: str) -> list[ListItem]: ...

    async def list_last_sales(
        self,
        skus: Sequence[str],
        months: int,
        customer_keys: Sequence[str] | None,
        timeout: float | None = None,
    ) -> dict[str, LastSale]: ...


class MasterSource(Protocol):
    """The platform's copy of the commercial masters."""

    async def products(self, skus: Sequence[str]) -> dict[str, ProductMaster]: ...

    async def supplier_names(self, codes: Sequence[str]) -> dict[str, str]: ...

    async def pending_increases(self, skus: Sequence[str]) -> dict[str, Decimal]: ...

    async def increase_dates(self, skus: Sequence[str]) -> dict[str, date]: ...

    async def customers(
        self, seller_codes: Sequence[str] | None, q: str | None, page: int, size: int
    ) -> CustomerPage: ...

    async def business_groups(
        self, seller_codes: Sequence[str] | None, q: str | None, page: int, size: int
    ) -> GroupPage:
        """Business groups with an active customer of these salespeople (``None``: all)."""
        ...

    async def group_nits(self, group: str) -> list[str]:
        """The NITs of a business group (its active customers)."""
        ...

    async def customer_branches(self, nit: str) -> list[CustomerBranchOption]:
        """The branches of a customer, active first (a quotation by branch, 2026-11-24)."""
        ...


class SupportSource(Protocol):
    """The supports master and the customers' scope (shared with the order validator)."""

    async def agreements(
        self, supplier_codes: Sequence[str], skus: Sequence[str], nits: Sequence[str]
    ) -> list[Agreement]: ...

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerScope]: ...

    async def branch_scope(self, nit: str, branch_id: str) -> CustomerScope | None:
        """One branch of the customer, for a quotation by branch (2026-11-24)."""
        ...


class QuotationRepository(Protocol):
    """Quotations and their lines."""

    async def create(
        self, quotation: Quotation, lines: Sequence[QuotationLine]
    ) -> QuotationDetail: ...

    async def get(self, quotation_id: UUID) -> QuotationDetail | None: ...

    async def get_lines(
        self, quotation_id: UUID, skus: Sequence[str]
    ) -> QuotationDetail | None: ...

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
        kind: str = "cotizacion",
        created_by_ids: Collection[UUID] | None = None,
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

    async def write(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        expected_version: int,
        replace_lines: bool = False,
        inserted: Sequence[QuotationLine] = (),
        removed: Sequence[UUID] = (),
    ) -> None: ...

    async def delete(self, quotation_id: UUID) -> bool: ...

    async def delete_stale_drafts(self, created_before: datetime) -> list[UUID]:
        """Delete the customer drafts never sent to validation created before that moment."""
        ...

    async def reserved_list_ids(self) -> list[str]:
        """Codes of the new lists already validated (pending load or loaded): SIESA may not
        have them yet, but they are taken."""
        ...

    async def set_categories(self, quotation_id: UUID, categories: dict[UUID, str]) -> None: ...

    async def fill_last_sales(
        self, quotation_id: UUID, by_sku: Mapping[str, Mapping[str, Any]], warning: str
    ) -> bool:
        """Write the lines' last-sale columns and drop ``warning`` without a new version."""
        ...

    async def has_warning(self, quotation_id: UUID, warning: str) -> bool: ...


class StagingRepository(QuotationRepository, Protocol):
    """Quotations plus the staging of the list updates and their loads."""

    async def staged(self, kind: str, price_list_id: str | None = None) -> list[StagedItem]: ...

    async def staged_elsewhere(
        self, price_list_id: str, skus: Sequence[str], quotation_id: UUID
    ) -> dict[str, UUID]: ...

    async def create_load(self, load: PriceListLoad, line_ids: Sequence[UUID]) -> PriceListLoad: ...

    async def loads(self, kind: str, limit: int = 30) -> list[PriceListLoad]: ...

    async def get_load(self, load_id: UUID) -> PriceListLoad | None: ...


class InsightSource(Protocol):
    """Warehouse reads of an item's detail in a list update."""

    async def stock(
        self, skus: Sequence[str], warehouses: Sequence[str], centers: Sequence[str] = ()
    ) -> list[StockRow]: ...

    async def price_history(
        self, price_list_id: str, sku: str, months: int
    ) -> list[PriceVersion]: ...

    async def monthly_sales(
        self, sku: str, months: int, customer_keys: Sequence[str] | None
    ) -> list[MonthlySale]: ...


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


class TeamLookup(Protocol):
    """The call center (2026-11-23): who its leader sees."""

    async def led_members(self, principal: Principal) -> frozenset[UUID] | None:
        """The members of the area when the caller leads it; ``None`` otherwise."""
        ...


class ManagerPolicy(Protocol):
    """Who quotes (commercial area) or who validates (approvers); admins are both."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    def today(self) -> date: ...


class ExportWriter(Protocol):
    """Turns a price list update into a file."""

    def write(self, export: PriceListExport) -> bytes: ...


class OfferWriter(Protocol):
    """Turns the customer offer into a workbook."""

    def write(self, document: OfferDocument) -> bytes: ...


class LinesWriter(Protocol):
    """Turns every line of a quotation, with its costs, into a workbook for BI; with
    ``template``, the list update template the advisors upload back (no costs)."""

    def write(
        self,
        detail: QuotationDetail,
        center_names: Mapping[str, str | None],
        template: bool = False,
    ) -> bytes: ...


@dataclass(frozen=True)
class TemplateContent:
    """What an uploaded list update template says: its references, in the file's order and
    once each, and the price list in its header (``None`` if the row is gone)."""

    skus: tuple[str, ...]
    price_list_id: str | None


class TemplateReader(Protocol):
    """Reads an uploaded list update template.

    Raises:
        InvalidInputError: It is not a workbook, or not the template.
    """

    def read(self, content: bytes) -> TemplateContent: ...


class SupportRequestRef(Protocol):
    """The latest internal support request of a SKU in a quotation."""

    id: Any
    status: str
    decision: str | None
    approved_margin_pct: Any


class SupportRequestLinks(Protocol):
    """Internal support requests asked from a quotation (supports module)."""

    async def is_reviewer(self, principal: Principal) -> bool:
        """Whether the caller decides them (BI or a designated reviewer)."""
        ...

    async def latest_for(self, quotation_id: Any) -> Mapping[str, SupportRequestRef]:
        """The latest request of each SKU of the quotation."""
        ...
