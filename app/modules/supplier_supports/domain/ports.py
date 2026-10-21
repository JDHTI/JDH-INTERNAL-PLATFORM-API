"""Ports of the supplier supports module."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.modules.commercial_masters.domain.entities import CommercialSupplier, ProductRef
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    Support,
    SupportData,
    SupportRow,
)
from app.modules.supplier_supports.domain.filters import SearchSpec


class SupportRepository(Protocol):
    """Persistence of supports and their tiers."""

    async def create(
        self, support_id: UUID, data: SupportData, actor: str, now: datetime
    ) -> Support: ...

    async def get(self, support_id: UUID) -> Support | None: ...

    async def get_row(self, support_id: UUID) -> SupportRow | None: ...

    async def update(
        self, support_id: UUID, data: SupportData, expected_version: int, actor: str, now: datetime
    ) -> Support: ...

    async def void(
        self, support_id: UUID, reason: str, expected_version: int, actor: str, now: datetime
    ) -> Support: ...

    async def search(self, spec: SearchSpec) -> tuple[list[SupportRow], int]: ...

    async def get_by_source_key(self, source_key: str) -> Support | None: ...

    async def existing_source_keys(self, keys: Sequence[str]) -> set[str]: ...

    async def conflicts(self, support_id: UUID) -> list[Conflict]: ...

    async def scope_values(self) -> dict[str, list[str]]: ...


class MasterLookup(Protocol):
    """The masters a support points to."""

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]: ...

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]: ...

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]: ...

    async def commercial_supplier_names(
        self, ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, str]]: ...

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]: ...


class ManagerPolicy(Protocol):
    """Decides who may create, edit and void supports."""

    async def is_manager(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    """Business calendar."""

    def today(self) -> date: ...
