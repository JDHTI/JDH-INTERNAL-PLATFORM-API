"""Use cases of the quoter."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.catalog import Section
from app.modules.quotations.domain import rules, vocabulary
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
    Seller,
)
from app.modules.quotations.domain.export import ExportRow, ExportTaxRow, PriceListExport
from app.modules.quotations.domain.ports import (
    Calendar,
    ExportWriter,
    ManagerPolicy,
    MasterSource,
    QuotationRepository,
    SupportSource,
    WarehouseSource,
)

MAX_PAGE_SIZE = 100
MAX_PRICE = Decimal(10) ** 15
_LIST_ID = re.compile(r"^[A-Za-z0-9]{1,3}$")


@dataclass(frozen=True)
class LineChange:
    """The advisor's price for a SKU; ``None`` clears it."""

    sku: str
    new_price: Decimal | None


@dataclass(frozen=True)
class Permissions:
    can_manage: bool
    seller: Seller | None


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    media_type: str


def normalize_nit(value: str) -> str:
    """Digits of a NIT without a separated check digit (``900.123.456-7`` -> ``900123456``)."""

    text = re.sub(r"-\s*\d\s*$", "", value.strip())
    nit = clean_nit(text)
    if not nit:
        raise InvalidInputError("Escribe el NIT del cliente")
    return nit


