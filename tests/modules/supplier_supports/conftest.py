"""In-memory ports for the supplier supports service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.modules.commercial_masters.domain.entities import CommercialSupplier, ProductRef
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    Support,
    SupportData,
    SupportRow,
)
from app.modules.supplier_supports.domain.filters import SearchSpec

TODAY = date(2026, 10, 9)
FAMILIA_ID = uuid4()
UNILEVER_ID = uuid4()
SKU_1128 = ProductRef(uuid4(), "1128", "Ph Jumbo Blanco HD 4x250mt", "FAMILIA", "233", True)
SKU_8749 = ProductRef(uuid4(), "8749", "Arroz Diana x 1Kg", "DIANA", "546", True)
# Pack factor and the warehouse's average unit cost, to infer the unit of a workbook cost.
SKU_3474 = ProductRef(
    uuid4(), "3474", "Maizena x720g", "MAIZENA", "399", True, Decimal("24"), Decimal("13782.70")
)
SKU_1001 = ProductRef(
    uuid4(),
    "1001",
    "Salsa de Tomate Fruco Gx4350g",
    "FRUCO",
    "399",
    True,
    Decimal("4"),
    Decimal("60245.58"),
)


class FakeLookup:
    """Suppliers, products and customers that exist in the masters."""

    def __init__(self) -> None:
        self.suppliers = {"233": FAMILIA_ID, "399": UNILEVER_ID}
        self.names = {FAMILIA_ID: ("233", "FAMILIA SANCELA"), UNILEVER_ID: ("399", "UNILEVER")}
        self.products = {
            "1128": SKU_1128,
            "8749": SKU_8749,
            "3474": SKU_3474,
            "1001": SKU_1001,
        }
        self.customers = {"800116299": "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"}

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]:
        return {sku: self.products[sku] for sku in skus if sku in self.products}

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        return {code: self.suppliers[code] for code in codes if code in self.suppliers}

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]:
        return []

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        return {item: self.names[item] for item in ids if item in self.names}

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]:
        return {nit: self.customers[nit] for nit in nits if nit in self.customers}


class FakeSupportRepository:
    """Keeps supports in a dict, with the version check of the real repository."""

    def __init__(self) -> None:
        self.items: dict[UUID, Support] = {}
        self.last_search: SearchSpec | None = None

    async def create(self, support_id: UUID, data: SupportData, actor: str, now: datetime):
        if data.source_key and any(
            item.data.source_key == data.source_key for item in self.items.values()
        ):
            raise ConflictError("duplicado")
        support = Support(
            id=support_id,
            data=data,
            void_reason=None,
            voided_at=None,
            created_by=actor,
            updated_by=actor,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.items[support_id] = support
        return support

    async def get(self, support_id: UUID) -> Support | None:
        return self.items.get(support_id)

    async def get_row(self, support_id: UUID) -> SupportRow | None:
        support = self.items.get(support_id)
        return None if support is None else SupportRow(support=support, supplier_code="233")

    async def update(self, support_id, data, expected_version, actor, now):
        current = self.items.get(support_id)
        if current is None:
            raise NotFoundError("Apoyo no encontrado")
        if current.version != expected_version:
            raise ConflictError("versión")
        saved = replace(
            current, data=data, version=current.version + 1, updated_by=actor, updated_at=now
        )
        self.items[support_id] = saved
        return saved

    async def void(self, support_id, reason, expected_version, actor, now):
        current = self.items[support_id]
        saved = replace(
            current,
            data=replace(current.data, status="anulado"),
            void_reason=reason,
            voided_at=now,
            version=current.version + 1,
            updated_by=actor,
        )
        self.items[support_id] = saved
        return saved

    async def search(self, spec: SearchSpec):
        self.last_search = spec
        rows = [
            SupportRow(support=item, supplier_code="233")
            for item in self.items.values()
            if spec.include_voided or item.data.status != "anulado"
        ]
        return rows, len(rows)

    async def get_by_source_key(self, source_key: str) -> Support | None:
        return next(
            (item for item in self.items.values() if item.data.source_key == source_key), None
        )

    async def existing_source_keys(self, keys: Sequence[str]) -> set[str]:
        return {
            item.data.source_key
            for item in self.items.values()
            if item.data.source_key in keys and item.data.source_key
        }

    async def conflicts(self, support_id: UUID) -> list[Conflict]:
        return []

    async def scope_values(self) -> dict[str, list[str]]:
        return {"regionals": ["ANTIOQUIA"], "business_units": [], "channels": [], "programs": []}


class FakeManagers:
    """Admins and a fixed set of e-mails manage supports."""

    def __init__(self, emails: Sequence[str] = ()) -> None:
        self.emails = set(emails)

    async def is_manager(self, principal: Principal) -> bool:
        return principal.is_platform_admin or principal.email in self.emails


class FixedCalendar:
    def today(self) -> date:
        return TODAY


def person(email: str = "ana@jdh.com.co", admin: bool = False) -> Principal:
    return Principal(user_id=uuid4(), email=email, display_name=email, is_platform_admin=admin)


@pytest.fixture
def repo() -> FakeSupportRepository:
    return FakeSupportRepository()


@pytest.fixture
def lookup() -> FakeLookup:
    return FakeLookup()


@pytest.fixture
def service(repo, lookup) -> SupplierSupportService:
    return SupplierSupportService(
        repository=repo,
        masters=lookup,
        managers=FakeManagers(["comercial@jdh.com.co"]),
        calendar=FixedCalendar(),
    )


@pytest.fixture
def manager() -> Principal:
    return person("comercial@jdh.com.co")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 9, 12, tzinfo=UTC)
