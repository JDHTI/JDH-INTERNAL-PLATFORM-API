"""Use cases for reading and curating the commercial masters."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    Customer,
    LegalSupplier,
    Page,
    Product,
    ProductRef,
    SupplierProductCode,
    clean_nit,
)
from app.modules.commercial_masters.domain.ports import CommercialMasterRepository, ManagerPolicy
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 200


def _term(value: str | None) -> str | None:
    return " ".join(value.split()) or None if value else None


def _bounds(page: int, size: int) -> None:
    if page < 1:
        raise InvalidInputError("La página debe ser mayor o igual a 1")
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise InvalidInputError(f"El tamaño debe estar entre 1 y {MAX_PAGE_SIZE}")


class CommercialMasterService:
    """Read commercial masters and let managers curate supplier margins and NITs."""

    def __init__(self, repository: CommercialMasterRepository, managers: ManagerPolicy) -> None:
        """Create the service.

        Args:
            repository (CommercialMasterRepository): Master persistence port.
            managers (ManagerPolicy): Decides who may edit supplier data.
        """

        self.repository = repository
        self.managers = managers

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
        list_margin_pct: Decimal | None = None,
        list_margin_notes: str | None = None,
        confirmed_nits: Sequence[str] | None = None,
        primary_nit: str | None = None,
    ) -> CommercialSupplier:
        """Set the list margin and confirm the legal NITs of a commercial supplier.

        Args:
            principal (Principal): The caller; must be a manager.
            code (str): Commercial supplier code.
            fields (frozenset[str]): Names sent by the client; omitted ones are untouched.
            list_margin_pct (Decimal | None): Default list margin as a fraction (0.18).
            list_margin_notes (str | None): Free-text exceptions to the margin.
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
        changes: dict[str, object] = {}
        if "list_margin_pct" in fields:
            if list_margin_pct is not None and not Decimal("0") <= list_margin_pct < Decimal("1"):
                raise InvalidInputError("El margen lista debe estar entre 0 y 1")
            changes["list_margin_pct"] = list_margin_pct
        if "list_margin_notes" in fields:
            changes["list_margin_notes"] = _term(list_margin_notes)
        nits = None
        if "confirmed_nits" in fields:
            nits = [nit for value in confirmed_nits or () if (nit := clean_nit(value))]
        primary = clean_nit(primary_nit) if "primary_nit" in fields else None
        if primary is not None and nits is not None and primary not in nits:
            raise InvalidInputError("El NIT principal debe estar entre los NIT confirmados")
        updated = await self.repository.update_commercial_supplier(
            code.strip(), changes, nits, primary
        )
        if updated is None:
            raise NotFoundError("Proveedor comercial no encontrado")
        return updated

    async def list_legal_suppliers(
        self, query: str | None, nit: str | None, is_active: bool | None, page: int, size: int
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
    ) -> Page[Product]:
        """Page products by text/SKU, brand, category and commercial supplier."""

        _bounds(page, size)
        return await self.repository.list_products(
            _term(query),
            _term(brand),
            _term(category),
            _term(commercial_supplier_code),
            is_active,
            page,
            size,
        )


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
            )
            for product in page.items
        ]

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        """Commercial supplier ids by code."""

        return await self.repository.commercial_suppliers_by_code(list(dict.fromkeys(codes)))

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]:
        """Commercial suppliers whose name contains the text."""

        return await self.repository.commercial_suppliers_by_name(query)

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        """``(code, name)`` by commercial supplier id."""

        return await self.repository.commercial_supplier_names(list(dict.fromkeys(ids)))

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]:
        """``{nit: legal name}`` of NITs that exist among the customers."""

        return await self.repository.existing_customer_nits(list(dict.fromkeys(nits)))

    async def set_list_margin(self, code: str, pct: Decimal | None, notes: str | None) -> bool:
        """Set a supplier's default list margin."""

        return await self.repository.set_list_margin(code, pct, notes)

    async def save_supplier_product_codes(
        self, rows: Sequence[SupplierProductCode]
    ) -> tuple[int, list[SupplierProductCode]]:
        """Upsert supplier SKUs; returns the count and the unresolved rows."""

        return await self.repository.upsert_supplier_product_codes(rows)
