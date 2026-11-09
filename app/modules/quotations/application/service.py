"""Use cases of the quoter."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.commercial_masters.domain.margins import channel_field
from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.catalog import Section
from app.modules.order_validation.domain.rules import BASIS
from app.modules.quotations.domain import offer, pricing, rules, vocabulary
from app.modules.quotations.domain.catalog import catalog
from app.modules.quotations.domain.entities import (
    CustomerContext,
    CustomerPage,
    ItemInfo,
    LastSale,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    QuotationPolicy,
    QuotationRef,
    RequestedProduct,
    RequestedProductPage,
    Seller,
    SellerContact,
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
    ManagerPolicy,
    MasterSource,
    OfferWriter,
    QuotationRepository,
    SalesScope,
    SalesScopeResolver,
    SupportSource,
    TemplateFile,
    WarehouseSource,
)

logger = logging.getLogger(__name__)
MAX_PAGE_SIZE = 100
MAX_PRICE = Decimal(10) ** 15
# SKUs one paste may carry.
MAX_PASTED_SKUS = 500
# Excel columns arrive one per line; lists may use commas, semicolons, tabs or spaces.
_SEPARATORS = re.compile(r"[\s,;|]+")
# A number cell copied from Excel may come as ``26278.0``.
_EXCEL_INTEGER = re.compile(r"^(\d+)\.0+$")
_LIST_ID = re.compile(r"^[A-Z0-9]{1,3}$")
_MARGIN_TOLERANCE = Decimal("0.00005")
_LIST_NAME_LENGTH = 40
# Longest description and supplier name of a local product.
LOCAL_TEXT_LENGTH = 120
_LOCAL_NO_MARGIN = "Producto solo para cotización: sin costo para calcular el precio"
_LOCAL_IN_TEMPLATE = "Producto solo para cotización: no se edita con la plantilla"
# Longest note BI writes on a requested product.
NOTE_LENGTH = 1000
_QUANTITY_STEP = Decimal("0.01")
# Characters a file name may not carry on Windows, macOS or Linux.
_FILE_UNSAFE = re.compile(r'[\\/:*?"<>|]+')


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
class TemplateResult:
    """What an uploaded workbook did."""

    updated: tuple[str, ...]
    cleared: tuple[str, ...]
    added: tuple[str, ...]
    unchanged: int
    not_found: tuple[str, ...]
    inactive: tuple[str, ...]
    skipped: tuple[Skipped, ...]


@dataclass(frozen=True)
class _Entry:
    """A SKU to price: its place in the quotation and where its data comes from."""

    sku: str
    position: int
    line_id: UUID
    sale: LastSale | None = None
    item: ItemInfo | None = None


def _entry_of(line: QuotationLine) -> _Entry:
    """Rebuild a stored line's source, to price it again without asking for its last sale."""

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


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    media_type: str


@dataclass(frozen=True)
class _Setup:
    """How a customer's quotation is costed and priced."""

    family: str
    plan: pricing.StockPlan
    regional: str


def normalize_nit(value: str) -> str:
    """Digits of a NIT without a separated check digit (``900.123.456-7`` -> ``900123456``)."""

    text = re.sub(r"-\s*\d\s*$", "", value.strip())
    nit = clean_nit(text)
    if not nit:
        raise InvalidInputError("Escribe el NIT del cliente")
    return nit


