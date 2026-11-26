"""What Nivel de servicio needs from the outside: the warehouse, its tables and the permissions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.service_level.domain.entities import (
    Classified,
    ClassRun,
    ClassValue,
    Group,
    LineFilters,
    LineRow,
    OrderLine,
    Reason,
    Totals,
)


class ModulePolicy(Protocol):
    """Who reads and who manages one module of the catalog."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class OrderLineSource(Protocol):
    """The lines of the fulfilled sales orders in the warehouse."""

    async def available(self) -> bool:
        """Whether the warehouse publishes the order lines yet."""
        ...

    async def lines(self, since: date, until: date) -> list[OrderLine]:
        """Every line of the orders fulfilled with an order date in the days."""
        ...


@dataclass(frozen=True)
class StoreResult:
    upserted: int
    removed: int
    voided: int


@dataclass(frozen=True)
class FilterChoices:
    """The values the dropdowns offer for a period: code → name."""

    cos: Mapping[str, str]
    warehouses: Mapping[str, str]
    suppliers: Mapping[str, str]
    sellers: Mapping[str, str]


class ServiceLevelRepository(Protocol):
    """The copied lines, their reasons, the reasons' catalog and the ABCD classes."""

    # --- reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]: ...

    async def get_reason(self, reason_id: UUID) -> Reason | None: ...

    async def create_reason(self, code: str, name: str, responsible: str) -> Reason: ...

    async def update_reason(
        self,
        reason_id: UUID,
        *,
        name: str | None,
        responsible: str | None,
        is_active: bool | None,
    ) -> Reason: ...

    async def responsibles(self) -> list[str]: ...

    # --- the copy of the warehouse ---

    async def has_lines(self) -> bool: ...

    async def store_lines(
        self, since: date, until: date, lines: Sequence[OrderLine], now: datetime
    ) -> StoreResult:
        """Upsert the lines read for the days; the days' lines not read again go away, or are
        voided when they carry a reason."""
        ...

    async def data_as_of(self) -> datetime | None: ...

    # --- reading ---

    async def page(
        self, filters: LineFilters, sort: str, descending: bool, page: int, size: int
    ) -> tuple[list[LineRow], int]: ...

    async def totals(self, filters: LineFilters) -> Totals: ...

    async def groups(
        self,
        filters: LineFilters,
        dimension: str,
        sort: str,
        descending: bool,
        limit: int | None,
        offset: int,
    ) -> tuple[list[Group], int]: ...

    async def daily(self, filters: LineFilters) -> list[tuple[date, Totals]]: ...

    async def choices(self, since: date, until: date) -> FilterChoices: ...

    # --- reasons of the lines ---

    async def lines_by_key(self, keys: Sequence[str]) -> dict[str, LineRow]: ...

    async def set_reasons(
        self,
        keys: Sequence[str],
        reason_id: UUID | None,
        note: str | None,
        actor: str,
        now: datetime,
    ) -> int: ...

    # --- ABCD ---

    async def class_values(self, kind: str, since: date, until: date) -> list[ClassValue]: ...

    async def replace_classes(
        self, classes: Mapping[str, Sequence[Classified]], run: ClassRun
    ) -> None: ...

    async def latest_class_run(self) -> ClassRun | None: ...

    async def stored_class_values(self, kind: str, co: str | None) -> list[Decimal]: ...


__all__ = [
    "FilterChoices",
    "ModulePolicy",
    "OrderLineSource",
    "ServiceLevelRepository",
    "StoreResult",
]
