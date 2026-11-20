"""Use cases of the quoter."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.commercial_masters.domain.margins import customer_margin_field
from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.catalog import Section
from app.modules.order_validation.domain.entities import Agreement, CustomerScope
from app.modules.order_validation.domain.rules import BASIS
from app.modules.quotations.application.sales_backfill import SalesBackfill
from app.modules.quotations.domain import list_codes, offer, pricing, rules, vocabulary
from app.modules.quotations.domain.catalog import catalog
from app.modules.quotations.domain.entities import (
    BillingCenter,
    Branch,
    CostInfo,
    CustomerContext,
    CustomerPage,
    GroupPage,
    ItemInfo,
    LastSale,
    ListItem,
    ListPrice,
    PriceListInfo,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    QuotationPolicy,
    Seller,
    SellerContact,
    StockRow,
    StockTotal,
    TemporaryCustomer,
    stock_totals,
)
from app.modules.quotations.domain.export import (
    ExportRow,
    ExportTaxRow,
    NewPriceList,
    PriceListExport,
)
from app.modules.quotations.domain.ports import (
    Calendar,
    ExportWriter,
    LinesWriter,
    ManagerPolicy,
    MasterSource,
    OfferWriter,
    QuotationRepository,
    SalesScope,
    SalesScopeResolver,
    SupportRequestLinks,
    SupportSource,
    WarehouseSource,
)

logger = logging.getLogger(__name__)
MAX_PAGE_SIZE = 100
MAX_PRICE = Decimal(10) ** 15
# Most SKUs of a list update: list 1 (GENERAL) has about 7,600.
LIST_MAX_LINES = 10_000
# A list with more active branches than this reads the whole company's last sales: reading
# by its 28 thousand branches (list 1) passed 20 s.
LIST_SALES_BRANCH_LIMIT = 1_000
# SKUs one paste may carry.
MAX_PASTED_SKUS = 500
# Excel columns arrive one per line; lists may use commas, semicolons, tabs or spaces.
_SEPARATORS = re.compile(r"[\s,;|]+")
# A number cell copied from Excel may come as ``26278.0``.
_EXCEL_INTEGER = re.compile(r"^(\d+)\.0+$")
_LIST_ID = re.compile(r"^[A-Z0-9]{1,3}$")
_LIST_NAME_LENGTH = 40
# Longest description and supplier name of a local product.
LOCAL_TEXT_LENGTH = 120
_LOCAL_NO_MARGIN = "Producto solo para cotización: sin costo para calcular el precio"
_QUANTITY_STEP = Decimal("0.01")
# Characters a file name may not carry on Windows, macOS or Linux.
_FILE_UNSAFE = re.compile(r'[\\/:*?"<>|]+')
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def parse_skus(values: Sequence[str]) -> tuple[list[str], int]:
    """SKUs of a pasted list, in order and without repeats.

    Args:
        values (Sequence[str]): SKUs or chunks of pasted text.

    Returns:
        tuple[list[str], int]: The unique SKUs and how many repeats were dropped.
    """

    tokens: list[str] = []
    for value in values:
        for token in _SEPARATORS.split(value or ""):
            token = token.strip().strip("'\"")
            if token:
                match = _EXCEL_INTEGER.match(token)
                tokens.append(match.group(1) if match else token)
    unique = list(dict.fromkeys(tokens))
    return unique, len(tokens) - len(unique)


def list_name(value: str) -> str:
    """A SIESA list description: plain ASCII upper case, at most 40 characters."""

    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = "".join(char if char.isascii() and char.isprintable() else " " for char in text)
    return " ".join(text.upper().split())[:_LIST_NAME_LENGTH].rstrip()


@dataclass(frozen=True)
class ImportResult:
    """What a pasted list did: SKUs added, revalidated, unknown, inactive and repeats."""

    added: tuple[str, ...]
    revalidated: tuple[str, ...]
    not_found: tuple[str, ...]
    inactive: tuple[str, ...]
    repeated: int


@dataclass(frozen=True)
class Skipped:
    """A SKU a change could not price, and why."""

    sku: str
    reason: str
    row_number: int | None = None


@dataclass(frozen=True)
class _Entry:
    """A SKU to price: its place in the quotation and where its data comes from."""

    sku: str
    position: int
    line_id: UUID
    sale: LastSale | None = None
    item: ItemInfo | None = None
    # A list update's line: ``lista`` or ``agregado``.
    origin: str | None = None


@dataclass(frozen=True)
class _Audience:
    """Who a list update serves: the NITs whose supports count and the branches whose sales
    give the last sale (``None``: the whole company, for a list of thousands of branches)."""

    nits: tuple[str, ...]
    sale_keys: tuple[str, ...] | None


def _list_entry_of(line: QuotationLine) -> _Entry:
    """A list update's stored line as an entry: its item and the last sale it was built with
    (the list price is read again)."""

    item = ItemInfo(
        sku=line.sku,
        description=line.description,
        unit=line.unit,
        vat_pct=line.vat_pct,
        supplier_code=line.supplier_code,
        pack_factor=line.pack_factor,
        category=line.category,
    )
    sale = None
    if line.last_sale_date is not None and line.last_sale_price is not None:
        sale = LastSale(
            sku=line.sku,
            sale_date=line.last_sale_date,
            document=line.last_document,
            sale_unit=line.last_sale_unit,
            quantity=line.last_quantity or Decimal(1),
            gross_price=line.last_gross_price or line.last_sale_price,
            net_price=line.last_sale_price,
            discount_pct=line.last_discount_pct,
            sales_in_window=line.sales_in_window,
            unit_cost=line.last_sale_cost,
        )
    return _Entry(line.sku, line.position, line.id, sale=sale, item=item, origin=line.origin)


def _entry_of(line: QuotationLine, for_list: bool = False) -> _Entry:
    """Rebuild a stored line's source, to price it again without asking for its last sale."""

    if for_list:
        return _list_entry_of(line)
    if line.origin == vocabulary.HISTORY and line.last_price is not None:
        sale = LastSale(
            sku=line.sku,
            sale_date=line.last_sale_date or date.min,
            document=line.last_document,
            sale_unit=line.last_sale_unit,
            quantity=line.last_quantity or Decimal(1),
            gross_price=line.last_gross_price or line.last_price,
            net_price=line.last_price,
            discount_pct=line.last_discount_pct,
            sales_in_window=line.sales_in_window,
            description=line.description,
            unit=line.unit,
            vat_pct=line.vat_pct,
            supplier_code=line.supplier_code,
            pack_factor=line.pack_factor,
            category=line.category,
        )
        return _Entry(line.sku, line.position, line.id, sale=sale)
    item = ItemInfo(
        sku=line.sku,
        description=line.description,
        unit=line.unit,
        vat_pct=line.vat_pct,
        supplier_code=line.supplier_code,
        pack_factor=line.pack_factor,
        category=line.category,
    )
    return _Entry(line.sku, line.position, line.id, item=item)


@dataclass(frozen=True)
class LineChange:
    """The advisor's price for a SKU (``None`` clears it), or the margin to price it at, and
    the quantity the customer would buy.

    ``set_price`` is false for a change that only carries a quantity; ``set_quantity`` says
    the quantity was sent (``None`` then clears it).
    """

    sku: str
    new_price: Decimal | None = None
    margin_pct: Decimal | None = None
    quantity: Decimal | str | None = None
    set_price: bool = True
    set_quantity: bool = False


@dataclass(frozen=True)
class Permissions:
    can_manage: bool
    seller: Seller | None
    can_validate: bool = False
    # ``all``, ``team``, ``mine`` or ``none``; ``None`` when no scope resolver is wired.
    sales_scope: str | None = None
    # May ask an internal support from a customer quotation (quotes and does not review).
    can_request_support: bool = False
    # The calculation rules (costs, warehouses, formulas) are BI's; Comercial gets the manual.
    can_see_rules: bool = False


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    media_type: str


# References one stock breakdown may ask for.
MAX_STOCK_SKUS = 500


@dataclass(frozen=True)
class StockBreakdown:
    """The stock of some references in a quotation's warehouses: where it is read, every
    warehouse with stock and the total per SKU."""

    scope: pricing.StockScope
    # The references asked (SIESA lines of the quotation), with or without stock.
    skus: tuple[str, ...]
    rows: tuple[StockRow, ...]
    totals: dict[str, StockTotal]


@dataclass(frozen=True)
class _Setup:
    """How a customer's quotation is costed and priced."""

    family: str
    # The stock plans compared, by the center each one stands for (see pricing.stock_plans).
    plans: dict[str | None, pricing.StockPlan]
    regional: str

    @property
    def stock_label(self) -> str:
        return pricing.stock_label(self.plans)


def normalize_nit(value: str) -> str:
    """Digits of a NIT without a separated check digit (``900.123.456-7`` -> ``900123456``)."""

    text = re.sub(r"-\s*\d\s*$", "", value.strip())
    nit = clean_nit(text)
    if not nit:
        raise InvalidInputError("Escribe el NIT del cliente")
    return nit


def _margin_field(context: CustomerContext, scope: CustomerScope | None = None) -> str:
    """The customer's margin list: the platform's master (with the seller's team) when it
    knows the customer, else the channel and operation center the quotation read."""

    if scope is not None and scope.margin_field:
        return scope.margin_field
    return customer_margin_field(context.channel, context.billing_center_code)


_NO_ID = UUID(int=0)


def _same_group(value: str | None, group: str) -> bool:
    return " ".join((value or "").split()).casefold() == " ".join(group.split()).casefold()


def _group_agreements(agreements: Sequence[Agreement], group: str) -> list[Agreement]:
    """The supports of a business group's quotation (2026-11-19): the general ones (no NIT)
    and the group's (``customer_group``). A support of one NIT of the group never covers the
    whole group.

    The group's supports are loaded one per NIT (``bulk/groups``): they are taken as one,
    for the whole quotation (no NIT, no branch), and the copies collapse.
    """

    kept: dict[Agreement, Agreement] = {}
    for item in agreements:
        if item.customer_nit is None and not item.customer_group:
            kept.setdefault(item, item)
            continue
        if not _same_group(item.customer_group, group):
            continue
        whole = replace(item, customer_nit=None, customer_branch_id=None)
        kept.setdefault(replace(whole, id=_NO_ID, dwh_unit_cost=None), whole)
    return list(kept.values())


def _setup(context: CustomerContext) -> _Setup:
    basis = BASIS.get(_margin_field(context), "lista")
    return _Setup(
        family=pricing.family(context.price_list_id, basis),
        plans=pricing.stock_plans(context.price_list_id, context.cost_centers),
        regional=pricing.regional_of(context.cost_centers, context.billing_center_code),
    )