def _setup(price_list_id: str, billing_center: str | None, channel: str | None) -> _Setup:
    basis = BASIS.get(channel_field(channel), "lista")
    return _Setup(
        family=pricing.family(price_list_id, basis),
        plan=pricing.stock_plan(price_list_id, billing_center),
        regional=pricing.regional(billing_center),
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
        template: TemplateFile | None = None,
        max_upload_bytes: int = 10 * 1024 * 1024,
        sales: SalesScopeResolver | None = None,
        offer_writer: OfferWriter | None = None,
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
            template (TemplateFile | None): The workbook advisors edit in bulk.
            max_upload_bytes (int): Largest workbook accepted.
            sales (SalesScopeResolver | None): Each person's customers (salespeople module).
                Without one every reader sees every customer and quotation.
            offer_writer (OfferWriter | None): Writes the offer the customer receives.
        """

        self.repository = repository
        self.offer_writer = offer_writer
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
        self.template = template
        self.max_upload_bytes = max_upload_bytes
        self.sales = sales

    # ------------------------------------------------------------ access

    async def can_manage(self, principal: Principal) -> bool:
        """Whether the caller quotes: Comercial, and BI too (it validates and also quotes)."""

        if await self.managers.is_manager(principal):
            return True
        return self.approvers is not None and await self.approvers.is_manager(principal)

    async def can_validate(self, principal: Principal) -> bool:
        if principal.is_platform_admin:
            return True
        return self.approvers is not None and await self.approvers.is_manager(principal)

    async def _sees_all(self, principal: Principal) -> bool:
        """Validators and moderators read every quotation; anyone else only the ones they made."""

        return principal.is_moderator or await self.can_validate(principal)

    async def _require_owner_or_validator(
        self, principal: Principal, quotation: Quotation, action: str
    ) -> None:
        if quotation.created_by_id == principal.user_id or await self.can_validate(principal):
            return
        raise ForbiddenError(f"Solo quien creó la cotización puede {action}")

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.can_manage(principal):
            raise ForbiddenError("No tienes permiso para usar el cotizador")

    async def can_read(self, principal: Principal) -> bool:
        """Read on the quoter in either area (comercial quotes, BI validates)."""

        if await self.managers.can_read(principal):
            return True
        return self.approvers is not None and await self.approvers.can_read(principal)

    async def _require_reader(self, principal: Principal) -> None:
        if not await self.can_read(principal):
            raise ForbiddenError("No tienes permiso para consultar el cotizador")

    async def permissions(self, principal: Principal) -> Permissions:
        """Whether the caller may quote or validate, and the salesperson linked by e-mail.

        A warehouse outage never hides the page: the seller is just unknown.
        """

        can_manage = await self.can_manage(principal)
        seller = None
        if can_manage:
            try:
                sellers = await self.warehouse.sellers(principal.email)
            except Exception:  # noqa: BLE001 - the link is a convenience
                sellers = []
            seller = sellers[0] if sellers else None
        scope = await self._scope(principal)
        return Permissions(
            can_manage,
            seller,
            await self.can_validate(principal),
            scope.scope if scope is not None else None,
        )

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

        if quotation.created_by_id == principal.user_id or await self._sees_all(principal):
            return
        raise ForbiddenError("Solo puedes ver las cotizaciones que creaste")

    async def customers(
        self, principal: Principal, scope: str, q: str | None, page: int, size: int
    ) -> tuple[CustomerPage, bool]:
        """Customers to quote: the caller's portfolio (``mine``) or all.

        With a scope resolver the server decides: a salesperson or supervisor gets their
        portfolio or their team's whatever ``scope`` asks, and someone without a link gets
        none. Only a person whose scope is ``all`` chooses between ``mine`` and ``all``.

        Returns:
            tuple[CustomerPage, bool]: The page and whether the caller is linked to a seller.
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
            return await self.masters.customers(codes, q, page, size), linked
        if scope == "mine":
            codes = [seller.code for seller in await self.warehouse.sellers(principal.email)]
            return await self.masters.customers(codes, q, page, size), bool(codes)
        try:
            linked = bool(await self.warehouse.sellers(principal.email))
        except Exception:  # noqa: BLE001 - searching every customer needs no warehouse
            linked = False
        return await self.masters.customers(None, q, page, size), linked

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

    async def _price(
        self, context: CustomerContext, entries: Sequence[_Entry]
    ) -> tuple[list[QuotationLine], date | None]:
        """Price SKUs (from the sales window or added) keeping each entry's id and position.

        The product copy goes first (it gives the supplier of each SKU); then the warehouse
        reads and the supports are fetched at the same time.
        """

        if not entries:
            return [], None
        setup = _setup(context.price_list_id, context.billing_center_code, context.channel)
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
        costs, prices, taxes, agreements, scopes, names = await asyncio.gather(
            self.warehouse.costs(skus, setup.plan),
            self.warehouse.list_prices(context.price_list_id, skus),
            self.warehouse.value_tax_keys(skus),
            self.supports.agreements(codes, skus, [context.nit]),
            self.supports.customers([context.nit]),
            self.masters.supplier_names(codes),
        )
        today = self.calendar.today()
        lines: list[QuotationLine] = []
        for entry in entries:
            sku, supplier = entry.sku, supplier_of(entry.sku)
            lines.append(
                rules.build_line(
                    entry.position,
                    sku,
                    entry.sale,
                    item=entry.item,
                    nit=context.nit,
                    channel=context.channel,
                    scope=scopes.get(context.nit),
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
                )
            )
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
    ) -> tuple[list[QuotationLine], date | None]:
        """Lines of the sales window plus the SKUs the advisor had added.

        ``reserved`` lines (the local products a refresh keeps) count toward the maximum.
        """

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
        )

    @staticmethod
    def _customer_fields(context: CustomerContext) -> dict[str, object]:
        """Header fields that come from the customer and how it is priced."""

        setup = _setup(context.price_list_id, context.billing_center_code, context.channel)
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
            "regional": setup.regional,
            "price_family": setup.family,
            "stock_source": setup.plan.label,
            # List 1 is never updated from a quotation: its customer gets a new list.
            "list_action": vocabulary.CREATE_LIST if general else vocabulary.UPDATE_LIST,
        }

    async def _seller_contact(self, context: CustomerContext) -> SellerContact | None:
        """The customer's salesperson as the offer shows them; ``None`` without a code."""

        if not context.salesperson_code:
            return None
        return await self.warehouse.seller_contact(context.salesperson_code)

    async def create(
        self,
        principal: Principal,
        customer_nit: str,
        window_months: int,
        validity_days: int = vocabulary.DEFAULT_VALIDITY_DAYS,
    ) -> QuotationDetail:
        """Build a quotation of a customer from its last sales in the window.

        Args:
            principal (Principal): The advisor.
            customer_nit (str): NIT (punctuation and a separated check digit are accepted).
            window_months (int): 6 or 12.
            validity_days (int): Days the offer holds from today (1 to 365).

        Returns:
            QuotationDetail: The new draft with every SKU priced as it was last sold.
        """

        await self._require_manager(principal)
        if window_months not in vocabulary.WINDOWS:
            raise InvalidInputError("La ventana debe ser de 6 o 12 meses")
        _check_validity(validity_days)
        nit = normalize_nit(customer_nit)
        context = await self._context(nit, await self._scope(principal))
        lines, snapshot = await self._build(context, window_months)
        seller = await self._seller_contact(context)
        if not lines:
            # A customer without sales in the window is quoted with the SKUs the advisor adds.
            context = replace(context, warnings=(*context.warnings, "sin_ventas"))
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
            **self._customer_fields(context),  # type: ignore[arg-type]
        )
        return await self.repository.create(quotation, lines)

    # ------------------------------------------------------------ read

    async def get(self, principal: Principal, quotation_id: UUID) -> QuotationDetail:
        await self._require_reader(principal)
        detail = await self.repository.get(quotation_id)
        if detail is None:
            raise NotFoundError("Cotización no encontrada")
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
        self, principal: Principal, quotation_id: UUID, *, any_status: bool = False
    ) -> QuotationDetail:
        """The quotation, if the caller may edit it and it is not awaiting validation.

        BI (and admins) has the whole module: it edits any quotation. Anyone else, only theirs.
        ``any_status`` lets through a pending one too: for changes that never touch prices
        (offer settings, quantities).
        """

        await self._require_manager(principal)
        detail = await self.get(principal, quotation_id)
        await self._require_owner_or_validator(principal, detail.quotation, "editarla")
        if not any_status and detail.quotation.status == vocabulary.PENDING:
            raise ConflictError(
                "La cotización está pendiente de validación; devuélvela a borrador para editarla"
            )
        return detail

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
    ) -> QuotationPage:
        await self._require_reader(principal)
        _page(page, size)
        if status is not None and status not in vocabulary.STATUSES:
            raise InvalidInputError("Estado desconocido")
        own_only = mine or not await self._sees_all(principal)
        return await self.repository.list(
            created_by_id=principal.user_id if own_only else None,
            status=status,
            customer_nit=normalize_nit(customer_nit) if customer_nit else None,
            q=q.strip() if q and q.strip() else None,
            page=page,
            size=size,
        )

    # ------------------------------------------------------------ change

    def _touched(
        self, quotation: Quotation, lines: Sequence[QuotationLine], actor: str
    ) -> Quotation:
        """The header after an edit: back to draft, counts again, review stamps cleared."""

        total, changed, below, below_target = rules.counts(lines)
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
        rebuilt lines.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        context = await self._context(detail.quotation.customer_nit)
        added = [line.sku for line in detail.lines if line.origin == vocabulary.ADDED]
        local = [line for line in detail.lines if line.is_local]
        lines, snapshot = await self._build(
            context, detail.quotation.window_months, added, reserved=len(local)
        )
        if not lines and not local:
            context = replace(context, warnings=(*context.warnings, "sin_ventas"))
        stored = {line.sku: line for line in detail.lines}
        lines = [_kept(line, stored.get(line.sku)) for line in lines]
        lines += [
            replace(line, position=len(lines) + offset)
            for offset, line in enumerate(local, start=1)
        ]
        fields = self._customer_fields(context)
        fields["seller_contact"] = await self._seller_contact(context)
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

    async def _new_skus(
        self, detail: QuotationDetail, skus: Sequence[str]
    ) -> tuple[list[_Entry], list[str], list[str]]:
        """Entries for SKUs not in the quotation yet, and the unknown and inactive ones."""

        items = await self.warehouse.items(skus) if skus else {}
        not_found = [sku for sku in skus if sku not in items]
        inactive = [sku for sku in skus if sku in items and not items[sku].is_active]
        valid = [sku for sku in skus if sku in items and items[sku].is_active]
        if len(detail.lines) + len(valid) > self.max_lines:
            raise InvalidInputError(f"La cotización admite máximo {self.max_lines} referencias")
        start = max((line.position for line in detail.lines), default=0) + 1
        entries = [
            _Entry(sku, start + offset, uuid4(), item=items[sku])
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
        entries = [_entry_of(present[sku]) for sku in existing] + fresh
        priced, _ = await self._price(self._stored_context(detail.quotation), entries)
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
        requested_product_id: UUID | None = None,
    ) -> QuotationDetail:
        """Quote a product SIESA does not have (``origin: local``).

        It gets the next ``LOC-<n>`` of the quotation and goes at the end. It has no cost, so
        the rule does not judge it: it is not counted as changed or below the rule, it does
        not block the confirmation, and it never reaches the workbook or the SIESA files.

        A new product is entered in the requested products registry (``solicitado``) and the
        line points to it. With ``requested_product_id`` (an entry still ``solicitado``) the
        description, supplier and VAT come from that entry, and the price from ``price`` or,
        without one, the entry's estimated price.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read (409 when it changed).
            description (str | None): What the product is (1 to 120 characters).
            supplier_name (str | None): Who supplies it, optional (up to 120).
            price (Decimal | str | None): Price before VAT, whole pesos above zero.
            vat_pct (Decimal | str | None): VAT in points: 19, 5 or 0.
            requested_product_id (UUID | None): A registry entry to quote again.

        Returns:
            QuotationDetail: The quotation, back to draft, with the new line.

        Raises:
            InvalidInputError: A field is missing or out of range, the registry entry does not
                exist or is not ``solicitado``, or the quotation is full.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        amount = _decimal_input(price, "El precio debe ser un número en pesos")
        created: list[RequestedProduct] = []
        if requested_product_id is not None:
            product = await self.repository.get_requested_product(requested_product_id)
            if product is None:
                raise InvalidInputError("El producto solicitado no existe")
            if product.status != vocabulary.PRODUCT_REQUESTED:
                label = vocabulary.REQUESTED_STATUS_LABELS.get(product.status, product.status)
                raise InvalidInputError(
                    f"El producto solicitado está {label.lower()}: solo se cotiza uno solicitado"
                )
            text, supplier = product.description, product.supplier_name
            vat = product.vat_pct if product.vat_pct is not None else Decimal(0)
            if amount is None:
                amount = product.estimated_price
        else:
            text = " ".join((description or "").split())
            if not text:
                raise InvalidInputError("Escribe la descripción del producto")
            if len(text) > LOCAL_TEXT_LENGTH:
                raise InvalidInputError(
                    f"La descripción admite máximo {LOCAL_TEXT_LENGTH} caracteres"
                )
            supplier = " ".join((supplier_name or "").split()) or None
            if supplier is not None and len(supplier) > LOCAL_TEXT_LENGTH:
                raise InvalidInputError(
                    f"El proveedor admite máximo {LOCAL_TEXT_LENGTH} caracteres"
                )
            vat = _decimal_input(vat_pct, "El IVA es 19, 5 o 0 %")
            if vat is None or vat not in vocabulary.LOCAL_VAT_POINTS:
                raise InvalidInputError("El IVA es 19, 5 o 0 %")
        if amount is None:
            raise InvalidInputError("Escribe el precio del producto, antes de IVA")
        amount = _price(amount, text)
        if len(detail.lines) + 1 > self.max_lines:
            raise InvalidInputError(f"La cotización admite máximo {self.max_lines} referencias")
        if requested_product_id is None:
            now = datetime.now(UTC)
            product = RequestedProduct(
                id=uuid4(),
                description=text,
                supplier_name=supplier,
                estimated_price=amount,
                vat_pct=Decimal(int(vat)),
                status=vocabulary.PRODUCT_REQUESTED,
                siesa_sku=None,
                note=None,
                created_by=principal.email,
                created_at=now,
                updated_by=principal.email,
                updated_at=now,
            )
            created.append(product)
        line = rules.local_line(
            max((item.position for item in detail.lines), default=0) + 1,
            rules.local_sku(detail.lines),
            description=text,
            supplier_name=supplier,
            vat_pct=Decimal(int(vat)) if vat == vat.to_integral_value() else vat,
            new_price=amount,  # type: ignore[arg-type]
            line_id=uuid4(),
        )
        line = replace(line, requested_product_id=product.id)
        quotation = self._touched(detail.quotation, [*detail.lines, line], principal.email)
        return await self.repository.save(
            quotation, [], version, inserted=[line], requested=created
        )

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
        if line.origin == vocabulary.HISTORY and line.is_active:
            raise InvalidInputError(
                "Solo se quitan referencias agregadas, inactivas o solo para cotización; para no "
                "actualizar una del histórico, deja vacío su precio nuevo"
            )
        lines = [item for item in detail.lines if item.sku != sku]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(quotation, [], version, removed=[line.id])

    async def promote_line(
        self, principal: Principal, quotation_id: UUID, version: int, sku: str
    ) -> QuotationDetail:
        """Swap a local product for the SIESA reference BI created for it.

        The local line's registry entry must be ``creado``. The SIESA reference is priced like
        an added one (``agregado``) in the local line's place, keeping its new price and
        quantity. If the quotation already has that reference, the local line just goes and
        its quantity passes to the reference when that one has none.

        Raises:
            InvalidInputError: The line is not a local product, its entry is not ``creado``,
                or the reference is no longer active in SIESA.
        """

        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        line = next((item for item in detail.lines if item.sku == sku), None)
        if line is None:
            raise NotFoundError(f"La referencia {sku} no está en la cotización")
        if not line.is_local:
            raise InvalidInputError(f"La referencia {sku} no es un producto solo para cotización")
        product = (
            await self.repository.get_requested_product(line.requested_product_id)
            if line.requested_product_id
            else None
        )
        if product is None or product.status != vocabulary.PRODUCT_CREATED or not product.siesa_sku:
            raise InvalidInputError(
                "El producto todavía no está creado en SIESA: BI debe marcarlo como creado"
            )
        target = product.siesa_sku
        existing = next((item for item in detail.lines if item.sku == target), None)
        if existing is not None:
            changed: list[QuotationLine] = []
            if existing.quantity is None and line.quantity is not None:
                changed.append(replace(existing, quantity=line.quantity))
            by_id = {item.id: item for item in changed}
            lines = [by_id.get(item.id, item) for item in detail.lines if item.id != line.id]
            quotation = self._touched(detail.quotation, lines, principal.email)
            return await self.repository.save(quotation, changed, version, removed=[line.id])
        items = await self.warehouse.items([target])
        item = items.get(target)
        if item is None or not item.is_active:
            raise InvalidInputError(f"La referencia {target} no existe o está inactiva en SIESA")
        priced, _ = await self._price(
            self._stored_context(detail.quotation),
            [_Entry(target, line.position, uuid4(), item=item)],
        )
        promoted = replace(rules.reprice(priced[0], line.new_price), quantity=line.quantity)
        lines = [promoted if item.id == line.id else item for item in detail.lines]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(
            quotation, [], version, inserted=[promoted], removed=[line.id]
        )

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
    ) -> QuotationDetail:
        """Change how the customer offer reads; ``None`` leaves a setting as it is.

        Allowed in every status and never changes it (the prices are not touched), but it
        checks and bumps the version. A new validity starts today.

        Raises:
            InvalidInputError: A value out of range.
        """

        detail = await self._editable(principal, quotation_id, any_status=True)
        quotation = detail.quotation
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
        today = self.calendar.today()
        document = offer.offer_document(detail, today)
        quotation = detail.quotation
        name = _file_name(quotation.customer_name or quotation.customer_nit)
        return ExportFile(
            self.offer_writer.write(document),
            f"Cotizacion {name} {today.isoformat()}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ------------------------------------------------------------ requested products

    async def _require_registry_reader(self, principal: Principal) -> None:
        if not (await self.can_manage(principal) or await self.can_validate(principal)):
            raise ForbiddenError("No tienes permiso para ver los productos solicitados")

    async def _visible_refs(
        self, principal: Principal, product_ids: Sequence[UUID]
    ) -> dict[UUID, list[QuotationRef]]:
        """The quotations of each entry that the caller can open."""

        refs = await self.repository.requested_product_quotations(product_ids)
        if await self._sees_all(principal):
            return refs
        return {
            key: [item for item in values if item.created_by_id == principal.user_id]
            for key, values in refs.items()
        }

    async def requested_products(
        self, principal: Principal, *, status: str | None, q: str | None, page: int, size: int
    ) -> tuple[RequestedProductPage, dict[UUID, list[QuotationRef]]]:
        """The registry, requested first, with the quotations of each the caller can open."""

        await self._require_registry_reader(principal)
        _page(page, size)
        if status is not None and status not in vocabulary.REQUESTED_STATUSES:
            raise InvalidInputError("Estado desconocido")
        result = await self.repository.list_requested_products(
            status=status, q=q.strip() if q and q.strip() else None, page=page, size=size
        )
        refs = await self._visible_refs(principal, [item.id for item in result.items])
        return result, refs

    async def update_requested_product(
        self,
        principal: Principal,
        product_id: UUID,
        *,
        status: str,
        siesa_sku: str | None = None,
        note: str | None = None,
        set_note: bool = False,
    ) -> tuple[RequestedProduct, list[QuotationRef]]:
        """BI marks a requested product as created (with its SIESA reference), discarded, or
        requested again.

        ``creado`` needs a SIESA reference that exists and is active. Only ``creado`` keeps a
        reference: going back to ``solicitado`` (or to ``descartado``) clears it. The note
        changes only when ``set_note``.

        Raises:
            ForbiddenError: The caller does not validate.
            NotFoundError: The entry does not exist.
            InvalidInputError: Unknown status, or a missing, unknown or inactive reference.
        """

        if not await self.can_validate(principal):
            raise ForbiddenError("Solo quien valida gestiona los productos solicitados")
        product = await self.repository.get_requested_product(product_id)
        if product is None:
            raise NotFoundError("Producto solicitado no encontrado")
        if status not in vocabulary.REQUESTED_STATUSES:
            raise InvalidInputError("El estado es solicitado, creado o descartado")
        code: str | None = None
        if status == vocabulary.PRODUCT_CREATED:
            code = (siesa_sku or "").strip() or None
            if code is None:
                raise InvalidInputError("Escribe la referencia SIESA del producto creado")
            item = (await self.warehouse.items([code])).get(code)
            if item is None:
                raise InvalidInputError(f"La referencia {code} no existe en SIESA")
            if not item.is_active:
                raise InvalidInputError(f"La referencia {code} está inactiva en SIESA")
        values: dict[str, object] = {"status": status, "siesa_sku": code}
        if set_note:
            text = (note or "").strip() or None
            if text is not None and len(text) > NOTE_LENGTH:
                raise InvalidInputError(f"La nota admite máximo {NOTE_LENGTH} caracteres")
            values["note"] = text
        updated = replace(
            product,
            updated_by=principal.email,
            updated_at=datetime.now(UTC),
            **values,  # type: ignore[arg-type]
        )
        saved = await self.repository.update_requested_product(updated)
        refs = await self._visible_refs(principal, [saved.id])
        return saved, refs.get(saved.id, [])

    # ------------------------------------------------------------ template

    async def template_file(self, principal: Principal, quotation_id: UUID) -> ExportFile:
        """The quotation as a workbook to edit prices or margins in bulk.

        Local products stay out: the workbook is about SIESA references.
        """

        if self.template is None:
            raise InvalidInputError("La plantilla no está disponible")
        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        siesa = replace(detail, lines=tuple(rules.siesa_lines(detail.lines)))
        return ExportFile(
            self.template.write(siesa),
            f"cotizacion-{quotation.customer_nit}-lista{quotation.price_list_id}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _template_change(
        self, line: QuotationLine, new_price: Decimal | None, new_margin: Decimal | None
    ) -> LineChange | None:
        """What a workbook row asks for a line: the cell the advisor changed wins.

        The file comes back with both columns filled; a price that differs from the stored
        one wins, else a margin that differs, else two blank cells clear the price.
        """

        if new_price is not None and new_price != line.new_price:
            return LineChange(line.sku, new_price=new_price)
        stored = line.new_margin_with_support_pct
        if new_margin is not None and (
            stored is None or abs(new_margin - stored) > _MARGIN_TOLERANCE
        ):
            return LineChange(line.sku, margin_pct=new_margin)
        if new_price is None and new_margin is None and line.new_price is not None:
            return LineChange(line.sku, new_price=None)
        return None

    async def upload_template(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        filename: str | None,
        content: bytes,
    ) -> tuple[QuotationDetail, TemplateResult]:
        """Apply a workbook downloaded with :meth:`template_file` and edited by the advisor.

        Each row sets the SKU's new price, or prices it at the new margin; both blank clears
        the price. SKUs the quotation does not have are added (if they exist and are active)
        and priced the same way. Rows that cannot be read or priced, and rows of a local
        product (``LOC-<n>``), are reported; the rest is saved in one go.

        Raises:
            InvalidInputError: Empty, too big, unreadable, or from another quotation.
        """

        if self.template is None:
            raise InvalidInputError("La plantilla no está disponible")
        if not filename or not content:
            raise InvalidInputError("Sube la plantilla de la cotización")
        if len(content) > self.max_upload_bytes:
            raise InvalidInputError(
                f"El archivo supera el máximo de {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        detail = await self._editable(principal, quotation_id)
        self._check_version(detail.quotation, version)
        owner = self.template.quotation_id(content)
        if owner is not None and owner != quotation_id:
            raise InvalidInputError("La plantilla es de otra cotización")
        rows = self.template.read(filename, content)
        if not rows:
            raise InvalidInputError("La plantilla no tiene referencias")
        family = detail.quotation.price_family
        skipped: list[Skipped] = []
        by_row: dict[str, tuple[int, Decimal | None, Decimal | None]] = {}
        for row in rows:
            if rules.is_local_sku(row.sku):
                skipped.append(Skipped(row.sku, _LOCAL_IN_TEMPLATE, row.row_number))
                continue
            if row.problem:
                skipped.append(Skipped(row.sku, row.problem, row.row_number))
                continue
            by_row[row.sku] = (row.row_number, row.new_price, row.new_margin_pct)

        present = {line.sku: line for line in detail.lines if not line.is_local}
        new = [sku for sku in by_row if sku not in present]
        if len(new) > MAX_PASTED_SKUS:
            raise InvalidInputError(f"Se agregan máximo {MAX_PASTED_SKUS} referencias por vez")
        fresh, not_found, inactive = await self._new_skus(detail, new)
        added_lines: dict[str, QuotationLine] = {}
        if fresh:
            priced, _ = await self._price(self._stored_context(detail.quotation), fresh)
            added_lines = {line.sku: line for line in priced}

        updated: dict[str, QuotationLine] = {}
        cleared: list[str] = []
        unchanged = 0
        for sku, (number, price, margin) in by_row.items():
            line = present.get(sku) or added_lines.get(sku)
            if line is None:
                continue
            change = self._template_change(line, price, margin)
            if change is None:
                unchanged += 1
                if sku in added_lines:
                    updated[sku] = line
                continue
            try:
                priced_line, reason = self._apply(line, change, family)
            except InvalidInputError as exc:
                priced_line, reason = None, Skipped(sku, exc.detail)
            if priced_line is None:
                skipped.append(replace(reason, row_number=number))  # type: ignore[arg-type]
                if sku in added_lines:
                    updated[sku] = line
                continue
            if change.new_price is None and change.margin_pct is None:
                cleared.append(sku)
            updated[sku] = priced_line

        changed = [sku for sku in updated if sku in present]
        inserted = [updated[sku] for sku in added_lines if sku in updated]
        if not changed and not inserted:
            result = TemplateResult(
                (), (), (), unchanged, tuple(not_found), tuple(inactive), tuple(skipped)
            )
            return detail, result
        lines = [updated.get(line.sku, line) for line in detail.lines] + inserted
        quotation = self._touched(detail.quotation, lines, principal.email)
        saved = await self.repository.save(
            quotation, [updated[sku] for sku in changed], version, inserted=inserted
        )
        return saved, TemplateResult(
            updated=tuple(sku for sku in changed if sku not in cleared),
            cleared=tuple(cleared),
            added=tuple(line.sku for line in inserted),
            unchanged=unchanged,
            not_found=tuple(not_found),
            inactive=tuple(inactive),
            skipped=tuple(skipped),
        )

    # ------------------------------------------------------------ review

    def _check_rule(self, lines: Sequence[QuotationLine]) -> list[QuotationLine]:
        """Lines that go to the files; every one must meet the rule and have a unit."""

        changed = rules.exported(lines)
        if not changed:
            raise InvalidInputError("No hay precios nuevos de referencias activas para generar")
        blocked = [line for line in changed if line.compliance != vocabulary.MEETS]
        if blocked:
            listed = ", ".join(
                f"{line.sku} ({vocabulary.COMPLIANCE_LABELS.get(line.compliance or '', '')})"
                for line in blocked[:10]
            )
            more = f" y {len(blocked) - 10} más" if len(blocked) > 10 else ""
            raise InvalidInputError(f"Hay precios que no cumplen la regla: {listed}{more}")
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
        price_list_id: str | None,
        new_list_name: str | None,
        activation_date: date | None,
    ) -> QuotationDetail:
        """The advisor closes the draft and sends it to validation.

        - Every new price of an active SKU must meet the rule (inactive SKUs never go out).
        - ``actualizacion`` writes to the customer's own list; ``creacion`` creates the list
          ``price_list_id`` (1 to 3 letters or digits, free in SIESA). A customer on list 1
          always gets a new list: list 1 is not updated from here.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read.
            list_action (str | None): ``actualizacion`` or ``creacion`` (default: what the
                quotation says).
            price_list_id (str | None): Code of the new list (creation only).
            new_list_name (str | None): Its description (default ``COTIZADOR <customer>``).
            activation_date (date | None): When SIESA activates the prices (default
                tomorrow); never in the past.

        Returns:
            QuotationDetail: The quotation, ``pendiente_validacion``.
        """

        detail = await self._editable(principal, quotation_id)
        quotation = detail.quotation
        self._check_version(quotation, version)
        self._check_rule(detail.lines)
        action = list_action or quotation.list_action
        if action not in vocabulary.LIST_ACTIONS:
            raise InvalidInputError("La acción de lista es actualizacion o creacion")
        code = (price_list_id or "").strip().upper() or None
        name: str | None = None
        if quotation.price_list_id == vocabulary.GENERAL_PRICE_LIST and (
            action == vocabulary.UPDATE_LIST
        ):
            raise InvalidInputError(
                "El cliente está en la lista 1, que no se actualiza por este medio: "
                "crea una lista nueva"
            )
        if action == vocabulary.UPDATE_LIST:
            if code is not None and code != quotation.price_list_id:
                raise InvalidInputError(
                    f"La actualización escribe en la lista del cliente ({quotation.price_list_id})"
                )
            code = quotation.price_list_id
        else:
            if code is None:
                raise InvalidInputError("Escribe el código de la lista nueva")
            if not _LIST_ID.fullmatch(code):
                raise InvalidInputError("El código de la lista es de 1 a 3 letras o números")
            if code == vocabulary.GENERAL_PRICE_LIST or code == quotation.price_list_id:
                raise InvalidInputError("La lista nueva debe tener un código distinto")
            if await self.warehouse.price_list_exists(code):
                raise InvalidInputError(f"La lista {code} ya existe en SIESA; usa otro código")
            name = list_name(new_list_name or f"COTIZADOR {quotation.customer_name or ''}")
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
            export_price_list_id=code,
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
        self._check_version(quotation, version)
        if quotation.status != vocabulary.PENDING:
            raise ConflictError("Solo se devuelve una cotización pendiente de validación")
        approver = await self.can_validate(principal)
        if not approver and not await self.can_manage(principal):
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
        self, principal: Principal, quotation_id: UUID, version: int
    ) -> QuotationDetail:
        """The approver validates a pending quotation: the files are released and the list
        counts as ``creada`` or ``actualizada``."""

        if not await self.can_validate(principal):
            raise ForbiddenError("No tienes permiso para validar cotizaciones")
        detail = await self.get(principal, quotation_id)
        quotation = detail.quotation
        self._check_version(quotation, version)
        if quotation.status != vocabulary.PENDING:
            raise ConflictError("La cotización no está pendiente de validación")
        self._check_rule(detail.lines)
        if quotation.list_action == vocabulary.CREATE_LIST and quotation.export_price_list_id:
            if await self.warehouse.price_list_exists(quotation.export_price_list_id):
                raise InvalidInputError(
                    f"La lista {quotation.export_price_list_id} ya existe en SIESA; devuelve la "
                    "cotización para cambiar el código"
                )
        now = datetime.now(UTC)
        validated = replace(
            quotation,
            status=vocabulary.CLOSED_BY_ACTION[quotation.list_action],
            validated_at=now,
            validated_by=principal.email,
            version=quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.save(validated, [], version)

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
                self.workbook.write(export),
                f"ACTUALIZACION LISTAS PRECIOS_{principal.email}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        return ExportFile(
            self.flat_file.write(export),
            f"precios-lista{list_id}-{quotation.customer_nit}-{stamp}.txt",
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


def _kept(line: QuotationLine, stored: QuotationLine | None) -> QuotationLine:
    """A repriced line with the advisor's price and quantity it had stored."""

    priced = rules.reprice(line, stored.new_price if stored else None)
    return replace(priced, quantity=stored.quantity if stored else None)


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
    "TemplateResult",
    "list_name",
    "normalize_nit",
    "parse_skus",
]
