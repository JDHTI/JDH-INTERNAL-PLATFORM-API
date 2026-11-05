"""Contracts of the master catalog: declarations, the warehouse, the cache and access."""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.masters.domain.entities import Freshness, MasterDetail, MasterSpec

Row = Mapping[str, Any]


class WarehouseUnavailable(Exception):
    """The DWH could not answer (down, timed out, or a view being re-created)."""


class MasterRepository(Protocol):
    """Read the masters declared in the platform (``master_definitions``)."""

    async def list_definitions(self) -> list[MasterDetail]:
        """Active masters with their fields, in sort order (counts unset)."""
        ...

    async def get_definition(self, slug: str) -> MasterDetail | None:
        """One active master with its fields, or ``None``."""
        ...


class MasterWarehouse(Protocol):
    """Read a master straight from its ``consumo`` view.

    Every method raises :class:`WarehouseUnavailable` when the DWH does not answer.
    """

    async def count(self, spec: MasterSpec, query: str | None, is_active: bool | None) -> int:
        """Rows matching the filters."""
        ...

    async def page(
        self,
        spec: MasterSpec,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> list[Row]:
        """Rows matching the filters, ordered by code, name and key."""
        ...

    async def freshness(
        self, sequences: Sequence[str], refresh: bool = False
    ) -> dict[str, Freshness]:
        """The state of these sequences; empty when monitoring cannot be read.

        Args:
            sequences (Sequence[str]): Sequence names.
            refresh (bool): Skip the short memo and ask the DWH now.
        """
        ...


class CachedValue(Protocol):
    """A value read from the DWH and the sequence run it came from."""

    version: UUID | None
    value: Any


class MasterCache(Protocol):
    """What the platform last read from the DWH, kept per request."""

    def get(self, key: Hashable) -> CachedValue | None:
        """The last value stored under the key, whatever its run."""
        ...

    def put(self, key: Hashable, version: UUID | None, value: Any) -> None:
        """Store a value read while the sequence's last run was ``version``."""
        ...


class MasterAccess(Protocol):
    """Which masters a person may read, decided by the areas where the module is on."""

    async def visible_masters(self, principal: Principal) -> frozenset[str] | None:
        """Slugs the person may read; ``None`` means every master.

        An empty set means none (the person reads ``maestros`` nowhere, or the areas
        where they do have no master switched on).
        """
        ...

    async def area_masters(self, slug: str) -> frozenset[str] | None:
        """Masters switched on in one area; ``None`` is every master, empty when off."""
        ...

    async def can_see_private(self, principal: Principal) -> bool:
        """Whether personal data fields (``is_pii``) are shown to the person."""
        ...