class QuotationService:
    """Build quotations from the warehouse, price them and export the update for SIESA."""

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
        """

        self.repository = repository
        self.warehouse = warehouse
        self.masters = masters
        self.supports = supports
        self.managers = managers
        self.calendar = calendar
        self.workbook = workbook
        self.flat_file = flat_file
        self.policy = policy or QuotationPolicy()
        self.max_lines = max_lines

    # ------------------------------------------------------------ access

    async def can_manage(self, principal: Principal) -> bool:
        return await self.managers.is_manager(principal)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para usar el cotizador")

    async def permissions(self, principal: Principal) -> Permissions:
        """Whether the caller may quote, and the warehouse salesperson linked by e-mail.

        A warehouse outage never hides the page: the seller is just unknown.
        """

        can_manage = await self.managers.is_manager(principal)
        seller = None
        if can_manage:
            try:
                sellers = await self.warehouse.sellers(principal.email)
            except Exception:  # noqa: BLE001 - the link is a convenience
                sellers = []
            seller = sellers[0] if sellers else None
        return Permissions(can_manage, seller)

    def rule_catalog(self) -> tuple[Section, ...]:
        return catalog(self.policy)

    async def customers(
        self, principal: Principal, scope: str, q: str | None, page: int, size: int
    ) -> tuple[CustomerPage, bool]:
        """Customers to quote: the caller's portfolio (``mine``) or all.

        Returns:
            tuple[CustomerPage, bool]: The page and whether the caller is linked to a seller.
        """

        await self._require_manager(principal)
        _page(page, size)
        if scope == "mine":
            codes = [seller.code for seller in await self.warehouse.sellers(principal.email)]
            return await self.masters.customers(codes, q, page, size), bool(codes)
        try:
            linked = bool(await self.warehouse.sellers(principal.email))
        except Exception:  # noqa: BLE001 - searching every customer needs no warehouse
            linked = False
        return await self.masters.customers(None, q, page, size), linked

    # ------------------------------------------------------------ build

    async def _context(self, nit: str) -> CustomerContext:
        branches = await self.warehouse.branches(nit)
        if not branches:
            raise NotFoundError(f"No se encontró el cliente con NIT {nit}")
        draft = rules.customer_context(nit, branches)
        shared = await self.warehouse.price_list_customers(draft.price_list_id)
        return rules.customer_context(nit, branches, shared)

    async def _price(
        self,
        context: CustomerContext,
        sales: Sequence[LastSale],
        added: Mapping[str, ItemInfo],
        start: int = 1,
    ) -> tuple[list[QuotationLine], date | None]:
        """Price the SKUs of the sales window and the ones the advisor added, in that order."""

        sold = {sale.sku: sale for sale in sales}
        skus = [*sold, *(sku for sku in added if sku not in sold)]
        if not skus:
            return [], None
        products = await self.masters.products(skus)

        def supplier_of(sku: str) -> str | None:
            product = products.get(sku)
            source = sold.get(sku) or added.get(sku)
            return (product.supplier_code if product else None) or (
                source.supplier_code if source else None
            )

        codes = sorted({code for code in map(supplier_of, skus) if code})
        costs = await self.warehouse.costs(skus, context.billing_center_code)
        prices = await self.warehouse.list_prices(context.price_list_id, skus)
        taxes = await self.warehouse.value_tax_keys(skus)
        agreements = await self.supports.agreements(codes, skus, [context.nit])
        scopes = await self.supports.customers([context.nit])
        names = await self.masters.supplier_names(codes)
        today = self.calendar.today()
        lines: list[QuotationLine] = []
        for position, sku in enumerate(skus, start=start):
            supplier = supplier_of(sku)
            lines.append(
                rules.build_line(
                    position,
                    sku,
                    sold.get(sku),
                    item=added.get(sku),
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
                    line_id=uuid4(),
                )
            )
        cut = max((cost.cut_date for cost in costs.values() if cost.cut_date), default=None)
        return lines, cut

    async def _build(
        self, context: CustomerContext, months: int, added: Sequence[str] = ()
    ) -> tuple[list[QuotationLine], date | None]:
        """Lines of the sales window plus the SKUs the advisor had added."""

        sales = await self.warehouse.last_sales(context.nit, months)
        sold = {sale.sku for sale in sales}
        extra = [sku for sku in added if sku not in sold]
        if len(sales) + len(extra) > self.max_lines:
            raise InvalidInputError(
                f"El cliente tiene {len(sales) + len(extra)} referencias; "
                f"el máximo es {self.max_lines}"
            )
        items = await self.warehouse.items(extra) if extra else {}
        return await self._price(context, sales, items)

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

    async def create(
        self, principal: Principal, customer_nit: str, window_months: int
    ) -> QuotationDetail:
        """Build a quotation of a customer from its last sales in the window.

        Args:
            principal (Principal): The advisor.
            customer_nit (str): NIT (punctuation and a separated check digit are accepted).
            window_months (int): 6 or 12.

        Returns:
            QuotationDetail: The new draft with every SKU priced as it was last sold.
        """

        await self._require_manager(principal)
        if window_months not in vocabulary.WINDOWS:
            raise InvalidInputError("La ventana debe ser de 6 o 12 meses")
        nit = normalize_nit(customer_nit)
        context = await self._context(nit)
        lines, cut = await self._build(context, window_months)
        if not lines:
            # A customer without sales in the window is quoted with the SKUs the advisor adds.
            context = replace(context, warnings=(*context.warnings, "sin_ventas"))
        now = datetime.now(UTC)
        total, changed, below = rules.counts(lines)
        quotation = Quotation(
            id=uuid4(),
            customer_nit=nit,
            customer_name=context.name,
            channel_code=context.channel_code,
            channel=context.channel,
            price_list_id=context.price_list_id,
            price_list_name=context.price_list_name,
            price_list_customers=context.price_list_customers,
            billing_center_code=context.billing_center_code,
            salesperson_code=context.salesperson_code,
            salesperson_name=context.salesperson_name,
            window_months=window_months,
            status=vocabulary.DRAFT,
            activation_date=None,
            export_price_list_id=None,
            warnings=context.warnings,
            built_at=now,
            cost_cut_date=cut,
            max_points_below_target=self.policy.max_points_below_target,
            line_count=total,
            changed_count=changed,
            below_rule_count=below,
            version=1,
            created_by_id=principal.user_id,
            created_by=principal.email,
            created_at=now,
            updated_by=principal.email,
            updated_at=now,
        )
        return await self.repository.create(quotation, lines)

    # ------------------------------------------------------------ read

    async def get(self, principal: Principal, quotation_id: UUID) -> QuotationDetail:
        await self._require_manager(principal)
        detail = await self.repository.get(quotation_id)
        if detail is None:
            raise NotFoundError("Cotización no encontrada")
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
        await self._require_manager(principal)
        _page(page, size)
        if status is not None and status not in vocabulary.STATUSES:
            raise InvalidInputError("Estado desconocido")
        return await self.repository.list(
            created_by_id=principal.user_id if mine else None,
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
        total, changed, below = rules.counts(lines)
        return replace(
            quotation,
            status=vocabulary.DRAFT,
            line_count=total,
            changed_count=changed,
            below_rule_count=below,
            version=quotation.version + 1,
            updated_by=actor,
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _check_version(quotation: Quotation, version: int) -> None:
        if quotation.version != version:
            raise ConflictError("La cotización cambió desde que la abriste; recárgala")

    async def update_lines(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        changes: Sequence[LineChange],
    ) -> QuotationDetail:
        """Set or clear the advisor's prices. A price below the rule is kept and marked.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read (409 when it changed).
            changes (Sequence[LineChange]): Prices by SKU.

        Returns:
            QuotationDetail: The quotation, back to draft.
        """

        detail = await self.get(principal, quotation_id)
        self._check_version(detail.quotation, version)
        if not changes:
            raise InvalidInputError("No hay precios para guardar")
        by_sku = {line.sku: line for line in detail.lines}
        updated: dict[str, QuotationLine] = {}
        for change in changes:
            line = by_sku.get(change.sku)
            if line is None:
                raise InvalidInputError(f"La referencia {change.sku} no está en la cotización")
            updated[change.sku] = rules.reprice(line, _price(change.new_price, change.sku))
        lines = [updated.get(line.sku, line) for line in detail.lines]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(quotation, list(updated.values()), version)

    async def refresh(
        self, principal: Principal, quotation_id: UUID, version: int
    ) -> QuotationDetail:
        """Rebuild from the warehouse and the masters, keeping the advisor's prices by SKU."""

        detail = await self.get(principal, quotation_id)
        self._check_version(detail.quotation, version)
        context = await self._context(detail.quotation.customer_nit)
        added = [line.sku for line in detail.lines if line.origin == vocabulary.ADDED]
        lines, cut = await self._build(context, detail.quotation.window_months, added)
        if not lines:
            context = replace(context, warnings=(*context.warnings, "sin_ventas"))
        prices = {line.sku: line.new_price for line in detail.lines if line.new_price is not None}
        lines = [rules.reprice(line, prices.get(line.sku)) for line in lines]
        quotation = replace(
            self._touched(detail.quotation, lines, principal.email),
            customer_name=context.name,
            channel_code=context.channel_code,
            channel=context.channel,
            price_list_id=context.price_list_id,
            price_list_name=context.price_list_name,
            price_list_customers=context.price_list_customers,
            billing_center_code=context.billing_center_code,
            salesperson_code=context.salesperson_code,
            salesperson_name=context.salesperson_name,
            warnings=context.warnings,
            built_at=datetime.now(UTC),
            cost_cut_date=cut,
            max_points_below_target=self.policy.max_points_below_target,
        )
        return await self.repository.save(quotation, lines, version, replace_lines=True)

    async def add_lines(
        self, principal: Principal, quotation_id: UUID, version: int, skus: Sequence[str]
    ) -> QuotationDetail:
        """Add SKUs the customer has not bought, to quote them too.

        Each one is priced like the others (cost, target margin, supports, liquor tax) and has
        no last price. Unknown, inactive or repeated SKUs are refused, and nothing is added.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read.
            skus (Sequence[str]): JDH references.

        Returns:
            QuotationDetail: The quotation with the new lines at the end, back to draft.
        """

        detail = await self.get(principal, quotation_id)
        self._check_version(detail.quotation, version)
        wanted = list(dict.fromkeys(sku.strip() for sku in skus if sku and sku.strip()))
        if not wanted:
            raise InvalidInputError("Indica las referencias a agregar")
        present = {line.sku for line in detail.lines}
        repeated = [sku for sku in wanted if sku in present]
        if repeated:
            raise InvalidInputError(f"Ya están en la cotización: {', '.join(repeated[:10])}")
        if len(detail.lines) + len(wanted) > self.max_lines:
            raise InvalidInputError(f"La cotización admite máximo {self.max_lines} referencias")
        items = await self.warehouse.items(wanted)
        missing = [sku for sku in wanted if sku not in items]
        if missing:
            raise InvalidInputError(f"No existen en el maestro: {', '.join(missing[:10])}")
        inactive = [sku for sku in wanted if not items[sku].is_active]
        if inactive:
            raise InvalidInputError(f"Están inactivas en SIESA: {', '.join(inactive[:10])}")
        start = max((line.position for line in detail.lines), default=0) + 1
        added, _ = await self._price(
            self._stored_context(detail.quotation), [], {sku: items[sku] for sku in wanted}, start
        )
        lines = [*detail.lines, *added]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(quotation, lines, version, replace_lines=True)

    async def remove_line(
        self, principal: Principal, quotation_id: UUID, version: int, sku: str
    ) -> QuotationDetail:
        """Take out a SKU the advisor added. Sales-window SKUs stay: leave their price empty."""

        detail = await self.get(principal, quotation_id)
        self._check_version(detail.quotation, version)
        line = next((item for item in detail.lines if item.sku == sku), None)
        if line is None:
            raise NotFoundError(f"La referencia {sku} no está en la cotización")
        if line.origin != vocabulary.ADDED:
            raise InvalidInputError(
                "Solo se quitan referencias agregadas; para no actualizar una del histórico, "
                "deja vacío su precio nuevo"
            )
        lines = [item for item in detail.lines if item.sku != sku]
        quotation = self._touched(detail.quotation, lines, principal.email)
        return await self.repository.save(quotation, lines, version, replace_lines=True)

    async def generate(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        activation_date: date | None,
        price_list_id: str | None,
    ) -> QuotationDetail:
        """Close the update: every new price must meet the rule.

        Args:
            principal (Principal): The advisor.
            quotation_id (UUID): Quotation.
            version (int): Version the caller read.
            activation_date (date | None): When SIESA activates the prices (default
                tomorrow); never in the past.
            price_list_id (str | None): Target list (default the customer's).

        Returns:
            QuotationDetail: The quotation, ``generado``.
        """

        detail = await self.get(principal, quotation_id)
        self._check_version(detail.quotation, version)
        changed = [line for line in detail.lines if line.new_price is not None]
        if not changed:
            raise InvalidInputError("No hay precios nuevos para generar el formato")
        blocked = [line for line in changed if line.compliance != vocabulary.MEETS]
        if blocked:
            listed = ", ".join(
                f"{line.sku} ({vocabulary.COMPLIANCE_LABELS.get(line.compliance or '', '')})"
                for line in blocked[:10]
            )
            more = f" y {len(blocked) - 10} más" if len(blocked) > 10 else ""
            raise InvalidInputError(f"Hay precios que no cumplen la regla: {listed}{more}")
        today = self.calendar.today()
        activation = activation_date or today + timedelta(days=1)
        if activation < today:
            raise InvalidInputError("La fecha de activación no puede ser anterior a hoy")
        target = (price_list_id or "").strip() or None
        if target is not None and not _LIST_ID.fullmatch(target):
            raise InvalidInputError("La lista de precios es un código de 1 a 3 letras o números")
        for line in changed:
            if not line.unit:
                raise InvalidInputError(f"La referencia {line.sku} no tiene unidad de medida")
        now = datetime.now(UTC)
        quotation = replace(
            detail.quotation,
            status=vocabulary.GENERATED,
            activation_date=activation,
            export_price_list_id=target,
            version=detail.quotation.version + 1,
            updated_by=principal.email,
            updated_at=now,
            generated_at=now,
            generated_by=principal.email,
        )
        return await self.repository.save(quotation, [], version)

    async def delete(self, principal: Principal, quotation_id: UUID) -> None:
        """Delete a quotation: its creator or an admin."""

        detail = await self.get(principal, quotation_id)
        if detail.quotation.created_by_id != principal.user_id and not principal.is_platform_admin:
            raise ForbiddenError("Solo quien creó la cotización puede borrarla")
        await self.repository.delete(quotation_id)

    # ------------------------------------------------------------ export

    def _export(self, detail: QuotationDetail) -> PriceListExport:
        quotation = detail.quotation
        if quotation.status != vocabulary.GENERATED or quotation.activation_date is None:
            raise ConflictError("Genera el formato antes de descargarlo")
        list_id = quotation.export_price_list_id or quotation.price_list_id
        rows: list[ExportRow] = []
        taxes: list[ExportTaxRow] = []
        for line in detail.lines:
            if line.new_price is None:
                continue
            unit = line.unit or ""
            rows.append(
                ExportRow(list_id, line.sku, quotation.activation_date, unit, line.new_price)
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
        return PriceListExport(tuple(rows), tuple(taxes))

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
    if value is None:
        return None
    if not value.is_finite() or value <= 0:
        raise InvalidInputError(f"El precio de {sku} debe ser mayor que cero")
    if value >= MAX_PRICE:
        raise InvalidInputError(f"El precio de {sku} es demasiado alto")
    try:
        exponent = value.normalize().as_tuple().exponent
    except InvalidOperation as exc:  # pragma: no cover - finite values normalize
        raise InvalidInputError(f"Precio inválido para {sku}") from exc
    if isinstance(exponent, int) and exponent < -4:
        raise InvalidInputError(f"El precio de {sku} admite máximo 4 decimales")
    return value


__all__ = [
    "MAX_PAGE_SIZE",
    "ExportFile",
    "LineChange",
    "Permissions",
    "QuotationService",
    "normalize_nit",
]