class QuotationService:
    """Build quotations from the warehouse, price them, validate them and export the update."""

    def __init__(
        self,
        repository: QuotationRepository,
        warehouse: WarehouseSource,
        masters: MasterSource,
        supports: SupportSource,
        managers: ManagerPolicy,
        calendar: Calendar,
        workbook: ExportWriter,
        flat_file: ExportWriter,
        policy: QuotationPolicy | None = None,
        max_lines: int = 3000,
        approvers: ManagerPolicy | None = None,
        sales: SalesScopeResolver | None = None,
        offer_writer: OfferWriter | None = None,
        lines_writer: LinesWriter | None = None,
        list_managers: ManagerPolicy | None = None,
        list_approvers: ManagerPolicy | None = None,
        main_lists: Sequence[str] = (),
        main_managers: ManagerPolicy | None = None,
        main_approvers: ManagerPolicy | None = None,
        support_requests: SupportRequestLinks | None = None,
        sales_backfill: SalesBackfill | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (QuotationRepository): Quotations and lines.
            warehouse (WarehouseSource): Live warehouse reads.
            masters (MasterSource): Local master copy (products, suppliers, customers).
            supports (SupportSource): Supports master and customer scope.
            managers (ManagerPolicy): Who may quote (commercial area and admins).
            calendar (Calendar): Bogotá calendar.
            workbook (ExportWriter): Writes the update workbook.
            flat_file (ExportWriter): Writes the SIESA import file.
            policy (QuotationPolicy | None): The general rule.
            max_lines (int): Most SKUs per quotation.
            approvers (ManagerPolicy | None): Who validates a confirmed quotation; without
                one only administrators do.
            sales (SalesScopeResolver | None): Each person's customers (salespeople module).
                Without one every reader sees every customer and quotation.
            offer_writer (OfferWriter | None): Writes the offer the customer receives.
            lines_writer (LinesWriter | None): Writes BI's workbook of lines and costs.
            list_managers (ManagerPolicy | None): Who updates price lists (module
                ``actualizacion-listas``); without one only administrators do.
            list_approvers (ManagerPolicy | None): Who validates list updates; without one
                only administrators do.
            main_lists (Sequence[str]): The main lists (general, wholesale, Market): only
                module ``listas-principales`` updates them, never ``actualizacion-listas``.
            main_managers (ManagerPolicy | None): Who updates the main lists.
            main_approvers (ManagerPolicy | None): Who validates their items (BI's analysts
                and leader).
            support_requests (SupportRequestLinks | None): Internal support requests asked
                from a customer quotation (supports module): who reviews them and the latest
                one of each SKU. Without it nobody asks from the quoter.
            sales_backfill (SalesBackfill | None): Reads again, in the background, the last
                sales of a list update built without them. Without it only a refresh does.
        """

        self.sales_backfill = sales_backfill
        self.support_requests = support_requests
        self.list_managers = list_managers
        self.list_approvers = list_approvers
        self.main_managers = main_managers
        self.main_approvers = main_approvers
        self.list_max_lines = LIST_MAX_LINES
        # Seconds the last sales of a list update may take; ``None`` is the warehouse's own
        # limit. The background sync of the main lists raises it: list 1 and 1M took 32 to 33 s.
        self.list_sales_timeout: float | None = None
        self.main_list_order = tuple(
            dict.fromkeys(code.strip() for code in main_lists if code.strip())
        )
        self.main_lists = frozenset(self.main_list_order)
        self.repository = repository
        self.offer_writer = offer_writer
        self.lines_writer = lines_writer
        self.warehouse = warehouse
        self.masters = masters
        self.supports = supports
        self.managers = managers
        self.calendar = calendar
        self.workbook = workbook
        self.flat_file = flat_file
        self.policy = policy or QuotationPolicy()
        self.max_lines = max_lines
        self.approvers = approvers
        self.sales = sales

    # ------------------------------------------------------------ access

    def _policies(self, kind: str) -> tuple[ManagerPolicy | None, ManagerPolicy | None]:
        """Who manages and who validates each kind (module ``cotizador`` or
        ``actualizacion-listas``)."""

        if kind == vocabulary.LIST_UPDATE:
            return self.list_managers, self.list_approvers
        if kind == vocabulary.MAIN_LIST:
            return self.main_managers, self.main_approvers
        if kind != vocabulary.QUOTATION:
            raise InvalidInputError("Tipo desconocido: cotizacion, lista o principal")
        return self.managers, self.approvers

    @staticmethod
    def _noun(kind: str) -> str:
        return "la actualización" if kind in vocabulary.LIST_KINDS else "la cotización"

    async def can_manage(self, principal: Principal, kind: str = vocabulary.QUOTATION) -> bool:
        """Whether the caller quotes: Comercial, and BI too (it validates and also quotes).
        For list updates, whoever manages ``actualizacion-listas``."""

        managers, approvers = self._policies(kind)
        if principal.is_platform_admin:
            return True
        if managers is not None and await managers.is_manager(principal):
            return True
        return approvers is not None and await approvers.is_manager(principal)

    async def can_validate(self, principal: Principal, kind: str = vocabulary.QUOTATION) -> bool:
        """Who validates each kind. A customer quotation pending validation is also validated
        from ``actualizacion-listas`` (BI, 2026-11-19): whoever validates list updates
        validates it too."""

        _, approvers = self._policies(kind)
        if principal.is_platform_admin:
            return True
        if approvers is not None and await approvers.is_manager(principal):
            return True
        return (
            kind == vocabulary.QUOTATION
            and self.list_approvers is not None
            and await self.list_approvers.is_manager(principal)
        )

    async def _sees_all(self, principal: Principal, kind: str = vocabulary.QUOTATION) -> bool:
        """Validators and moderators read every quotation; anyone else only the ones they made."""

        return principal.is_moderator or await self.can_validate(principal, kind)

    async def _require_owner_or_validator(
        self, principal: Principal, quotation: Quotation, action: str
    ) -> None:
        if quotation.created_by_id == principal.user_id or await self.can_validate(
            principal, quotation.kind
        ):
            return
        # The main lists are one shared board: whoever manages the module edits every list.
        if quotation.kind == vocabulary.MAIN_LIST and await self.can_manage(
            principal, quotation.kind
        ):
            return
        raise ForbiddenError(f"Solo quien creó {self._noun(quotation.kind)} puede {action}")

    async def _require_manager(
        self, principal: Principal, kind: str = vocabulary.QUOTATION
    ) -> None:
        if not await self.can_manage(principal, kind):
            if kind == vocabulary.MAIN_LIST:
                raise ForbiddenError("No tienes permiso para actualizar las listas principales")
            if kind == vocabulary.LIST_UPDATE:
                raise ForbiddenError("No tienes permiso para actualizar listas de precios")
            raise ForbiddenError("No tienes permiso para usar el cotizador")

    async def can_read(self, principal: Principal, kind: str = vocabulary.QUOTATION) -> bool:
        """Read on the quoter in either area (comercial quotes, BI validates); the customer
        quotations also for whoever validates list updates (:meth:`can_validate`)."""

        managers, approvers = self._policies(kind)
        if principal.is_platform_admin:
            return True
        if managers is not None and await managers.can_read(principal):
            return True
        if approvers is not None and await approvers.can_read(principal):
            return True
        return kind == vocabulary.QUOTATION and await self.can_validate(principal, kind)

    async def _require_reader(self, principal: Principal, kind: str = vocabulary.QUOTATION) -> None:
        if not await self.can_read(principal, kind):
            if kind == vocabulary.MAIN_LIST:
                raise ForbiddenError("No tienes permiso para consultar las listas principales")
            if kind == vocabulary.LIST_UPDATE:
                raise ForbiddenError(
                    "No tienes permiso para consultar las actualizaciones de listas"
                )
            raise ForbiddenError("No tienes permiso para consultar el cotizador")

    def is_main_list(self, price_list_id: str | None) -> bool:
        """One of the main lists: only module ``listas-principales`` updates it."""

        return (price_list_id or "").strip() in self.main_lists

    def list_allowed(self, kind: str, price_list_id: str | None) -> bool:
        """Whether a list update of this kind may take the list: the main lists go to
        ``principal`` and every other list to ``lista``."""

        return (kind == vocabulary.MAIN_LIST) == self.is_main_list(price_list_id)

    def _check_list_kind(self, kind: str, price_list_id: str | None) -> None:
        """Raise when the list belongs to the other module.

        Raises:
            ForbiddenError: A main list asked from ``actualizacion-listas``.
            InvalidInputError: Any other list asked from ``listas-principales``.
        """

        if self.list_allowed(kind, price_list_id):
            return
        if kind == vocabulary.MAIN_LIST:
            raise InvalidInputError(
                f"La lista {price_list_id} no es una lista principal: actualízala en "
                "Actualización de listas"
            )
        raise ForbiddenError(
            f"La lista {price_list_id} es una lista principal: solo se actualiza en Listas "
            "principales (BI)"
        )

    async def _check_restricted(self, principal: Principal, quotation: Quotation) -> None:
        """A stored list update whose list moved to the other module is not touched here."""

        if quotation.is_list_update:
            self._check_list_kind(quotation.kind, quotation.price_list_id)

    async def permissions(
        self, principal: Principal, kind: str = vocabulary.QUOTATION
    ) -> Permissions:
        """Whether the caller may quote or validate, and the salesperson linked by e-mail.

        A warehouse outage never hides the page: the seller is just unknown. A list update
        has no salesperson.
        """

        can_manage = await self.can_manage(principal, kind)
        seller = None
        if can_manage and kind == vocabulary.QUOTATION:
            try:
                sellers = await self.warehouse.sellers(principal.email)
            except Exception:  # noqa: BLE001 - the link is a convenience
                sellers = []
            seller = sellers[0] if sellers else None
        scope = await self._scope(principal) if kind == vocabulary.QUOTATION else None
        # Everyone who quotes asks, approvers too (2026-11-19); nobody approves their own.
        can_request = (
            kind == vocabulary.QUOTATION and can_manage and self.support_requests is not None
        )
        return Permissions(
            can_manage,
            seller,
            await self.can_validate(principal, kind),
            scope.scope if scope is not None else None,
            can_request,
            await self.can_see_rules(principal),
        )

    async def can_see_rules(self, principal: Principal) -> bool:
        """BI's members (the quoter's validators and the main lists' area) and admins see
        the calculation rules; Comercial sees the operating manual."""

        if principal.is_platform_admin:
            return True
        for policy in (
            self.approvers,
            self.list_approvers,
            self.main_approvers,
            self.main_managers,
        ):
            if policy is not None and await policy.can_read(principal):
                return True
        return False

    async def support_requests_for(self, detail: QuotationDetail) -> Mapping[str, Any]:
        """The latest internal support request of each SKU of a customer quotation.

        Never fails the quotation: when the supports side cannot answer, no request shows.
        """

        if self.support_requests is None or detail.quotation.kind != vocabulary.QUOTATION:
            return {}
        try:
            return await self.support_requests.latest_for(detail.quotation.id)
        except Exception as exc:  # noqa: BLE001 - the request status is a convenience
            logger.warning(
                "quotations: support requests of %s not read: %s", detail.quotation.id, exc
            )
            return {}

    def rule_catalog(self) -> tuple[Section, ...]:
        return catalog(self.policy)

    # ------------------------------------------------------------ sales scope

    async def _scope(self, principal: Principal) -> SalesScope | None:
        """The caller's sales scope; ``None`` when no resolver is wired (everything)."""

        return await self.sales.scope_for(principal) if self.sales is not None else None

    @staticmethod
    def _restricted(scope: SalesScope | None) -> bool:
        return scope is not None and not scope.is_all

    async def _check_visible(self, principal: Principal, quotation: Quotation) -> None:
        """Outside validation, each person reaches only the quotations they created."""

        if quotation.created_by_id == principal.user_id or await self._sees_all(
            principal, quotation.kind
        ):
            return
        # The main lists are one shared board: whoever reads the module sees every list.
        if quotation.kind == vocabulary.MAIN_LIST and await self.can_read(
            principal, quotation.kind
        ):
            return
        if quotation.is_list_update:
            raise ForbiddenError("Solo puedes ver las actualizaciones que creaste")
        raise ForbiddenError("Solo puedes ver las cotizaciones que creaste")

    async def customers(
        self,
        principal: Principal,
        scope: str,
        q: str | None,
        page: int,
        size: int,
        kind: str = vocabulary.QUOTATION,
    ) -> tuple[CustomerPage, bool]:
        """Customers to quote: the caller's portfolio (``mine``) or all.

        With a scope resolver the server decides: a salesperson or supervisor gets their
        portfolio or their team's whatever ``scope`` asks, and someone without a link gets
        none. Only a person whose scope is ``all`` chooses between ``mine`` and ``all``.

        Returns:
            tuple[CustomerPage, bool]: The page and whether the caller is linked to a seller.
        """

        if kind in vocabulary.LIST_KINDS:
            # A list update is about the list, not a portfolio: every customer can be searched.
            await self._require_manager(principal, kind)
            _page(page, size)
            return await self.masters.customers(None, q, page, size), False
        await self._require_manager(principal)
        _page(page, size)
        sales = await self._scope(principal)
        if sales is not None:
            linked = bool(sales.seller_codes or sales.supervisor_codes)
            if sales.is_all:
                codes = list(sales.seller_codes) if scope == "mine" else None
            else:
                codes = list(sales.codes or ())
            return await self.masters.customers(codes, q, page, size), linked
        if scope == "mine":
            codes = [seller.code for seller in await self.warehouse.sellers(principal.email)]
            return await self.masters.customers(codes, q, page, size), bool(codes)
        try:
            linked = bool(await self.warehouse.sellers(principal.email))
        except Exception:  # noqa: BLE001 - searching every customer needs no warehouse
            linked = False
        return await self.masters.customers(None, q, page, size), linked

    async def business_groups(
        self, principal: Principal, scope: str, q: str | None, page: int, size: int
    ) -> tuple[GroupPage, bool]:
        """Business groups to quote, with the same portfolio rule as :meth:`customers`: a
        salesperson or supervisor gets the groups of their customers (or their team's), and
        only a person who sees every customer chooses between ``mine`` and ``all``.

        Returns:
            tuple[GroupPage, bool]: The page and whether the caller is linked to a seller.
        """

        await self._require_manager(principal)
        _page(page, size)
        sales = await self._scope(principal)
        if sales is not None:
            linked = bool(sales.seller_codes or sales.supervisor_codes)
            if sales.is_all:
                codes = list(sales.seller_codes) if scope == "mine" else None
            else:
                codes = list(sales.codes or ())
            return await self.masters.business_groups(codes, q, page, size), linked
        if scope == "mine":
            codes = [seller.code for seller in await self.warehouse.sellers(principal.email)]
            return await self.masters.business_groups(codes, q, page, size), bool(codes)
        return await self.masters.business_groups(None, q, page, size), False

    # ------------------------------------------------------------ build

    async def _context(self, nit: str, scope: SalesScope | None = None) -> CustomerContext:
        branches = await self.warehouse.branches(nit)
        if not branches:
            raise NotFoundError(f"No se encontró el cliente con NIT {nit}")
        if self._restricted(scope) and not any(
            scope.allows(branch.salesperson_code)  # type: ignore[union-attr]
            for branch in branches
        ):
            raise ForbiddenError(f"El cliente {nit} no está en tu cartera")
        draft = rules.customer_context(nit, branches)
        shared = await self.warehouse.price_list_customers(draft.price_list_id)
        context = rules.customer_context(nit, branches, shared)
        if context.price_list_id == vocabulary.GENERAL_PRICE_LIST:
            context = replace(context, warnings=(*context.warnings, "lista_general"))
        return context

    async def _group_context(self, group: str, scope: SalesScope | None = None) -> CustomerContext:
        """A business group as one customer (2026-11-19): the branches of every NIT decide
        the list, channel and center by majority (``listas_distintas`` when they differ); the
        NIT with the most active branches stands for the group. In the caller's scope when
        one of its branches is."""

        name = (group or "").strip()
        if not name:
            raise InvalidInputError("Elige el grupo empresarial")
        nits = await self.masters.group_nits(name)
        if not nits:
            raise NotFoundError(f"No se encontró el grupo empresarial {name}")
        branches = await self.warehouse.group_branches(nits)
        if not branches:
            raise NotFoundError(f"El grupo empresarial {name} no tiene sucursales en SIESA")
        if self._restricted(scope) and not any(
            scope.allows(branch.salesperson_code)  # type: ignore[union-attr]
            for branch in branches
        ):
            raise ForbiddenError(f"El grupo empresarial {name} no está en tu cartera")
        active: dict[str, int] = {}
        for branch in branches:
            if branch.nit:
                active[branch.nit] = active.get(branch.nit, 0) + int(branch.is_active)
        leader = min(active or {nits[0]: 0}, key=lambda nit: (-active.get(nit, 0), nit))
        draft = rules.customer_context(leader, branches)
        shared = await self.warehouse.price_list_customers(draft.price_list_id)
        context = replace(
            rules.customer_context(leader, branches, shared),
            name=name,
            business_group=name,
            group_nits=tuple(nits),
        )
        if context.price_list_id == vocabulary.GENERAL_PRICE_LIST:
            context = replace(context, warnings=(*context.warnings, "lista_general"))
        return context

    async def _price(
        self,
        context: CustomerContext,
        entries: Sequence[_Entry],
        audience: _Audience | None = None,
    ) -> tuple[list[QuotationLine], date | None]:
        """Price SKUs (from the sales window or added) keeping each entry's id and position.

        The product copy goes first (it gives the supplier of each SKU); then the warehouse
        reads and the supports are fetched at the same time.

        With an ``audience`` (a list update) each line is compared against the list's price
        in force (a placeholder of $1 or less is no price), the supports are those of every
        customer of the list, and each entry carries its last sale.
        """

        if not entries:
            return [], None
        setup = _setup(context)
        skus = [entry.sku for entry in entries]
        sources = {entry.sku: entry.sale or entry.item for entry in entries}
        products = await self.masters.products(skus)

        def supplier_of(sku: str) -> str | None:
            product = products.get(sku)
            source = sources.get(sku)
            return (product.supplier_code if product else None) or (
                source.supplier_code if source else None
            )

        codes = sorted({code for code in map(supplier_of, skus) if code})

        async def list_prices() -> dict[str, ListPrice]:
            # A temporary customer has no list in SIESA.
            if not context.price_list_id:
                return {}
            return await self.warehouse.list_prices(context.price_list_id, skus)

        async def costs_by_center() -> dict[str, CostInfo]:
            # One read per center, at the same time; each SKU keeps the costliest.
            rows = await asyncio.gather(
                *(self.warehouse.costs(skus, plan) for plan in setup.plans.values())
            )
            return pricing.highest_cost(dict(zip(setup.plans, rows, strict=True)))

        group = context.business_group if audience is None else None
        if group:
            nits = list(context.group_nits) or await self.masters.group_nits(group)
        else:
            nits = list(audience.nits) if audience is not None else [context.nit]
        stock_scope = pricing.stock_scope(context.price_list_id, context.cost_centers)
        (
            costs,
            prices,
            taxes,
            agreements,
            scopes,
            names,
            increases,
            stock_rows,
        ) = await asyncio.gather(
            costs_by_center(),
            list_prices(),
            self.warehouse.value_tax_keys(skus),
            self.supports.agreements(codes, skus, nits),
            self.supports.customers(nits),
            self.masters.supplier_names(codes),
            self.masters.pending_increases(skus),
            self.warehouse.stock(skus, stock_scope.warehouses, stock_scope.centers),
        )
        stock = stock_totals(stock_rows)
        if group:
            agreements = _group_agreements(agreements, group)
        # A group has no one customer scope: its margin list comes from the majority channel.
        scope = None if group else scopes.get(context.nit)
        today = self.calendar.today()
        lines: list[QuotationLine] = []
        for entry in entries:
            sku, supplier = entry.sku, supplier_of(entry.sku)
            if audience is not None:
                listed = prices.get(sku)
                base = listed.price if listed is not None else None
                placeholder = base is not None and base <= 1
                line = rules.build_line(
                    entry.position,
                    sku,
                    entry.sale,
                    item=entry.item,
                    nit=context.nit,
                    channel=context.channel,
                    margin_field=_margin_field(context),
                    scope=None,
                    product=products.get(sku),
                    supplier_name=names.get(supplier or ""),
                    cost=costs.get(sku),
                    list_price=listed,
                    tax=taxes.get(sku),
                    agreements=[
                        item
                        for item in agreements
                        if item.supplier_code in (supplier, None)
                        and item.product_sku in (sku, None)
                    ],
                    policy=self.policy,
                    day=today,
                    line_id=entry.line_id,
                    price_family=setup.family,
                    regional=setup.regional,
                    increase=increases.get(sku),
                    list_scopes=scopes,
                    base_price=None if placeholder else base,
                    origin=entry.origin or vocabulary.ADDED,
                )
                if placeholder:
                    line = replace(line, problems=(*line.problems, "precio_marcador"))
                lines.append(line)
                continue
            lines.append(
                rules.build_line(
                    entry.position,
                    sku,
                    entry.sale,
                    item=entry.item,
                    nit=context.nit,
                    channel=context.channel,
                    margin_field=_margin_field(context, scope),
                    scope=scope,
                    product=products.get(sku),
                    supplier_name=names.get(supplier or ""),
                    cost=costs.get(sku),
                    list_price=prices.get(sku),
                    tax=taxes.get(sku),
                    agreements=[
                        item
                        for item in agreements
                        if item.supplier_code in (supplier, None)
                        and item.product_sku in (sku, None)
                    ],
                    policy=self.policy,
                    day=today,
                    line_id=entry.line_id,
                    price_family=setup.family,
                    regional=setup.regional,
                    increase=increases.get(sku),
                )
            )
        # The stock is the consolidated one of the warehouses of the chosen operation centers
        # (every center: Nacional), not the cost's warehouse (2026-11-19).
        lines = [
            replace(
                line,
                stock_on_hand=stock[line.sku].on_hand if line.sku in stock else Decimal(0),
                stock_available=stock[line.sku].available if line.sku in stock else Decimal(0),
            )
            for line in lines
        ]
        snapshot = max(
            (cost.snapshot_date for cost in costs.values() if cost.snapshot_date), default=None
        )
        return lines, snapshot

    async def _build(
        self,
        context: CustomerContext,
        months: int,
        added: Sequence[str] = (),
        reserved: int = 0,
        history: bool = True,
    ) -> tuple[list[QuotationLine], date | None]:
        """Lines of the sales window plus the SKUs the advisor had added.

        ``reserved`` lines (the local products a refresh keeps) count toward the maximum.
        Without ``history`` (a quotation that started empty) the sales are not read: only
        the added SKUs are priced.
        """

        sales: list[LastSale] = []
        if history and context.business_group:
            nits = list(context.group_nits) or await self.masters.group_nits(context.business_group)
            sales = await self.warehouse.group_last_sales(nits, months)
        elif history:
            sales = await self.warehouse.last_sales(context.nit, months)
        sold = {sale.sku for sale in sales}
        extra = [sku for sku in added if sku not in sold]
        total = len(sales) + len(extra) + reserved
        if total > self.max_lines:
            raise InvalidInputError(
                f"El cliente tiene {total} referencias; el máximo es {self.max_lines}"
            )
        items = await self.warehouse.items(extra) if extra else {}
        entries = [
            _Entry(sale.sku, position, uuid4(), sale=sale)
            for position, sale in enumerate(sales, start=1)
        ]
        entries += [
            _Entry(sku, len(entries) + offset, uuid4(), item=items[sku])
            for offset, sku in enumerate((sku for sku in extra if sku in items), start=1)
        ]
        return await self._price(context, entries)

    @staticmethod
    def _stored_context(quotation: Quotation) -> CustomerContext:
        """The customer as the quotation keeps it, to price SKUs without asking the warehouse."""

        return CustomerContext(
            nit=quotation.customer_nit,
            name=quotation.customer_name,
            channel_code=quotation.channel_code,
            channel=quotation.channel,
            price_list_id=quotation.price_list_id,
            price_list_name=quotation.price_list_name,
            price_list_customers=quotation.price_list_customers,
            billing_center_code=quotation.billing_center_code,
            salesperson_code=quotation.salesperson_code,
            salesperson_name=quotation.salesperson_name,
            warnings=quotation.warnings,
            cost_centers=quotation.cost_centers,
            # The group's NITs are read again when it is priced.
            business_group=quotation.business_group,
        )

    @staticmethod
    def _customer_fields(context: CustomerContext) -> dict[str, object]:
        """Header fields that come from the customer and how it is priced."""

        setup = _setup(context)
        general = context.price_list_id == vocabulary.GENERAL_PRICE_LIST
        return {
            "customer_name": context.name,
            "channel_code": context.channel_code,
            "channel": context.channel,
            "price_list_id": context.price_list_id,
            "price_list_name": context.price_list_name,
            "price_list_customers": context.price_list_customers,
            "billing_center_code": context.billing_center_code,
            "salesperson_code": context.salesperson_code,
            "salesperson_name": context.salesperson_name,
            "warnings": context.warnings,
            "cost_centers": context.cost_centers,
            "regional": setup.regional,
            "price_family": setup.family,
            "stock_source": setup.stock_label,
            # List 1 is never updated from a quotation: its customer gets a new list.
            "list_action": vocabulary.CREATE_LIST if general else vocabulary.UPDATE_LIST,
            "business_group": context.business_group,
            "group_customers": len(context.group_nits) if context.business_group else None,
        }

    async def _seller_contact(self, context: CustomerContext) -> SellerContact | None:
        """The customer's salesperson as the offer shows them; ``None`` without a code."""

        if not context.salesperson_code:
            return None
        return await self.warehouse.seller_contact(context.salesperson_code)

    # ------------------------------------------------------------ billing centers

    async def billing_centers(self, principal: Principal) -> list[BillingCenter]:
        """Active operation centers a quotation can be billed and priced from, by code."""

        if not (
            await self.can_read(principal, vocabulary.LIST_UPDATE)
            or await self.can_read(principal, vocabulary.MAIN_LIST)
        ):
            await self._require_reader(principal)
        return sorted(await self.warehouse.billing_centers(), key=lambda item: item.code)

    async def _center(self, code: str | None) -> BillingCenter | None:
        """The active operation center ``code``, or ``None`` without one.

        Raises:
            InvalidInputError: ``code`` is not an active operation center.
        """

        value = (code or "").strip()
        if not value:
            return None
        center = next(
            (item for item in await self.warehouse.billing_centers() if item.code == value), None
        )
        if center is None:
            raise InvalidInputError(f"El centro de operación {value} no existe o está inactivo")
        return center

    async def _center_name(self, code: str | None) -> str | None:
        """Name of a center, for the header; ``None`` when unknown."""

        if not code:
            return None
        try:
            centers = await self.warehouse.billing_centers()
        except Exception as exc:  # noqa: BLE001 - the name is only shown
            logger.warning("quotations: operation center %s not read: %s", code, exc)
            return None
        return next((item.name for item in centers if item.code == code), None)

    async def create(
        self,
        principal: Principal,
        customer_nit: str | None,
        window_months: int,
        validity_days: int = vocabulary.DEFAULT_VALIDITY_DAYS,
        *,
        temporary_customer: TemporaryCustomer | None = None,
        billing_center_code: str | None = None,
        with_history: bool = True,
        business_group: str | None = None,
    ) -> QuotationDetail:
        """Build a quotation of a customer from its last sales in the window, or an empty one
        for a temporary customer. With ``business_group`` (instead of the NIT) it quotes the
        whole group (2026-11-19, :meth:`_group_context`); the offer goes to the group.

        A temporary customer (not in SIESA) needs ``billing_center_code``: it has no sales and
        no list, and its lines are priced from that center. For a SIESA customer the center
        is optional: given, it replaces the one of the branches (``manual``). Without
        ``with_history`` a SIESA customer's quotation starts empty too: the advisor adds the
        references, and a refresh revalidates them without bringing the history in.

        Args:
            principal (Principal): The advisor.
            customer_nit (str | None): NIT (punctuation and a separated check digit are
                accepted); none for a temporary customer.
            window_months (int): 6 or 12.
            validity_days (int): Days the offer holds from today (1 to 365).
            temporary_customer (TemporaryCustomer | None): A customer not in SIESA yet.
            billing_center_code (str | None): Operation center to price from.

        Returns:
            QuotationDetail: The new draft with every SKU priced as it was last sold.
        """

        await self._require_manager(principal)
        if window_months not in vocabulary.WINDOWS:
            raise InvalidInputError("La ventana debe ser de 6 o 12 meses")
        _check_validity(validity_days)
        has_nit = bool((customer_nit or "").strip())
        group = (business_group or "").strip() or None
        given = sum((has_nit, group is not None, temporary_customer is not None))
        if given > 1:
            raise InvalidInputError(
                "Envía el NIT de un cliente, un grupo empresarial o un cliente temporal, uno solo"
            )
        if given == 0:
            raise InvalidInputError(
                "Escribe el NIT del cliente, el grupo empresarial o los datos del cliente temporal"
            )
        if temporary_customer is not None and not (billing_center_code or "").strip():
            raise InvalidInputError("Elige el centro de operación del cliente temporal")
        temporary = _temporary(temporary_customer) if temporary_customer else None
        center = await self._center(billing_center_code)
        if temporary is not None:
            nit = temporary.nit or ""
            context = CustomerContext(
                nit=nit,
                name=temporary.name,
                channel_code=None,
                channel=None,
                price_list_id=None,
                price_list_name=None,
                price_list_customers=None,
                billing_center_code=center.code,  # type: ignore[union-attr]
                salesperson_code=None,
                salesperson_name=None,
                cost_centers=(center.code,),  # type: ignore[union-attr]
            )
            lines, snapshot, seller = [], None, None
        else:
            if group is not None:
                context = await self._group_context(group, await self._scope(principal))
                nit = context.nit
            else:
                nit = normalize_nit(customer_nit or "")
                context = await self._context(nit, await self._scope(principal))
            if center is not None:
                context = replace(
                    context, billing_center_code=center.code, cost_centers=(center.code,)
                )
            lines, snapshot = await self._build(context, window_months, history=with_history)
            seller = await self._seller_contact(context)
            if not lines and with_history:
                # A customer without sales in the window is quoted with the SKUs the advisor
                # adds.
                context = replace(context, warnings=(*context.warnings, "sin_ventas"))
        fields = self._customer_fields(context)
        if temporary is not None:
            fields["list_action"] = vocabulary.CREATE_LIST
        name = center.name if center else await self._center_name(context.billing_center_code)
        now = datetime.now(UTC)
        total, changed, below, below_target = rules.counts(lines)
        quotation = Quotation(
            id=uuid4(),
            customer_nit=nit,
            window_months=window_months,
            status=vocabulary.DRAFT,
            activation_date=None,
            export_price_list_id=None,
            built_at=now,
            cost_cut_date=snapshot,
            max_points_below_target=self.policy.max_points_below_target,
            line_count=total,
            changed_count=changed,
            below_rule_count=below,
            below_target_count=below_target,
            version=1,
            created_by_id=principal.user_id,
            created_by=principal.email,
            created_at=now,
            updated_by=principal.email,
            updated_at=now,
            validity_days=validity_days,
            valid_from=self.calendar.today(),
            seller_contact=seller,
            billing_center_source=vocabulary.CENTER_MANUAL if center else vocabulary.CENTER_AUTO,
            billing_center_name=name,
            temporary_customer=temporary,
            with_history=with_history and temporary is None,
            **fields,  # type: ignore[arg-type]
        )
        return await self.repository.create(quotation, lines)

    # ------------------------------------------------------------ list updates

    async def price_lists(
        self, principal: Principal, q: str | None, kind: str = vocabulary.LIST_UPDATE
    ) -> list[PriceListInfo]:
        """SIESA lists to update, by code or by part of the name (at most 50).

        The router says of each one whether this module may take it (:meth:`list_allowed`):
        the main lists belong to ``principal`` and the rest to ``lista``. ``principal``
        with no text gives every main list.
        """

        if kind not in vocabulary.LIST_KINDS:
            raise InvalidInputError("Tipo de actualización desconocido: lista o principal")
        await self._require_manager(principal, kind)
        text = (q or "").strip()
        if not text:
            if kind != vocabulary.MAIN_LIST:
                return []
            found = [await self.warehouse.price_list(code) for code in self.main_list_order]
            return [info for info in found if info is not None]
        return await self.warehouse.price_lists(text[:80])

    async def stock_breakdown(
        self, principal: Principal, quotation_id: UUID, skus: Sequence[str] | None = None
    ) -> StockBreakdown:
        """The stock of the quotation's (or list update's) references in the warehouses of
        its operation centers (2026-11-19): consolidated per SKU and warehouse by warehouse,
        to show the detail behind each line's total. Without ``skus``, every SIESA line (at
        most :data:`MAX_STOCK_SKUS`).

        Raises:
            InvalidInputError: Too many references.
        """

        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        siesa = [line.sku for line in detail.lines if not line.is_local]
        wanted = list(dict.fromkeys(sku.strip() for sku in skus if sku.strip())) if skus else siesa
        if len(wanted) > MAX_STOCK_SKUS:
            raise InvalidInputError(f"Pide el desglose de hasta {MAX_STOCK_SKUS} referencias")
        scope = pricing.stock_scope(quotation.price_list_id, quotation.cost_centers)
        known = set(siesa)
        asked = tuple(sku for sku in wanted if sku in known)
        rows = await self.warehouse.stock(list(asked), scope.warehouses, scope.centers)
        return StockBreakdown(scope=scope, skus=asked, rows=tuple(rows), totals=stock_totals(rows))

    async def available_list_codes(
        self, principal: Principal, after: str | None, size: int
    ) -> list_codes.FreeCodes:
        """Free codes for a new list (BI, 2026-11-19): the 3-character codes SIESA does not
        have and no validated quotation took, in order, after ``after``.

        Raises:
            ForbiddenError: The caller reads neither the quoter nor the list updates.
            InvalidInputError: A page size out of range.
        """

        if not (
            await self.can_read(principal, vocabulary.QUOTATION)
            or await self.can_read(principal, vocabulary.LIST_UPDATE)
        ):
            raise ForbiddenError("No tienes permiso para consultar las listas de precios")
        if not 1 <= size <= list_codes.MAX_PAGE:
            raise InvalidInputError(f"El tamaño debe estar entre 1 y {list_codes.MAX_PAGE}")
        siesa, reserved = await asyncio.gather(
            self.warehouse.price_list_ids(), self.repository.reserved_list_ids()
        )
        return list_codes.free_codes([*siesa, *reserved], after, size)

    async def _list_branches(self, code: str) -> list[Branch]:
        return await self.warehouse.list_branches(code)

    def _audience(self, branches: Sequence[Branch], customer_nit: str | None = None) -> _Audience:
        """The NITs (supports) and the branch keys (last sale) of a list update.

        Supports count for every active customer of the list. The last sale is the one to
        the customer it started from, or to the list's active branches; a list with more
        than :data:`LIST_SALES_BRANCH_LIMIT` of them reads the whole company's (reading by
        28 thousand branches passed 20 s).
        """

        active = [branch for branch in branches if branch.is_active] or list(branches)
        nits = tuple(sorted({branch.nit for branch in active if branch.nit}))
        if customer_nit:
            keys = tuple(
                branch.customer_key
                for branch in branches
                if branch.nit == customer_nit and branch.customer_key
            )
            return _Audience(nits, keys)
        keys = tuple(branch.customer_key for branch in active if branch.customer_key)
        if len(keys) > LIST_SALES_BRANCH_LIMIT:
            return _Audience(nits, None)
        return _Audience(nits, keys)

    async def _list_sales(
        self, skus: Sequence[str], audience: _Audience
    ) -> tuple[dict[str, LastSale], bool]:
        """The last sale of each SKU to the audience, and whether it could be read.

        The warehouse can take long on this read; the list update is still built without
        it (``ultima_venta_no_leida``) and a refresh tries again.
        """

        if not skus or (audience.sale_keys is not None and not audience.sale_keys):
            return {}, True
        try:
            sales = await self.warehouse.list_last_sales(
                skus,
                vocabulary.LIST_UPDATE_WINDOW,
                audience.sale_keys,
                timeout=self.list_sales_timeout,
            )
        except DependencyUnavailableError as exc:
            logger.warning("quotations: last sales of a list not read: %s", exc)
            return {}, False
        return sales, True

    async def _build_list(
        self,
        context: CustomerContext,
        audience: _Audience,
        added: Sequence[_Entry] = (),
        previous: Mapping[str, LastSale] | None = None,
        items: Sequence[ListItem] | None = None,
    ) -> tuple[list[QuotationLine], date | None, bool]:
        """Every SKU with a price in force on the list, then the SKUs the person had added.

        ``previous`` are the last sales the lines had, kept when the warehouse does not
        answer with the new ones. ``items`` are the list's prices when already read (read
        alongside its branches).

        Returns:
            tuple: The lines, the cost snapshot and whether the last sales were read.
        """

        code = context.price_list_id or ""
        if items is None:
            items = await self.warehouse.list_items(code)
        listed = {item.item.sku for item in items}
        extra = [entry for entry in added if entry.sku not in listed]
        total = len(items) + len(extra)
        if total > self.list_max_lines:
            raise InvalidInputError(
                f"La lista {code} tiene {total} referencias; el máximo es {self.list_max_lines}"
            )
        skus = [item.item.sku for item in items] + [entry.sku for entry in extra]
        sales, read = await self._list_sales(skus, audience)
        if not read and previous:
            sales = dict(previous)
        entries = [
            _Entry(
                item.item.sku,
                position,
                uuid4(),
                sale=sales.get(item.item.sku),
                item=item.item,
                origin=vocabulary.FROM_LIST,
            )
            for position, item in enumerate(items, start=1)
        ]
        entries += [
            replace(
                entry,
                position=len(items) + offset,
                sale=sales.get(entry.sku, entry.sale),
                origin=vocabulary.ADDED,
            )
            for offset, entry in enumerate(extra, start=1)
        ]
        lines, snapshot = await self._price(context, entries, audience)
        return lines, snapshot, read

    async def _resolve_list(
        self, customer_nit: str | None, price_list_id: str | None
    ) -> tuple[PriceListInfo, str, str | None]:
        """The list a list update starts from, its source and the customer's NIT if any.

        Raises:
            NotFoundError: No such customer or list.
            InvalidInputError: Both or neither were given, or the customer has no list.
        """

        has_nit = bool((customer_nit or "").strip())
        has_list = bool((price_list_id or "").strip())
        if has_nit == has_list:
            raise InvalidInputError("Elige un cliente o una lista de precios")
        if has_nit:
            nit = normalize_nit(customer_nit or "")
            branches = await self.warehouse.branches(nit)
            if not branches:
                raise NotFoundError(f"No se encontró el cliente con NIT {nit}")
            draft = rules.customer_context(nit, branches)
            if "sin_lista" in draft.warnings or not draft.price_list_id:
                raise InvalidInputError(f"El cliente {nit} no tiene lista de precios asignada")
            info = await self.warehouse.price_list(draft.price_list_id)
            if info is None:
                raise NotFoundError(f"La lista {draft.price_list_id} del cliente no está en SIESA")
            return info, vocabulary.SOURCE_CUSTOMER, nit
        info = await self.warehouse.price_list(price_list_id or "")
        if info is None:
            raise NotFoundError(f"No existe la lista de precios {price_list_id}")
        return info, vocabulary.SOURCE_LIST, None

    async def create_list_update(
        self,
        principal: Principal,
        *,
        customer_nit: str | None = None,
        price_list_id: str | None = None,
        kind: str = vocabulary.LIST_UPDATE,
    ) -> QuotationDetail:
        """Start the update of an existing SIESA price list, chosen by a customer (its
        assigned list), by code or by name (the code of the list picked in the search).

        Every SKU with a price in force on the list becomes a line compared against that
        price, with the same cost, target and rule as a quotation, the last sale of the SKU
        to the customer (or to the list's customers) and every support of the list's
        customers. Its items are reviewed one by one and loaded from the list's staging. The
        main lists are updated only from ``principal`` (module ``listas-principales``).

        Raises:
            ForbiddenError: The caller does not update lists of this kind, or the list is a
                main list asked from ``lista``.
            InvalidInputError: A list that is not a main list asked from ``principal``.
        """

        if kind not in vocabulary.LIST_KINDS:
            raise InvalidInputError("Tipo de actualización desconocido: lista o principal")
        await self._require_manager(principal, kind)
        info, source, nit = await self._resolve_list(customer_nit, price_list_id)
        self._check_list_kind(kind, info.code)
        # Its branches and its prices do not depend on each other: one wait instead of two.
        branches, items = await asyncio.gather(
            self._list_branches(info.code), self.warehouse.list_items(info.code)
        )
        customer_name = None
        if nit:
            customer_name = next(
                (
                    branch.legal_name
                    for branch in branches
                    if branch.nit == nit and branch.legal_name
                ),
                None,
            )
            if customer_name is None:
                own = await self.warehouse.branches(nit)
                customer_name = next((item.legal_name for item in own if item.legal_name), None)
        context = rules.list_context(
            info.code, info.name, branches, nit=nit or "", customer_name=customer_name
        )
        audience = self._audience(branches, nit)
        lines, snapshot, read = await self._build_list(context, audience, items=items)
        warnings = list(context.warnings)
        if not lines:
            warnings.append("lista_sin_precios")
        if not read:
            warnings.append(vocabulary.SALES_NOT_READ)
        context = replace(context, warnings=tuple(warnings))
        fields = self._customer_fields(context)
        fields["list_action"] = vocabulary.UPDATE_LIST
        now = datetime.now(UTC)
        total, changed, below, below_target = rules.counts(lines)
        quotation = Quotation(
            id=uuid4(),
            customer_nit=nit or "",
            window_months=vocabulary.LIST_UPDATE_WINDOW,
            status=vocabulary.DRAFT,
            activation_date=None,
            export_price_list_id=None,
            built_at=now,
            cost_cut_date=snapshot,
            max_points_below_target=self.policy.max_points_below_target,
            line_count=total,
            changed_count=changed,
            below_rule_count=below,
            below_target_count=below_target,
            version=1,
            created_by_id=principal.user_id,
            created_by=principal.email,
            created_at=now,
            updated_by=principal.email,
            updated_at=now,
            valid_from=self.calendar.today(),
            billing_center_source=vocabulary.CENTER_AUTO,
            billing_center_name=await self._center_name(context.billing_center_code),
            kind=kind,
            source=source,
            **fields,  # type: ignore[arg-type]
        )
        created = await self.repository.create(quotation, lines)
        if not read:
            self._retry_sales(created, audience)
        return created

    async def _refresh_list(
        self, principal: Principal, detail: QuotationDetail, version: int
    ) -> QuotationDetail:
        """Rebuild a list update from the list as SIESA has it now, keeping the new prices
        by SKU and the added SKUs; a center chosen by hand stays."""

        quotation = detail.quotation
        code = quotation.price_list_id or ""
        branches, items = await asyncio.gather(
            self._list_branches(code), self.warehouse.list_items(code)
        )
        nit = quotation.customer_nit or None
        context = rules.list_context(
            code,
            quotation.price_list_name,
            branches,
            nit=quotation.customer_nit,
            customer_name=quotation.customer_name if nit else None,
        )
        manual = quotation.billing_center_source == vocabulary.CENTER_MANUAL
        if manual:
            context = replace(
                context,
                billing_center_code=quotation.billing_center_code,
                cost_centers=quotation.cost_centers,
            )
        added = [_list_entry_of(line) for line in detail.lines if line.origin == vocabulary.ADDED]
        # If the warehouse does not answer with the last sales, each line keeps the one it had.
        previous = {
            entry.sku: entry.sale
            for entry in map(_list_entry_of, detail.lines)
            if entry.sale is not None
        }
        audience = self._audience(branches, nit)
        lines, snapshot, read = await self._build_list(context, audience, added, previous, items)
        warnings = list(context.warnings)
        if not lines:
            warnings.append("lista_sin_precios")
        if not read:
            warnings.append(vocabulary.SALES_NOT_READ)
        context = replace(context, warnings=tuple(warnings))
        stored = {line.sku: line for line in detail.lines}
        lines = [_kept(line, stored.get(line.sku)) for line in lines]
        fields = self._customer_fields(context)
        fields["list_action"] = vocabulary.UPDATE_LIST
        if not manual:
            fields["billing_center_name"] = await self._center_name(context.billing_center_code)
        refreshed = replace(
            self._touched(quotation, lines, principal.email),
            built_at=datetime.now(UTC),
            cost_cut_date=snapshot,
            max_points_below_target=self.policy.max_points_below_target,
            **fields,  # type: ignore[arg-type]
        )
        saved = await self.repository.save(refreshed, lines, version, replace_lines=True)
        if not read:
            self._retry_sales(saved, audience)
        return saved

    def _retry_sales(self, detail: QuotationDetail, audience: _Audience) -> None:
        """Read the last sales of a list update built without them again, in the background
        (:mod:`sales_backfill`): the warning goes away on its own when the warehouse answers."""

        if self.sales_backfill is None:
            return
        skus = list(dict.fromkeys(line.sku for line in detail.lines if not line.is_local))
        self.sales_backfill.start(detail.quotation.id, skus, audience.sale_keys)

    def retrying_sales(self, quotation_id: UUID) -> bool:
        """Whether the last sales of this list update are being read again right now."""

        return self.sales_backfill is not None and self.sales_backfill.running(quotation_id)

    async def _stored_audience(self, quotation: Quotation) -> _Audience | None:
        """The audience of a stored list update (``None`` for a quotation)."""

        if not quotation.is_list_update:
            return None
        branches = await self._list_branches(quotation.price_list_id or "")
        return self._audience(branches, quotation.customer_nit or None)

    # ------------------------------------------------------------ read

    async def get(
        self, principal: Principal, quotation_id: UUID, skus: Sequence[str] | None = None
    ) -> QuotationDetail:
        """A quotation or list update; the permission is the one of its module.

        With ``skus``, only those lines come: a few rows of a large list update.
        """

        detail = await (
            self.repository.get(quotation_id)
            if skus is None
            else self.repository.get_lines(quotation_id, skus)
        )
        if detail is None:
            raise NotFoundError("Cotización no encontrada")
        await self._require_reader(principal, detail.quotation.kind)
        await self._check_visible(principal, detail.quotation)
        return await self._with_categories(detail)

    async def _with_categories(self, detail: QuotationDetail) -> QuotationDetail:
        """Fill the category of SIESA lines that never had it looked up (stored before
        ``0050``, or built while the product had no row), in one warehouse read.

        The categories are stored on the lines without touching the header (no new version,
        no status change): it is not an edit. A SKU without a product row or a category is
        stored as :data:`vocabulary.NO_CATEGORY`, so it is not looked up again. When the
        warehouse fails the detail comes back as it was: reading never fails for this.
        """

        missing = [line for line in detail.lines if not line.is_local and line.category is None]
        if not missing:
            return detail
        try:
            items = await self.warehouse.items(sorted({line.sku for line in missing}))
        except Exception as exc:  # noqa: BLE001 - the category is a convenience
            logger.warning("quotations: categories of %s not read: %s", detail.quotation.id, exc)
            return detail
        found = {
            line.id: (items[line.sku].category if line.sku in items else None)
            or vocabulary.NO_CATEGORY
            for line in missing
        }
        try:
            await self.repository.set_categories(detail.quotation.id, found)
        except Exception as exc:  # noqa: BLE001 - shown now, stored on a later read
            logger.warning("quotations: categories of %s not stored: %s", detail.quotation.id, exc)
        lines = tuple(
            replace(line, category=found[line.id]) if line.id in found else line
            for line in detail.lines
        )
        return replace(detail, lines=lines)

    async def _editable(
        self,
        principal: Principal,
        quotation_id: UUID,
        *,
        any_status: bool = False,
        skus: Sequence[str] | None = None,
    ) -> QuotationDetail:
        """The quotation, if the caller may edit it and it is not awaiting validation.

        BI (and admins) has the whole module: it edits any quotation. Anyone else, only theirs.
        ``any_status`` lets through a pending one too: for changes that never touch prices
        (offer settings, quantities). A closed one (validated or loaded) never changes: no
        edit, no refresh (2026-11-19). With ``skus``, only those lines come.
        """

        detail = await self.get(principal, quotation_id, skus)
        await self._require_manager(principal, detail.quotation.kind)
        await self._require_owner_or_validator(principal, detail.quotation, "editarla")
        await self._check_restricted(principal, detail.quotation)
        if detail.quotation.status in vocabulary.CLOSED:
            noun = self._noun(detail.quotation.kind)
            raise ConflictError(
                f"{noun[0].upper()}{noun[1:]} ya está cerrada "
                f"({vocabulary.STATUS_LABELS[detail.quotation.status].lower()}): sus datos no "
                "cambian; crea una nueva para cotizar de nuevo"
            )
        if not any_status and detail.quotation.status == vocabulary.PENDING:
            noun = self._noun(detail.quotation.kind)
            raise ConflictError(
                f"{noun[0].upper()}{noun[1:]} está pendiente de validación; devuélvela a "
                "borrador para editarla"
            )
        return detail

    async def editable(
        self,
        principal: Principal,
        quotation_id: UUID,
        *,
        any_status: bool = False,
        skus: Sequence[str] | None = None,
    ) -> QuotationDetail:
        """:meth:`_editable` for the services built on this one (review, staging)."""

        return await self._editable(principal, quotation_id, any_status=any_status, skus=skus)

    async def audience_of(self, quotation: Quotation) -> _Audience | None:
        """Whose sales count for a stored list update (``None`` for a quotation)."""

        return await self._stored_audience(quotation)

    @staticmethod
    def check_version(quotation: Quotation, version: int) -> None:
        QuotationService._check_version(quotation, version)

    @staticmethod
    def bumped(quotation: Quotation, actor: str) -> Quotation:
        return QuotationService._bumped(quotation, actor)

    @staticmethod
    def _no_offer(quotation: Quotation) -> None:
        """A list update goes to SIESA only: it has no customer offer, quantities or local
        products."""

        if quotation.is_list_update:
            raise InvalidInputError(
                "Una actualización de lista no tiene oferta al cliente, cantidades ni productos "
                "solo para cotización"
            )

    async def list(
        self,
        principal: Principal,
        *,
        mine: bool,
        status: str | None,
        customer_nit: str | None,
        q: str | None,
        page: int,
        size: int,
        kind: str = vocabulary.QUOTATION,
    ) -> QuotationPage:
        await self._require_reader(principal, kind)
        _page(page, size)
        if status is not None and status not in vocabulary.STATUSES:
            raise InvalidInputError("Estado desconocido")
        own_only = mine or not await self._sees_all(principal, kind)
        return await self.repository.list(
            created_by_id=principal.user_id if own_only else None,
            status=status,
            customer_nit=normalize_nit(customer_nit) if customer_nit else None,
            q=q.strip() if q and q.strip() else None,
            page=page,
            size=size,
            kind=kind,
        )

    # ------------------------------------------------------------ change

    def _touched(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        actor: str,
        counts: tuple[int, int, int, int] | None = None,
    ) -> Quotation:
        """The header after an edit: back to draft, counts again (the given ``counts`` when
        the lines are not all at hand), review stamps cleared."""

        total, changed, below, below_target = counts or rules.counts(lines)
        return replace(
            quotation,
            status=vocabulary.DRAFT,
            line_count=total,
            changed_count=changed,
            below_rule_count=below,
            below_target_count=below_target,
            version=quotation.version + 1,
            updated_by=actor,
            updated_at=datetime.now(UTC),
            confirmed_at=None,
            confirmed_by=None,
            validated_at=None,
            validated_by=None,
            uploaded_at=None,
            uploaded_by=None,
        )

    @staticmethod
    def _check_version(quotation: Quotation, version: int) -> None:
        if quotation.version != version:
            raise ConflictError("La cotización cambió desde que la abriste; recárgala")

    @staticmethod
    def _apply(
        line: QuotationLine, change: LineChange, family: str
    ) -> tuple[QuotationLine | None, Skipped | None]:
        """The line with the change applied, or why it could not be priced."""

        if change.set_quantity:
            if change.margin_pct is not None:
                raise InvalidInputError(
                    f"La referencia {line.sku} lleva margen y cantidad: envíalos por separado"
                )
            line = replace(line, quantity=_quantity(change.quantity, line.sku))
            if not change.set_price:
                return line, None
        if change.margin_pct is not None:
            margin = change.margin_pct
            if line.is_local:
                return None, Skipped(line.sku, _LOCAL_NO_MARGIN)
            if not margin.is_finite() or not Decimal(0) <= margin < 1:
                return None, Skipped(line.sku, "El margen va de 0 a menos de 100 %")
            if line.cost is None:
                return None, Skipped(line.sku, "Sin costo: no se puede calcular el precio")
            price = rules.price_for_margin(line, margin, family)
            if price is None:
                return None, Skipped(line.sku, "El margen no da un precio mayor que cero")
            return rules.reprice(line, price), None
        return rules.reprice(line, _price(change.new_price, line.sku)), None

    async def update_lines(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        changes: Sequence[LineChange],
    ) -> tuple[QuotationDetail, tuple[Skipped, ...]]:
        """Set or clear the advisor's prices, or price SKUs at a margin, and their quantities.

        A price below the rule is kept and marked. A change with ``margin_pct`` prices the
        SKU at that margin (with its support), rounded as the list family does: that is how
        a selection of SKUs gets one standard margin. SKUs that cannot be priced at a margin
        (no cost) are skipped and reported; the rest is saved.

        A batch that only carries quantities never touches prices: it is accepted in every
        status (pending validation too) and keeps the status, though it still checks and
        bumps the version.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read (409 when it changed).
            changes (Sequence[LineChange]): Prices, margins or quantities by SKU.

        Returns:
            tuple[QuotationDetail, tuple[Skipped, ...]]: The quotation, back to draft (unless
            only quantities changed), and the SKUs that were not priced.
        """

        only_quantities = bool(changes) and not any(change.set_price for change in changes)
        detail = await self._editable(principal, quotation_id, any_status=only_quantities)
        if any(change.set_quantity for change in changes):
            self._no_offer(detail.quotation)
        self._check_version(detail.quotation, version)
        if not changes:
            raise InvalidInputError("No hay precios para guardar")
        if only_quantities:
            return await self._set_quantities(principal, detail, version, changes), ()
        by_sku = {line.sku: line for line in detail.lines}
        updated: dict[str, QuotationLine] = {}
        skipped: list[Skipped] = []
        for change in changes:
            line = by_sku.get(change.sku)
            if line is None:
                raise InvalidInputError(f"La referencia {change.sku} no está en la cotización")
            priced, reason = self._apply(line, change, detail.quotation.price_family)
            if priced is None:
                skipped.append(reason)  # type: ignore[arg-type]
            else:
                if priced.new_price != line.new_price:
                    priced = _unreviewed(priced)
                updated[change.sku] = priced
        if not updated:
            raise InvalidInputError(
                "Ninguna referencia se pudo actualizar: "
                + "; ".join(f"{item.sku} ({item.reason})" for item in skipped[:10])
            )
        lines = [updated.get(line.sku, line) for line in detail.lines]
        quotation = self._touched(detail.quotation, lines, principal.email)
        saved = await self.repository.save(quotation, list(updated.values()), version)
        return saved, tuple(skipped)

    async def price_rows(
        self,
        principal: Principal,
        quotation_id: UUID,
        changes: Sequence[LineChange],
        *,
        ready: bool = False,
    ) -> tuple[QuotationDetail, tuple[Skipped, ...]]:
        """New prices on a few rows of a large list update (the board of the main lists).

        Unlike :meth:`update_lines`, only the header and the rows of ``changes`` are read and
        written, never the whole update (list 1 has thousands of rows): the header's counts
        move by what those rows changed. It writes at the version it read; the caller tries
        again on a conflict.

        With ``ready`` a row whose price changed goes to ``por_validar``, by whoever priced it,
        when it can (active and with a unit); without a price it is ``sin_marcar``.

        Returns:
            tuple[QuotationDetail, tuple[Skipped, ...]]: The header and those rows as stored
            (not the whole update), and the rows that were not priced.

        Raises:
            InvalidInputError: Not a list update, an unknown SKU or nothing could be priced.
            ConflictError: The update changed while it was written.
        """

        if not changes or any(change.set_quantity or not change.set_price for change in changes):
            raise InvalidInputError("Solo se cambian precios")
        detail = await self._editable(
            principal, quotation_id, skus=list(dict.fromkeys(change.sku for change in changes))
        )
        quotation = detail.quotation
        if not quotation.is_list_update:
            raise InvalidInputError("Solo una actualización de lista se edita por filas")
        by_sku = {line.sku: line for line in detail.lines}
        now = datetime.now(UTC)
        updated: dict[str, QuotationLine] = {}
        skipped: list[Skipped] = []
        for change in changes:
            line = by_sku.get(change.sku)
            if line is None:
                raise InvalidInputError(f"La referencia {change.sku} no está en la lista")
            priced, reason = self._apply(line, change, quotation.price_family)
            if priced is None:
                skipped.append(reason)  # type: ignore[arg-type]
                continue
            if priced.new_price != line.new_price:
                priced = _unreviewed(priced)
                if ready and priced.new_price is not None and priced.is_active and priced.unit:
                    priced = replace(
                        priced,
                        review_status=vocabulary.TO_VALIDATE,
                        reviewed_by=principal.email,
                        reviewed_at=now,
                    )
            updated[change.sku] = priced
        if not updated:
            raise InvalidInputError(
                "Ninguna referencia se pudo actualizar: "
                + "; ".join(f"{item.sku} ({item.reason})" for item in skipped[:10])
            )
        _, changed_before, blocked_before, _ = rules.counts([by_sku[sku] for sku in updated])
        _, changed_after, blocked_after, _ = rules.counts(list(updated.values()))
        header = self._touched(
            quotation,
            (),
            principal.email,
            counts=(
                quotation.line_count,
                quotation.changed_count + changed_after - changed_before,
                quotation.below_rule_count + blocked_after - blocked_before,
                quotation.below_target_count,
            ),
        )
        await self.repository.write(header, list(updated.values()), quotation.version)
        lines = tuple(updated.get(line.sku, line) for line in detail.lines)
        return QuotationDetail(header, lines), tuple(skipped)

    async def _set_quantities(
        self,
        principal: Principal,
        detail: QuotationDetail,
        version: int,
        changes: Sequence[LineChange],
    ) -> QuotationDetail:
        """Store quantities only: the status, the counts and the review stamps stay."""

        by_sku = {line.sku: line for line in detail.lines}
        updated: dict[str, QuotationLine] = {}
        for change in changes:
            line = updated.get(change.sku) or by_sku.get(change.sku)
            if line is None:
                raise InvalidInputError(f"La referencia {change.sku} no está en la cotización")
            updated[change.sku] = replace(line, quantity=_quantity(change.quantity, line.sku))
        return await self.repository.save(
            self._bumped(detail.quotation, principal.email), list(updated.values()), version
        )

    @staticmethod
    def _bumped(quotation: Quotation, actor: str) -> Quotation:
        """The header after a change that keeps its status: only a new version and stamp."""

        return replace(
            quotation,
            version=quotation.version + 1,
            updated_by=actor,
            updated_at=datetime.now(UTC),
        )

    async def refresh(
        self, principal: Principal, quotation_id: UUID, version: int
    ) -> QuotationDetail:
        """Rebuild from the warehouse and the masters, keeping the advisor's prices by SKU.

        Local products do not come from the warehouse: they are kept as they are, after the
        rebuilt lines. A center the advisor chose (``manual``) stays. A temporary customer
        has no sales to read: its lines are priced again as they are. One that started empty
        (``with_history`` false) revalidates its added SKUs and reads no sales.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        if detail.quotation.is_list_update:
            return await self._refresh_list(principal, detail, version)
        if detail.quotation.is_temporary_customer:
            return await self._repriced(principal, detail, version, detail.quotation.cost_centers)
        context = (
            await self._group_context(detail.quotation.business_group)
            if detail.quotation.business_group
            else await self._context(detail.quotation.customer_nit)
        )
        manual = detail.quotation.billing_center_source == vocabulary.CENTER_MANUAL
        if manual:
            context = replace(
                context,
                billing_center_code=detail.quotation.billing_center_code,
                cost_centers=detail.quotation.cost_centers,
            )
        added = [line.sku for line in detail.lines if line.origin == vocabulary.ADDED]
        local = [line for line in detail.lines if line.is_local]
        history = detail.quotation.with_history
        lines, snapshot = await self._build(
            context, detail.quotation.window_months, added, reserved=len(local), history=history
        )
        if not lines and not local and history:
            context = replace(context, warnings=(*context.warnings, "sin_ventas"))
        stored = {line.sku: line for line in detail.lines}
        lines = [_kept(line, stored.get(line.sku)) for line in lines]
        lines += [
            replace(line, position=len(lines) + offset)
            for offset, line in enumerate(local, start=1)
        ]
        fields = self._customer_fields(context)
        fields["seller_contact"] = await self._seller_contact(context)
        if not manual:
            fields["billing_center_name"] = await self._center_name(context.billing_center_code)
        if (
            detail.quotation.price_list_id == context.price_list_id
            and context.price_list_id != vocabulary.GENERAL_PRICE_LIST
        ):
            # The advisor's choice stands while the customer keeps its list (never list 1).
            fields.pop("list_action")
        quotation = replace(
            self._touched(detail.quotation, lines, principal.email),
            built_at=datetime.now(UTC),
            cost_cut_date=snapshot,
            max_points_below_target=self.policy.max_points_below_target,
            **fields,  # type: ignore[arg-type]
        )
        return await self.repository.save(quotation, lines, version, replace_lines=True)

    async def _repriced(
        self,
        principal: Principal,
        detail: QuotationDetail,
        version: int,
        centers: tuple[str, ...],
        source: str | None = None,
    ) -> QuotationDetail:
        """Price every SIESA line again as it is stored, costed from ``centers`` (none is
        *Nacional*).

        Cost (the stock plans of the centers), liquor tax (their regional), target, minimum,
        optimal price, margins and compliance are recomputed; new prices, quantities and
        local products stay. The quotation goes back to draft.
        """

        quotation = detail.quotation
        context = replace(self._stored_context(quotation), cost_centers=centers)
        siesa = [line for line in detail.lines if not line.is_local]
        for_list = quotation.is_list_update
        priced, snapshot = await self._price(
            context,
            [_entry_of(line, for_list) for line in siesa],
            await self._stored_audience(quotation),
        )
        stored = {line.id: line for line in siesa}
        by_id = {line.id: _kept(line, stored.get(line.id)) for line in priced}
        lines = [by_id.get(line.id, line) for line in detail.lines]
        setup = _setup(context)
        values: dict[str, object] = {
            "cost_centers": centers,
            "regional": setup.regional,
            "price_family": setup.family,
            "stock_source": setup.stock_label,
            "built_at": datetime.now(UTC),
            "max_points_below_target": self.policy.max_points_below_target,
        }
        if snapshot is not None:
            values["cost_cut_date"] = snapshot
        if source is not None:
            values["billing_center_source"] = source
        changed = replace(
            self._touched(quotation, lines, principal.email),
            **values,  # type: ignore[arg-type]
        )
        return await self.repository.save(changed, list(by_id.values()), version)

    async def set_billing_center(
        self, principal: Principal, quotation_id: UUID, version: int, codes: Sequence[str]
    ) -> QuotationDetail:
        """Cost the quotation from other operation centers (``manual``); none is *Nacional*.

        *Nacional* takes, SKU by SKU, the highest cost among every warehouse. One center
        takes its warehouses; several take, SKU by SKU, the costliest of them, and each line
        says which one it was. Same state and permission rules as :meth:`refresh`; the
        quotation goes back to draft. Lines are priced again (stock plans and regional),
        keeping new prices, quantities and local products. A refresh keeps the choice.

        Raises:
            InvalidInputError: A code is not an active operation center.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        return await self._repriced(
            principal, detail, version, await self._centers(codes), vocabulary.CENTER_MANUAL
        )

    async def compare_centers(
        self, principal: Principal, quotation_id: UUID, codes: Sequence[str]
    ) -> str | None:
        """Which of two or more centers costs the most for the quotation's SIESA references,
        before choosing them (see :func:`pricing.costliest_center`).

        Anyone who reads the quotation may ask: only the center comes back, no amount.
        ``None`` when they cost the same, or the list has its own warehouses (the centers
        do not change the cost).

        Raises:
            InvalidInputError: Fewer than two centers, or one that is not active.
        """

        detail = await self.get(principal, quotation_id)
        centers = await self._centers(codes)
        if len(centers) < 2:
            raise InvalidInputError("Elige al menos dos centros de operación para compararlos")
        price_list_id = detail.quotation.price_list_id
        skus = sorted({line.sku for line in detail.lines if not line.is_local})
        if not skus or not pricing.centers_set_cost(price_list_id):
            return None
        plans = pricing.stock_plans(price_list_id, centers)
        rows = await asyncio.gather(*(self.warehouse.costs(skus, plan) for plan in plans.values()))
        return pricing.costliest_center(dict(zip(plans, rows, strict=True)))

    async def _centers(self, codes: Sequence[str]) -> tuple[str, ...]:
        """Distinct active operation centers, by code; none is *Nacional*.

        Raises:
            InvalidInputError: A code is not an active operation center.
        """

        wanted = sorted({code.strip() for code in codes if code.strip()})
        if not wanted:
            return ()
        active = {item.code for item in await self.warehouse.billing_centers()}
        unknown = [code for code in wanted if code not in active]
        if unknown:
            raise InvalidInputError(
                f"El centro de operación {', '.join(unknown)} no existe o está inactivo"
            )
        return tuple(wanted)

    async def _new_skus(
        self, detail: QuotationDetail, skus: Sequence[str]
    ) -> tuple[list[_Entry], list[str], list[str]]:
        """Entries for SKUs not in the quotation yet, and the unknown and inactive ones."""

        items = await self.warehouse.items(skus) if skus else {}
        not_found = [sku for sku in skus if sku not in items]
        inactive = [sku for sku in skus if sku in items and not items[sku].is_active]
        valid = [sku for sku in skus if sku in items and items[sku].is_active]
        quotation = detail.quotation
        most = self.list_max_lines if quotation.is_list_update else self.max_lines
        if len(detail.lines) + len(valid) > most:
            raise InvalidInputError(
                f"{self._noun(quotation.kind).capitalize()} admite máximo {most} referencias"
            )
        sales: dict[str, LastSale] = {}
        if quotation.is_list_update and valid:
            # A SKU added to a list update still shows its last sale to the list's customers.
            audience = await self._stored_audience(quotation)
            if audience is not None:
                sales, _ = await self._list_sales(valid, audience)
        start = max((line.position for line in detail.lines), default=0) + 1
        origin = vocabulary.ADDED if quotation.is_list_update else None
        entries = [
            _Entry(
                sku, start + offset, uuid4(), sale=sales.get(sku), item=items[sku], origin=origin
            )
            for offset, sku in enumerate(valid)
        ]
        return entries, not_found, inactive

    async def add_lines(
        self, principal: Principal, quotation_id: UUID, version: int, values: Sequence[str]
    ) -> tuple[QuotationDetail, ImportResult]:
        """Add or revalidate the SKUs of a pasted list or a multiple selection.

        ``values`` may be SKUs or whole pasted text (an Excel column, or SKUs separated by
        commas, semicolons, tabs or spaces). SKUs already in the quotation are revalidated:
        cost, target, supports and liquor tax as they are now, keeping their new price. New
        ones are priced and added at the end (``agregado``). Unknown and inactive SKUs are
        reported and skipped; the rest still goes in.

        Returns:
            tuple[QuotationDetail, ImportResult]: The quotation (back to draft) and what was
            done with each SKU.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        wanted, repeated = parse_skus(values)
        if not wanted:
            raise InvalidInputError("Pega o escribe las referencias a agregar")
        if len(wanted) > MAX_PASTED_SKUS:
            raise InvalidInputError(f"Se admiten máximo {MAX_PASTED_SKUS} referencias por vez")
        # A local product (``LOC-<n>``) is not in SIESA: it is never looked up nor repriced.
        local = [sku for sku in wanted if rules.is_local_sku(sku)]
        wanted = [sku for sku in wanted if not rules.is_local_sku(sku)]
        present = {line.sku: line for line in detail.lines if not line.is_local}
        existing = [sku for sku in wanted if sku in present]
        new = [sku for sku in wanted if sku not in present]
        fresh, not_found, inactive = await self._new_skus(detail, new)
        not_found = local + not_found
        if not existing and not fresh:
            raise InvalidInputError(
                "Ninguna referencia se pudo agregar"
                + (f"; no existen: {', '.join(not_found[:10])}" if not_found else "")
                + (f"; inactivas en SIESA: {', '.join(inactive[:10])}" if inactive else "")
            )
        for_list = detail.quotation.is_list_update
        entries = [_entry_of(present[sku], for_list) for sku in existing] + fresh
        priced, _ = await self._price(
            self._stored_context(detail.quotation),
            entries,
            await self._stored_audience(detail.quotation),
        )
        by_sku = {line.sku: _kept(line, present.get(line.sku)) for line in priced}
        valid = [entry.sku for entry in fresh]
        lines = [by_sku.get(line.sku, line) for line in detail.lines]
        lines += [by_sku[sku] for sku in valid]
        quotation = self._touched(detail.quotation, lines, principal.email)
        saved = await self.repository.save(
            quotation,
            [by_sku[sku] for sku in existing],
            version,
            inserted=[by_sku[sku] for sku in valid],
        )
        return saved, ImportResult(
            added=tuple(valid),
            revalidated=tuple(existing),
            not_found=tuple(not_found),
            inactive=tuple(inactive),
            repeated=repeated,
        )

    async def add_local_line(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        *,
        description: str | None,
        supplier_name: str | None,
        price: Decimal | str | None,
        vat_pct: Decimal | str | None,
    ) -> QuotationDetail:
        """Quote a product SIESA does not have (``origin: local``).

        It gets the next ``LOC-<n>`` of the quotation and goes at the end. It has no cost, so
        the rule does not judge it: it is not counted as changed or below the rule, it does
        not block the confirmation, and it never reaches the workbook or the SIESA files. It
        lives only in this quotation.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read (409 when it changed).
            description (str | None): What the product is (1 to 120 characters).
            supplier_name (str | None): Who supplies it, optional (up to 120).
            price (Decimal | str | None): Price before VAT, whole pesos above zero.
            vat_pct (Decimal | str | None): VAT in points: 19, 5 or 0.

        Returns:
            QuotationDetail: The quotation, back to draft, with the new line.

        Raises:
            InvalidInputError: A field is missing or out of range, or the quotation is full.
        """

        detail = await self._editable(principal, quotation_id)
        self._no_offer(detail.quotation)
        self._check_version(detail.quotation, version)
        amount = _decimal_input(price, "El precio debe ser un número en pesos")
        text = " ".join((description or "").split())
        if not text:
            raise InvalidInputError("Escribe la descripción del producto")
        if len(text) > LOCAL_TEXT_LENGTH:
            raise InvalidInputError(f"La descripción admite máximo {LOCAL_TEXT_LENGTH} caracteres")
        supplier = " ".join((supplier_name or "").split()) or None
        if supplier is not None and len(supplier) > LOCAL_TEXT_LENGTH:
            raise InvalidInputError(f"El proveedor admite máximo {LOCAL_TEXT_LENGTH} caracteres")
        vat = _decimal_input(vat_pct, "El IVA es 19, 5 o 0 %")
        if vat is None or vat not in vocabulary.LOCAL_VAT_POINTS:
            raise InvalidInputError("El IVA es 19, 5 o 0 %")
        if amount is None:
            raise InvalidInputError("Escribe el precio del producto, antes de IVA")
        amount = _price(amount, text)
        if len(detail.lines) + 1 > self.max_lines:
            raise InvalidInputError(f"La cotización admite máximo {self.max_lines} referencias")
        line = rules.local_line(
            max((item.position for item in detail.lines), default=0) + 1,
            rules.local_sku(detail.lines),
            description=text,
            supplier_name=supplier,
            vat_pct=Decimal(int(vat)) if vat == vat.to_integral_value() else vat,
            new_price=amount,  # type: ignore[arg-type]
            line_id=uuid4(),
        )
        quotation = self._touched(detail.quotation, [*detail.lines, line], principal.email)
        return await self.repository.save(quotation, [], version, inserted=[line])

    async def remove_line(
        self, principal: Principal, quotation_id: UUID, version: int, sku: str
    ) -> QuotationDetail:
        """Take out a SKU the advisor added, a local product, or one inactive in SIESA (to
        replace it).

        Active sales-window SKUs stay: leave their price empty.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        line = next((item for item in detail.lines if item.sku == sku), None)
        if line is None:
            raise NotFoundError(f"La referencia {sku} no está en la cotización")
        if line.origin == vocabulary.FROM_LIST and line.is_active:
            raise InvalidInputError(
                "Solo se quitan referencias agregadas o inactivas; para no actualizar una de la "
                "lista, deja vacío su precio nuevo"
            )
        if line.origin == vocabulary.HISTORY and line.is_active:
            raise InvalidInputError(
                "Solo se quitan referencias agregadas, inactivas o solo para cotización; para no "
                "actualizar una del histórico, deja vacío su precio nuevo"
            )
        lines = [item for item in detail.lines if item.sku != sku]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(quotation, [], version, removed=[line.id])

    # ------------------------------------------------------------ offer

    async def update_offer(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        *,
        validity_days: int | None = None,
        conditions: Sequence[str] | None = None,
        include_seller: bool | None = None,
        quote_quantities: bool | None = None,
        offer_scope: str | None = None,
        include_variation: bool | None = None,
    ) -> QuotationDetail:
        """Change how the customer offer reads; ``None`` leaves a setting as it is.

        Allowed in every status and never changes it (the prices are not touched), but it
        checks and bumps the version. A new validity starts today.

        Raises:
            InvalidInputError: A value out of range.
        """

        detail = await self._editable(principal, quotation_id, any_status=True)
        quotation = detail.quotation
        self._no_offer(quotation)
        self._check_version(quotation, version)
        values: dict[str, object] = {}
        if validity_days is not None:
            _check_validity(validity_days)
            values["validity_days"] = validity_days
            values["valid_from"] = self.calendar.today()
        if conditions is not None:
            cleaned = offer.clean_conditions(conditions)
            if len(cleaned) > vocabulary.MAX_CONDITIONS:
                raise InvalidInputError(
                    f"La oferta admite máximo {vocabulary.MAX_CONDITIONS} condiciones"
                )
            for index, text in enumerate(cleaned, start=1):
                if len(text) > vocabulary.CONDITION_LENGTH:
                    raise InvalidInputError(
                        f"La condición {index} pasa de {vocabulary.CONDITION_LENGTH} caracteres"
                    )
            values["conditions"] = cleaned
        if include_seller is not None:
            values["include_seller"] = include_seller
        if quote_quantities is not None:
            values["quote_quantities"] = quote_quantities
        if include_variation is not None:
            values["include_variation"] = include_variation
        if offer_scope is not None:
            if offer_scope not in vocabulary.OFFER_SCOPES:
                raise InvalidInputError("Las líneas de la oferta son changed o all")
            values["offer_scope"] = offer_scope
        changed = replace(self._bumped(quotation, principal.email), **values)  # type: ignore[arg-type]
        return await self.repository.save(changed, [], version)

    async def offer_file(self, principal: Principal, quotation_id: UUID) -> ExportFile:
        """The offer for the customer as a workbook, in any status, for whoever reads it."""

        if self.offer_writer is None:
            raise InvalidInputError("La oferta en Excel no está disponible")
        detail = await self.get(principal, quotation_id)
        self._no_offer(detail.quotation)
        today = self.calendar.today()
        document = offer.offer_document(detail, today)
        quotation = detail.quotation
        name = _file_name(quotation.customer_name or quotation.customer_nit)
        return ExportFile(
            await asyncio.to_thread(self.offer_writer.write, document),
            f"Cotizacion {name} {today.isoformat()}.xlsx",
            _XLSX,
        )

    async def lines_file(self, principal: Principal, quotation_id: UUID) -> ExportFile:
        """Every line with what the platform knows and its costs, for BI to cross-check.

        Only validators get it: it carries the costs (the supplier's quotation, the
        inventory cost and the center that set each one), which the advisors do not see.
        The workbook is written in a worker thread, so a large quotation never holds up the
        API while it is written.
        """

        if self.lines_writer is None:
            raise InvalidInputError("La plantilla en Excel no está disponible")
        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        if not await self.can_validate(principal, quotation.kind):
            raise ForbiddenError("Solo quien valida las cotizaciones descarga la plantilla")
        names = {}
        try:
            names = {item.code: item.name for item in await self.warehouse.billing_centers()}
        except Exception as exc:  # noqa: BLE001 - the names only label the codes
            logger.warning("quotations: centers for the template not read: %s", exc)
        name = _file_name(quotation.customer_name or quotation.customer_nit)
        today = self.calendar.today()
        return ExportFile(
            await asyncio.to_thread(self.lines_writer.write, detail, names),
            f"Plantilla {name} {today.isoformat()}.xlsx",
            _XLSX,
        )

    # ------------------------------------------------------------ review

    def _check_rule(self, lines: Sequence[QuotationLine]) -> list[QuotationLine]:
        """Lines that go to the files, checked for a unit and whole pesos.

        The rule no longer blocks: a price under the minimum, without target or without cost
        goes to the validator marked (``below_rule_count``). There must be at least one
        priced active line, from SIESA or a local product (a temporary customer may only
        have local products).
        """

        changed = rules.exported(lines)
        priced = [
            line
            for line in lines
            if line.is_active and line.new_price is not None and line.new_price > 0
        ]
        if not priced:
            raise InvalidInputError("No hay referencias activas con precio nuevo para confirmar")
        for line in changed:
            if not line.unit:
                raise InvalidInputError(f"La referencia {line.sku} no tiene unidad de medida")
            if line.new_price is not None and line.new_price != line.new_price.to_integral():
                raise InvalidInputError(f"El precio de {line.sku} va en pesos enteros")
        return changed

    async def confirm(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        *,
        list_action: str | None,
        new_list_name: str | None,
        activation_date: date | None,
    ) -> QuotationDetail:
        """The advisor closes the draft and sends it to validation.

        - It needs at least one priced active line; the rule does not block (the validator
          sees the lines that break it).
        - ``actualizacion`` writes to the customer's own list; ``creacion`` creates a list
          whose code the validator gives. A customer on list 1 always gets a new list (list 1
          is not updated from here), and so does a temporary customer (no list in SIESA).

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read.
            list_action (str | None): ``actualizacion`` or ``creacion`` (default: what the
                quotation says).
            new_list_name (str | None): Description of a new list (optional; the validator
                may change it).
            activation_date (date | None): When SIESA activates the prices (default
                tomorrow); never in the past.

        Returns:
            QuotationDetail: The quotation, ``pendiente_validacion``.
        """

        detail = await self._editable(principal, quotation_id)
        quotation = detail.quotation
        _whole_review(quotation)
        self._check_version(quotation, version)
        self._check_rule(detail.lines)
        action = list_action or quotation.list_action
        if action not in vocabulary.LIST_ACTIONS:
            raise InvalidInputError("La acción de lista es actualizacion o creacion")
        if quotation.is_list_update and action != vocabulary.UPDATE_LIST:
            raise InvalidInputError("Una actualización de lista escribe en la misma lista")
        name: str | None = None
        if action == vocabulary.UPDATE_LIST and quotation.is_temporary_customer:
            raise InvalidInputError(
                "Un cliente temporal no tiene lista en SIESA: solo se crea una lista nueva"
            )
        if (
            not quotation.is_list_update
            and quotation.price_list_id == vocabulary.GENERAL_PRICE_LIST
            and action == vocabulary.UPDATE_LIST
        ):
            raise InvalidInputError(
                "El cliente está en la lista 1, que no se actualiza por este medio: "
                "crea una lista nueva"
            )
        if action == vocabulary.CREATE_LIST and (new_list_name or "").strip():
            name = list_name(new_list_name or "")
            if not name:
                raise InvalidInputError("Escribe la descripción de la lista nueva")
        today = self.calendar.today()
        activation = activation_date or today + timedelta(days=1)
        if activation < today:
            raise InvalidInputError("La fecha de activación no puede ser anterior a hoy")
        now = datetime.now(UTC)
        confirmed = replace(
            quotation,
            status=vocabulary.PENDING,
            list_action=action,
            export_price_list_id=None,
            new_list_name=name,
            activation_date=activation,
            confirmed_at=now,
            confirmed_by=principal.email,
            validated_at=None,
            validated_by=None,
            review_note=None,
            version=quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.save(confirmed, [], version)

    async def return_to_draft(
        self, principal: Principal, quotation_id: UUID, version: int, note: str | None
    ) -> QuotationDetail:
        """Send a pending quotation back to draft: the approver rejects it with a note, or
        an advisor withdraws it to keep editing."""

        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        _whole_review(quotation)
        self._check_version(quotation, version)
        if quotation.status != vocabulary.PENDING:
            raise ConflictError("Solo se devuelve una cotización pendiente de validación")
        approver = await self.can_validate(principal, quotation.kind)
        if not approver and not await self.can_manage(principal, quotation.kind):
            raise ForbiddenError("No tienes permiso para devolver la cotización")
        text = (note or "").strip() or None
        if approver and quotation.created_by_id != principal.user_id and text is None:
            raise InvalidInputError("Escribe por qué devuelves la cotización")
        now = datetime.now(UTC)
        returned = replace(
            quotation,
            status=vocabulary.DRAFT,
            review_note=text,
            confirmed_at=None,
            confirmed_by=None,
            version=quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.save(returned, [], version)

    async def validate(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        *,
        price_list_id: str | None = None,
        new_list_name: str | None = None,
    ) -> QuotationDetail:
        """The approver validates a pending quotation: the files are released and it waits
        to be loaded in SIESA (``pendiente_carga``).

        For ``creacion`` the approver gives the code of the new list (1 to 3 letters or
        digits, free in SIESA) and may change its description (default the one the advisor
        wrote, else ``COTIZADOR <customer>``). For ``actualizacion`` the files write to the
        customer's list.

        Raises:
            ForbiddenError: The caller does not validate.
            ConflictError: Stale version, or not pending validation.
            InvalidInputError: A missing, malformed or existing list code.
        """

        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        _whole_review(quotation)
        if not await self.can_validate(principal, quotation.kind):
            raise ForbiddenError("No tienes permiso para validar cotizaciones")
        self._check_version(quotation, version)
        if quotation.status != vocabulary.PENDING:
            raise ConflictError("La cotización no está pendiente de validación")
        self._check_rule(detail.lines)
        code = (price_list_id or "").strip().upper() or None
        name = quotation.new_list_name
        if quotation.list_action == vocabulary.CREATE_LIST:
            if code is None:
                raise InvalidInputError("Escribe el código de la lista nueva")
            if not _LIST_ID.fullmatch(code):
                raise InvalidInputError("El código de la lista es de 1 a 3 letras o números")
            if code == vocabulary.GENERAL_PRICE_LIST or code == quotation.price_list_id:
                raise InvalidInputError("La lista nueva debe tener un código distinto")
            if await self.warehouse.price_list_exists(code):
                raise InvalidInputError(f"La lista {code} ya existe en SIESA; usa otro código")
            if code in await self.repository.reserved_list_ids():
                raise InvalidInputError(
                    f"El código {code} ya lo tomó otra lista validada; usa otro código"
                )
            if (new_list_name or "").strip():
                name = list_name(new_list_name or "")
            name = name or list_name(f"COTIZADOR {quotation.customer_name or ''}")
            if not name:
                raise InvalidInputError("Escribe la descripción de la lista nueva")
        else:
            if code is not None and code != quotation.price_list_id:
                raise InvalidInputError(
                    f"La actualización escribe en la lista del cliente ({quotation.price_list_id})"
                )
            code, name = quotation.price_list_id, None
        now = datetime.now(UTC)
        validated = replace(
            quotation,
            status=vocabulary.UPLOAD_PENDING,
            export_price_list_id=code,
            new_list_name=name,
            validated_at=now,
            validated_by=principal.email,
            version=quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.save(validated, [], version)

    async def mark_uploaded(
        self, principal: Principal, quotation_id: UUID, version: int
    ) -> QuotationDetail:
        """The approver says the files were loaded in SIESA: ``pendiente_carga`` → ``cargada``.

        Raises:
            ForbiddenError: The caller does not validate.
            ConflictError: Stale version, or not pending upload.
        """

        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        _whole_review(quotation)
        if not await self.can_validate(principal, quotation.kind):
            raise ForbiddenError("Solo quien valida marca la cotización como cargada")
        self._check_version(quotation, version)
        if quotation.status != vocabulary.UPLOAD_PENDING:
            raise ConflictError("Solo se marca como cargada una cotización pendiente de carga")
        now = datetime.now(UTC)
        uploaded = replace(
            quotation,
            status=vocabulary.UPLOADED,
            uploaded_at=now,
            uploaded_by=principal.email,
            version=quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.save(uploaded, [], version)

    async def delete(self, principal: Principal, quotation_id: UUID) -> None:
        """Delete a quotation: its creator, or BI and admins."""

        detail = await self.get(principal, quotation_id)
        await self._require_owner_or_validator(principal, detail.quotation, "borrarla")
        await self.repository.delete(quotation_id)

    # ------------------------------------------------------------ export

    def _export(self, detail: QuotationDetail) -> PriceListExport:
        quotation = detail.quotation
        if quotation.status not in vocabulary.CLOSED or quotation.activation_date is None:
            raise ConflictError("Los archivos se generan cuando la cotización está validada")
        list_id = quotation.export_price_list_id or quotation.price_list_id
        rows: list[ExportRow] = []
        taxes: list[ExportTaxRow] = []
        for line in rules.exported(detail.lines):
            unit = line.unit or ""
            rows.append(
                ExportRow(
                    list_id,
                    line.sku,
                    quotation.activation_date,
                    unit,
                    line.new_price,  # type: ignore[arg-type]
                )
            )
            if line.value_tax_key and line.value_tax_amount:
                taxes.append(
                    ExportTaxRow(
                        list_id,
                        line.sku,
                        quotation.activation_date,
                        unit,
                        line.value_tax_key,
                        line.value_tax_amount,
                    )
                )
        new_list = None
        if quotation.list_action == vocabulary.CREATE_LIST:
            new_list = NewPriceList(list_id, quotation.new_list_name or list_name(list_id))
        return PriceListExport(tuple(rows), tuple(taxes), new_list)

    async def export(self, principal: Principal, quotation_id: UUID, fmt: str) -> ExportFile:
        """The update as the SIESA workbook (``xlsx``) or import file (``txt``)."""

        detail = await self.get(principal, quotation_id)
        export = self._export(detail)
        quotation = detail.quotation
        stamp = quotation.activation_date.strftime("%Y%m%d") if quotation.activation_date else ""
        list_id = quotation.export_price_list_id or quotation.price_list_id
        if fmt == "xlsx":
            return ExportFile(
                await asyncio.to_thread(self.workbook.write, export),
                f"ACTUALIZACION LISTAS PRECIOS_{principal.email}.xlsx",
                _XLSX,
            )
        owner = quotation.customer_nit or ("actualizacion" if quotation.is_list_update else "")
        return ExportFile(
            await asyncio.to_thread(self.flat_file.write, export),
            f"precios-lista{list_id}-{owner}-{stamp}.txt",
            "text/plain; charset=us-ascii",
        )


def _page(page: int, size: int) -> None:
    if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
        raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")


def _price(value: Decimal | None, sku: str) -> Decimal | None:
    """A new price: above zero, in whole pesos (SIESA's list takes no cents)."""

    if value is None:
        return None
    if not value.is_finite() or value <= 0:
        raise InvalidInputError(f"El precio de {sku} debe ser mayor que cero")
    if value >= MAX_PRICE:
        raise InvalidInputError(f"El precio de {sku} es demasiado alto")
    try:
        whole = value == value.to_integral_value()
    except InvalidOperation as exc:  # pragma: no cover - finite values compare
        raise InvalidInputError(f"Precio inválido para {sku}") from exc
    if not whole:
        raise InvalidInputError(f"El precio de {sku} va en pesos enteros")
    return value


def _temporary(value: TemporaryCustomer) -> TemporaryCustomer:
    """A temporary customer with its texts trimmed and checked, and its NIT cleaned.

    Raises:
        InvalidInputError: A required text is missing, a text is too long, or the NIT has no
            digits.
    """

    limit = vocabulary.TEMPORARY_TEXT_LENGTH

    def text(raw: str | None, label: str, required: bool) -> str | None:
        cleaned = " ".join((raw or "").split()) or None
        if cleaned is None and required:
            raise InvalidInputError(f"Escribe {label} del cliente temporal")
        if cleaned is not None and len(cleaned) > limit:
            raise InvalidInputError(f"{label.capitalize()} admite máximo {limit} caracteres")
        return cleaned

    nit = (value.nit or "").strip()
    return TemporaryCustomer(
        name=text(value.name, "el nombre", True),  # type: ignore[arg-type]
        legal_name=text(value.legal_name, "la razón social", True),  # type: ignore[arg-type]
        trade_name=text(value.trade_name, "el nombre comercial", False),
        nit=normalize_nit(nit) if nit else None,
    )


def _kept(line: QuotationLine, stored: QuotationLine | None) -> QuotationLine:
    """A repriced line with the advisor's price and quantity it had stored, and the review,
    observation and load of a list update's item (the price did not change)."""

    priced = rules.reprice(line, stored.new_price if stored else None)
    if stored is None:
        return replace(priced, quantity=None)
    return replace(
        priced,
        quantity=stored.quantity,
        review_status=stored.review_status,
        reviewed_by=stored.reviewed_by,
        reviewed_at=stored.reviewed_at,
        note=stored.note,
        load_id=stored.load_id,
    )


def _whole_review(quotation: Quotation) -> None:
    """A main list is reviewed item by item and loaded from its list's staging: it is never
    confirmed, validated or marked loaded as a whole. A list update (``lista``) is, like a
    quotation, since 2026-11-19."""

    if quotation.kind == vocabulary.MAIN_LIST:
        raise InvalidInputError(
            "Una lista principal se valida por ítem y se carga desde «Por cargar»"
        )


def _unreviewed(line: QuotationLine) -> QuotationLine:
    """An item whose price changed: unmarked again and out of any load (a new cycle)."""

    return replace(
        line,
        review_status=vocabulary.UNMARKED,
        reviewed_by=None,
        reviewed_at=None,
        load_id=None,
    )


def _quantity(value: Decimal | str | None, sku: str) -> Decimal | None:
    """A quantity in inventory units: above zero, at most two decimals and 10,000,000.

    Raises:
        InvalidInputError: Not a number, or out of range.
    """

    number = _decimal_input(value, f"La cantidad de {sku} debe ser un número")
    if number is None:
        return None
    if number <= 0:
        raise InvalidInputError(f"La cantidad de {sku} debe ser mayor que cero")
    if number > vocabulary.MAX_QUANTITY:
        raise InvalidInputError(
            f"La cantidad de {sku} pasa del máximo de {vocabulary.MAX_QUANTITY:,}".replace(",", ".")
        )
    if number != number.quantize(_QUANTITY_STEP):
        raise InvalidInputError(f"La cantidad de {sku} admite máximo dos decimales")
    return number.quantize(_QUANTITY_STEP)


def _check_validity(days: int) -> None:
    if isinstance(days, bool) or not 1 <= days <= vocabulary.MAX_VALIDITY_DAYS:
        raise InvalidInputError(f"La vigencia va de 1 a {vocabulary.MAX_VALIDITY_DAYS} días")


def _file_name(value: str) -> str:
    """Plain ASCII without the characters a file name cannot carry."""

    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = "".join(char if char.isascii() and char.isprintable() else " " for char in text)
    return " ".join(_FILE_UNSAFE.sub(" ", text).split()) or "cliente"


def _decimal_input(value: Decimal | str | int | None, message: str) -> Decimal | None:
    """A number the client sent as JSON number or text; blank is ``None``.

    Raises:
        InvalidInputError: ``message`` when it is not a finite number.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise InvalidInputError(message)
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InvalidInputError(message) from exc
    if not number.is_finite():
        raise InvalidInputError(message)
    return number


__all__ = [
    "MAX_PAGE_SIZE",
    "MAX_PASTED_SKUS",
    "ExportFile",
    "ImportResult",
    "LineChange",
    "Permissions",
    "QuotationService",
    "Skipped",
    "list_name",
    "normalize_nit",
    "parse_skus",
]
