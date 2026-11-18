"""Use cases for reading and curating the commercial masters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    Customer,
    CustomerRef,
    LegalSupplier,
    Page,
    Product,
    ProductRef,
    SupplierProductCode,
    clean_nit,
)
from app.modules.commercial_masters.domain.margins import (
    EMPLOYEE_MARGIN_FIELD,
    EMPLOYEE_MARGIN_LABEL,
    LIQUOR_TAX_FIELDS,
    MARGIN_FIELDS,
    MARGIN_LABELS,
    MarginChange,
    MarginIssue,
    MarginReport,
    MarginRow,
)
from app.modules.commercial_masters.domain.ports import CommercialMasterRepository, ManagerPolicy
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 200
MAX_MARGIN_ROWS = 60_000
MAX_REPORTED_CHANGES = 500
IMPORT_ACTOR = "importacion"
_ONE = Decimal(1)
_SIX_PLACES = Decimal("0.000001")
_FOUR_PLACES = Decimal("0.0001")
# Fields a person may change on a product. State and supplier come from the warehouse and
# are not editable; every other warehouse field is overwritten by the next sync.
EDITABLE_PRODUCT_FIELDS = frozenset(
    {
        "description",
        "supplier_sku",
        "manufacturer",
        "brand",
        "category",
        "subcategory",
        "product_line",
        "product_subline",
        "product_group",
        "pack_factor",
        "inventory_unit",
        "main_barcode",
        "margin_notes",
        "liquor_tax",
        "liquor_tax_coast",
        "liquor_tax_group",
        EMPLOYEE_MARGIN_FIELD,
        *MARGIN_FIELDS,
    }
)
_MARGIN_LABEL = {**MARGIN_LABELS, EMPLOYEE_MARGIN_FIELD: EMPLOYEE_MARGIN_LABEL}
_TEXT_PRODUCT_FIELDS = frozenset(
    {
        "description",
        "supplier_sku",
        "manufacturer",
        "brand",
        "category",
        "subcategory",
        "product_line",
        "product_subline",
        "product_group",
        "inventory_unit",
        "main_barcode",
        "margin_notes",
        "liquor_tax_group",
    }
)
# ProductMargins attribute of each margin column.
_MARGIN_ATTRIBUTE = {
    "margin_list": "list",
    "margin_wholesale": "wholesale",
    "margin_mass_food": "mass_food",
    "margin_market": "market",
    "margin_salsamentarias": "salsamentarias",
}


@dataclass(frozen=True)
class LiquorTax:
    """Liquor consumption tax per unit of a product (``IMPTOLIC``)."""

    national: Decimal
    coast: Decimal
    group: str


class MarginReader(Protocol):
    """Reads an uploaded spreadsheet into margin rows."""

    def read(self, filename: str, content: bytes, sheet: str | None = None) -> list[MarginRow]: ...


def _same_margin(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return left.quantize(_SIX_PLACES) == right.quantize(_SIX_PLACES)


def _same_tax(left: Decimal | str | None, right: Decimal | str | None) -> bool:
    if isinstance(left, Decimal) and isinstance(right, Decimal):
        return left.quantize(_FOUR_PLACES) == right.quantize(_FOUR_PLACES)
    return left == right


def _term(value: str | None) -> str | None:
    return " ".join(value.split()) or None if value else None


def _bounds(page: int, size: int) -> None:
    if page < 1:
        raise InvalidInputError("La página debe ser mayor o igual a 1")
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise InvalidInputError(f"El tamaño debe estar entre 1 y {MAX_PAGE_SIZE}")


class CommercialMasterService:
    """Read commercial masters; managers curate supplier NITs, products and their margins."""

    def __init__(
        self,
        repository: CommercialMasterRepository,
        managers: ManagerPolicy,
        margin_reader: MarginReader | None = None,
        max_upload_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        """Create the service.

        Args:
            repository (CommercialMasterRepository): Master persistence port.
            managers (ManagerPolicy): Decides who may edit supplier and product data.
            margin_reader (MarginReader | None): Reads uploaded margin files.
            max_upload_bytes (int): Largest margin file accepted.
        """

        self.repository = repository
        self.managers = managers
        self.margin_reader = margin_reader
        self.max_upload_bytes = max_upload_bytes

    async def list_commercial_suppliers(
        self, query: str | None, is_active: bool | None, page: int, size: int
    ) -> Page[CommercialSupplier]:
        """Page commercial suppliers."""

        _bounds(page, size)
        return await self.repository.list_commercial_suppliers(_term(query), is_active, page, size)

    async def get_commercial_supplier(self, code: str) -> CommercialSupplier:
        """One commercial supplier with its NITs, or not found."""

        supplier = await self.repository.get_commercial_supplier(code.strip())
        if supplier is None:
            raise NotFoundError("Proveedor comercial no encontrado")
        return supplier

    async def update_commercial_supplier(
        self,
        principal: Principal,
        code: str,
        *,
        fields: frozenset[str],
        confirmed_nits: Sequence[str] | None = None,
        primary_nit: str | None = None,
    ) -> CommercialSupplier:
        """Confirm the legal NITs of a commercial supplier.

        Args:
            principal (Principal): The caller; must be a manager.
            code (str): Commercial supplier code.
            fields (frozenset[str]): Names sent by the client; omitted ones are untouched.
            confirmed_nits (Sequence[str] | None): The complete set of confirmed NITs.
            primary_nit (str | None): The primary NIT among them.

        Returns:
            CommercialSupplier: The updated supplier.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If a value is out of range.
            NotFoundError: If the supplier does not exist.
        """

        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para editar proveedores")
        nits = None
        if "confirmed_nits" in fields:
            nits = [nit for value in confirmed_nits or () if (nit := clean_nit(value))]
        primary = clean_nit(primary_nit) if "primary_nit" in fields else None
        if primary is not None and nits is not None and primary not in nits:
            raise InvalidInputError("El NIT principal debe estar entre los NIT confirmados")
        updated = await self.repository.update_commercial_supplier(code.strip(), {}, nits, primary)
        if updated is None:
            raise NotFoundError("Proveedor comercial no encontrado")
        return updated

    async def list_legal_suppliers(
        self,
        query: str | None,
        nit: str | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> Page[LegalSupplier]:
        """Page legal suppliers by name or NIT."""

        _bounds(page, size)
        return await self.repository.list_legal_suppliers(
            _term(query), clean_nit(nit), is_active, page, size
        )

    async def list_customers(
        self,
        query: str | None,
        nit: str | None,
        channel: str | None,
        business_unit: str | None,
        business_group: str | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> Page[Customer]:
        """Page customers by text, NIT, channel, business unit and group."""

        _bounds(page, size)
        return await self.repository.list_customers(
            _term(query),
            clean_nit(nit),
            _term(channel),
            _term(business_unit),
            _term(business_group),
            is_active,
            page,
            size,
        )

    async def list_products(
        self,
        query: str | None,
        brand: str | None,
        category: str | None,
        commercial_supplier_code: str | None,
        is_active: bool | None,
        page: int,
        size: int,
        has_margin: bool | None = None,
    ) -> Page[Product]:
        """Page products by text/SKU, brand, category, supplier, active flag and margin."""

        _bounds(page, size)
        return await self.repository.list_products(
            _term(query),
            _term(brand),
            _term(category),
            _term(commercial_supplier_code),
            is_active,
            page,
            size,
            has_margin,
        )

    # --------------------------------------------------------------- products

    async def _require_manager(self, principal: Principal, what: str) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError(f"No tienes permiso para {what}")

    async def get_product(self, sku: str) -> Product:
        """One product with its margins, or not found."""

        product = await self.repository.get_product(sku.strip())
        if product is None:
            raise NotFoundError("Producto no encontrado")
        return product

    async def update_product(
        self, principal: Principal, sku: str, changes: Mapping[str, Any]
    ) -> Product:
        """Edit a product and its margins by hand.

        Margins and the liquor tax are never written by the warehouse sync, so they stay.
        Any other field is overwritten by the next sync of the products.

        Args:
            principal (Principal): The caller; must be a manager.
            sku (str): JDH reference.
            changes (Mapping[str, Any]): Only the fields to change; ``None`` clears one.

        Returns:
            Product: The updated product.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If a field is not editable or a value is out of range.
            NotFoundError: If the product does not exist.
        """

        await self._require_manager(principal, "editar productos")
        unknown = sorted(set(changes) - EDITABLE_PRODUCT_FIELDS)
        if unknown:
            raise InvalidInputError(f"Campos no editables: {', '.join(unknown)}")
        values: dict[str, Any] = {}
        for name, value in changes.items():
            if name in _MARGIN_LABEL:
                if value is not None and not Decimal(0) < value < _ONE:
                    raise InvalidInputError(
                        f"{_MARGIN_LABEL[name]} debe estar entre 0 y 1 (0.18 = 18 %); "
                        "envía null para dejarlo sin margen"
                    )
            elif name in _TEXT_PRODUCT_FIELDS:
                value = _term(value)
            elif value is not None and value < 0:
                raise InvalidInputError(f"{name} no puede ser negativo")
            values[name] = value
        if "description" in values and values["description"] is None:
            raise InvalidInputError("El producto necesita descripción")
        if not values:
            return await self.get_product(sku)
        product = await self.repository.update_product(
            sku.strip(), values, principal.email, datetime.now(UTC)
        )
        if product is None:
            raise NotFoundError("Producto no encontrado")
        return product

    async def set_product_margins(
        self,
        principal: Principal,
        sku: str,
        margins: Mapping[str, Decimal],
        notes: str | None,
        liquor_tax: LiquorTax | None,
        employee_margin: Decimal | None = None,
    ) -> Product:
        """Set every margin of one product by hand, without a file.

        All five channel margins are required. The employee margin is optional: ``None``
        keeps the product's. The liquor tax is stored only for the products that
        pay it: a product that already pays it must keep the three values (Nacional, Costa
        and the tax group); one that does not may start paying it.

        Args:
            principal (Principal): The caller; must be a manager.
            sku (str): JDH reference.
            margins (Mapping[str, Decimal]): One fraction per margin column (0.18 = 18 %).
            notes (str | None): Notes of the margin; ``None`` or blank clears them.
            liquor_tax (LiquorTax | None): The liquor tax, or ``None`` when it does not apply.
            employee_margin (Decimal | None): Margin of the employee price list, or ``None``
                to keep it.

        Returns:
            Product: The updated product.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If a margin is missing or out of range, or a product that pays
                the liquor tax comes without it.
            NotFoundError: If the product does not exist.
        """

        await self._require_manager(principal, "editar márgenes")
        missing = [MARGIN_LABELS[name] for name in MARGIN_FIELDS if margins.get(name) is None]
        if missing:
            raise InvalidInputError(f"Faltan márgenes: {', '.join(missing)}")
        unknown = sorted(set(margins) - set(MARGIN_FIELDS))
        if unknown:
            raise InvalidInputError(f"Márgenes desconocidos: {', '.join(unknown)}")
        values: dict[str, Any] = {}
        for name in MARGIN_FIELDS:
            value = margins[name]
            if not Decimal(0) < value < _ONE:
                raise InvalidInputError(
                    f"{MARGIN_LABELS[name]} debe estar entre 0 y 1 (0.18 = 18 %)"
                )
            # Half up, as PostgreSQL rounds a numeric column.
            values[name] = value.quantize(_SIX_PLACES, ROUND_HALF_UP)
        if employee_margin is not None:
            if not Decimal(0) < employee_margin < _ONE:
                raise InvalidInputError(
                    f"{EMPLOYEE_MARGIN_LABEL} debe estar entre 0 y 1 (0.13 = 13 %)"
                )
            values[EMPLOYEE_MARGIN_FIELD] = employee_margin.quantize(_SIX_PLACES, ROUND_HALF_UP)
        values["margin_notes"] = _term(notes)
        product = await self.get_product(sku)
        pays_liquor_tax = any(getattr(product, name) is not None for name in LIQUOR_TAX_FIELDS)
        if liquor_tax is None:
            if pays_liquor_tax:
                raise InvalidInputError(
                    "Este producto paga impuesto licor: envía Nacional, Costa y el grupo impositivo"
                )
        else:
            group = _term(liquor_tax.group)
            if group is None:
                raise InvalidInputError("Falta el grupo impositivo del impuesto licor")
            if liquor_tax.national < 0 or liquor_tax.coast < 0:
                raise InvalidInputError("El impuesto licor no puede ser negativo")
            values.update(
                liquor_tax=liquor_tax.national.quantize(_FOUR_PLACES, ROUND_HALF_UP),
                liquor_tax_coast=liquor_tax.coast.quantize(_FOUR_PLACES, ROUND_HALF_UP),
                liquor_tax_group=group,
            )
        updated = await self.repository.update_product(
            product.sku, values, principal.email, datetime.now(UTC)
        )
        if updated is None:
            raise NotFoundError("Producto no encontrado")
        return updated

    async def update_margins(
        self,
        principal: Principal,
        filename: str | None,
        content: bytes,
        *,
        apply: bool,
        sheet: str | None = None,
    ) -> MarginReport:
        """Update product margins from a file; a simulation unless ``apply``.

        Args:
            principal (Principal): The caller; must be a manager.
            filename (str | None): Name of the uploaded file (``.xlsx`` or ``.csv``).
            content (bytes): The file.
            apply (bool): Write the changes; otherwise only report them.
            sheet (str | None): Sheet to read; by default the margin master's own.

        Returns:
            MarginReport: What changed, what did not match and what was invalid.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If the file is empty, too big or has no margin columns.
        """

        await self._require_manager(principal, "actualizar márgenes")
        if self.margin_reader is None:
            raise InvalidInputError("La lectura de archivos de márgenes no está disponible")
        if not filename or not filename.strip():
            raise InvalidInputError("El archivo no tiene nombre")
        if not content:
            raise InvalidInputError("El archivo está vacío")
        if len(content) > self.max_upload_bytes:
            raise InvalidInputError(
                f"El archivo supera el máximo de {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        rows = self.margin_reader.read(filename.strip(), content, sheet)
        return await self.apply_margin_rows(rows, principal.email, apply=apply)

    async def apply_margin_rows(
        self, rows: Sequence[MarginRow], actor: str, *, apply: bool
    ) -> MarginReport:
        """Compare margin rows with the products and write the differences.

        Blank cells leave a margin as it is, ``0`` clears it, and a SKU that is not in the
        masters is reported. A row with an unreadable cell is skipped whole. When a SKU
        repeats, its last row wins.
        """

        if not rows:
            raise InvalidInputError("El archivo no tiene filas con referencia")
        if len(rows) > MAX_MARGIN_ROWS:
            raise InvalidInputError(f"El archivo supera el máximo de {MAX_MARGIN_ROWS} filas")
        by_sku = {row.sku: row for row in rows}
        duplicated = len(rows) - len(by_sku)
        products = await self.repository.margins_by_sku(list(by_sku))
        issues: list[MarginIssue] = []
        unknown: list[str] = []
        changes: list[MarginChange] = []
        updates: list[dict[str, Any]] = []
        unchanged = 0
        for sku, row in by_sku.items():
            if row.problems:
                issues.extend(MarginIssue(row.row_number, sku, item) for item in row.problems)
                continue
            product = products.get(sku)
            if product is None:
                unknown.append(sku)
                continue
            current = product.margins
            diff: dict[str, tuple[Decimal | None, Decimal | None]] = {}
            merged: dict[str, Any] = {"sku": sku}
            for name in MARGIN_FIELDS:
                before = getattr(current, _MARGIN_ATTRIBUTE[name])
                after = row.values.get(name, before) if name in row.values else before
                merged[name] = after
                if name in row.values and not _same_margin(before, after):
                    diff[name] = (before, after)
            notes_change = None
            merged["margin_notes"] = current.notes
            if row.has_notes and row.notes != current.notes:
                notes_change = (current.notes, row.notes)
                merged["margin_notes"] = row.notes
            tax_diff: dict[str, tuple[Any, Any]] = {}
            for name in LIQUOR_TAX_FIELDS:
                before = getattr(product, name)
                after = row.taxes.get(name, before)
                merged[name] = after
                if name in row.taxes and not _same_tax(before, after):
                    tax_diff[name] = (before, after)
            if not diff and notes_change is None and not tax_diff:
                unchanged += 1
                continue
            changes.append(MarginChange(sku, product.description, diff, notes_change, tax_diff))
            updates.append(merged)
        if apply and updates:
            await self.repository.bulk_update_products(updates, actor, datetime.now(UTC))
        return MarginReport(
            applied=apply,
            rows_read=len(rows),
            updated=len(updates),
            unchanged=unchanged,
            duplicated=duplicated,
            unknown_skus=tuple(unknown),
            issues=tuple(issues),
            changes=tuple(changes[:MAX_REPORTED_CHANGES]),
            changes_truncated=len(changes) > MAX_REPORTED_CHANGES,
        )

    async def products_with_margins(self) -> list[Product]:
        """Every product with its margins, active first (to export the template)."""

        return await self.repository.products_with_margins()


class MasterLookup:
    """Resolve the masters a support points to; used by the supports module."""

    def __init__(self, repository: CommercialMasterRepository) -> None:
        """Create the lookup.

        Args:
            repository (CommercialMasterRepository): Master persistence port.
        """

        self.repository = repository

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]:
        """Products by exact SKU."""

        return await self.repository.products_by_sku(list(dict.fromkeys(skus)))

    async def search_products(self, query: str, limit: int) -> list[ProductRef]:
        """Active products whose SKU is the text or whose description contains it."""

        page = await self.repository.list_products(
            _term(query), None, None, None, True, 1, max(1, min(limit, 100))
        )
        return [
            ProductRef(
                id=product.id,
                sku=product.sku,
                description=product.description,
                brand=product.brand,
                commercial_supplier_code=product.commercial_supplier_code,
                is_active=product.is_active,
                supplier_sku=product.supplier_sku,
            )
            for product in page.items
        ]

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        """Commercial supplier ids by code."""

        return await self.repository.commercial_suppliers_by_code(list(dict.fromkeys(codes)))

    async def products_by_supplier_sku(
        self, supplier_id: UUID, supplier_skus: Sequence[str]
    ) -> dict[str, list[ProductRef]]:
        """JDH products of a supplier's own SKUs (keys upper-cased)."""

        return await self.repository.products_by_supplier_sku(supplier_id, supplier_skus)

    async def business_group_members(self, groups: Sequence[str]) -> dict[str, list[tuple]]:
        """Active customers ``(nit, name)`` of each business group."""

        return await self.repository.business_group_members(groups)

    async def business_groups(self, query: str, limit: int = 20) -> list[tuple[str, int]]:
        """Business groups by name, with their active NITs."""

        return await self.repository.business_groups(query, max(1, min(limit, 50)))

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]:
        """Commercial suppliers whose name contains the text."""

        return await self.repository.commercial_suppliers_by_name(query)

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        """``(code, name)`` by commercial supplier id."""

        return await self.repository.commercial_supplier_names(list(dict.fromkeys(ids)))

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]:
        """``{nit: legal name}`` of NITs that exist among the customers."""

        return await self.repository.existing_customer_nits(list(dict.fromkeys(nits)))

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerRef]:
        """Name, business group and branches of the NITs that exist among the customers."""

        return await self.repository.customers_by_nit(list(dict.fromkeys(nits)))

    async def save_supplier_product_codes(
        self, rows: Sequence[SupplierProductCode]
    ) -> tuple[int, list[SupplierProductCode]]:
        """Upsert supplier SKUs; returns the count and the unresolved rows."""

        return await self.repository.upsert_supplier_product_codes(rows)
