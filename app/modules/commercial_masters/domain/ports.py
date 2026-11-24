"""Ports of the commercial masters module."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.commercial_masters.domain.employee_prices import (
    EmployeePriceProduct,
    WarehouseCost,
)
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    Customer,
    CustomerRef,
    LegalSupplier,
    Page,
    Product,
    ProductFilterOptions,
    ProductRef,
    SupplierProductCode,
    TargetMargin,
)
from app.modules.commercial_masters.domain.price_increases import Placement
from app.modules.identity.domain.principal import Principal

Row = Mapping[str, Any]


class ManagerPolicy(Protocol):
    """Decides who may create and edit commercial data (see ``AreaManagerPolicy``)."""

    async def is_manager(self, principal: Principal) -> bool: ...


class WarehouseCostSource(Protocol):
    """Average inventory costs by warehouse, from the data warehouse."""

    async def warehouse_costs(self, warehouse: str) -> list[WarehouseCost]: ...


class PlacementSource(Protocol):
    """Where each reference is set up (operation center and installation), from the data
    warehouse's inventory."""

    async def placements(self) -> list[Placement]:
        """Raises ``DependencyUnavailableError`` when the warehouse does not answer."""
        ...


class WarehouseSource(Protocol):
    """Read-only access to the data warehouse ``consumo`` views."""

    def suppliers(self, batch_size: int) -> AsyncIterator[list[Row]]: ...

    def customers(self, batch_size: int) -> AsyncIterator[list[Row]]: ...

    def products(self, batch_size: int) -> AsyncIterator[list[Row]]: ...

    def product_suppliers(self, batch_size: int) -> AsyncIterator[list[Row]]: ...

    async def unit_costs(self) -> list[Row]: ...

    async def commercial_suppliers(self) -> list[Row]: ...

    async def commercial_supplier_nits(self, per_supplier: int) -> list[Row]: ...


class MasterSyncRepository(Protocol):
    """Write side used by the DWH sync."""

    async def upsert_suppliers(self, rows: Sequence[Row], synced_at: datetime) -> int: ...

    async def upsert_customers(self, rows: Sequence[Row], synced_at: datetime) -> int: ...

    async def upsert_products(self, rows: Sequence[Row], synced_at: datetime) -> int: ...

    async def upsert_product_suppliers(self, rows: Sequence[Row], synced_at: datetime) -> int: ...

    async def upsert_commercial_suppliers(
        self, rows: Sequence[Row], synced_at: datetime
    ) -> int: ...

    async def replace_commercial_supplier_nits(self, rows: Sequence[Row]) -> int: ...

    async def update_unit_costs(self, rows: Sequence[Row]) -> int: ...

    async def deactivate_unseen(self, table: str, synced_at: datetime) -> int: ...

    async def touch_master_definition(self, slug: str, synced_at: datetime) -> None: ...


class CommercialMasterRepository(Protocol):
    """Read side of the masters and the small edits they allow."""

    async def list_commercial_suppliers(
        self, query: str | None, is_active: bool | None, page: int, size: int
    ) -> Page[CommercialSupplier]: ...

    async def get_commercial_supplier(self, code: str) -> CommercialSupplier | None: ...

    async def update_commercial_supplier(
        self,
        code: str,
        fields: Mapping[str, Any],
        confirmed_nits: Sequence[str] | None,
        primary_nit: str | None,
    ) -> CommercialSupplier | None: ...

    async def list_legal_suppliers(
        self,
        query: str | None,
        nit: str | None,
        is_active: bool | None,
        page: int,
        size: int,
    ) -> Page[LegalSupplier]: ...

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
    ) -> Page[Customer]: ...

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
        *,
        has_consumption_tax: bool | None = None,
    ) -> Page[Product]: ...

    async def product_options(self) -> ProductFilterOptions: ...

    async def get_product(self, sku: str) -> Product | None: ...

    async def update_product(
        self, sku: str, changes: Mapping[str, Any], actor: str, now: datetime
    ) -> Product | None: ...

    async def margins_by_sku(self, skus: Sequence[str]) -> dict[str, Product]: ...

    async def bulk_update_products(
        self, rows: Sequence[Mapping[str, Any]], actor: str, now: datetime
    ) -> int: ...

    async def products_with_margins(self) -> list[Product]: ...

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]: ...

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]: ...

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]: ...

    async def commercial_supplier_names(
        self, ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, str]]: ...

    async def existing_customer_nits(self, nits: Sequence[str]) -> dict[str, str]: ...

    async def customers_by_nit(self, nits: Sequence[str]) -> dict[str, CustomerRef]: ...

    async def target_margins(
        self, nit: str | None, skus: Sequence[str]
    ) -> dict[str, TargetMargin]: ...

    async def upsert_supplier_product_codes(
        self, rows: Sequence[SupplierProductCode]
    ) -> tuple[int, list[SupplierProductCode]]: ...

    async def employee_price_products(self) -> list[EmployeePriceProduct]: ...
