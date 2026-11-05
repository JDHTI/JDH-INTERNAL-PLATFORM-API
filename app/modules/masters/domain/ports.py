"""Contracts of the master catalog: persistence, the warehouse and who sees what."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterRecordPage,
    MasterSpec,
    MasterSummary,
)

Row = Mapping[str, Any]


class MasterRepository(Protocol):
    """Read masters, their structure and their records."""

    async def list_masters(self) -> list[MasterSummary]:
        """Return active masters ordered by sort order and name."""
        ...

    async def get_master(self, slug: str) -> MasterDetail | None:
        """Return one master with its fields, or None."""
        ...

    async def list_records(
        self,
        slug: str,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> MasterRecordPage | None:
        """Return a page of records, or None when the master does not exist."""
        ...


class MasterSyncRepository(Protocol):
    """Write the records the warehouse sync reads."""

    async def list_definitions(self) -> list[MasterDetail]:
        """Active masters with their fields, in sort order."""
        ...

    async def upsert_records(
        self, master_id: UUID, rows: Sequence[Row], synced_at: datetime
    ) -> int:
        """Insert or update records by ``(master_id, source_key)``; return how many."""
        ...

    async def deactivate_unseen(self, master_id: UUID, synced_at: datetime) -> int:
        """Mark inactive the active records this sync did not see; return how many."""
        ...

    async def touch_definition(self, master_id: UUID, synced_at: datetime) -> None:
        """Record when the master was last synced."""
        ...


class MasterSource(Protocol):
    """Read a master from the data warehouse."""

    def records(self, spec: MasterSpec, batch_size: int) -> AsyncIterator[list[Row]]:
        """Pages of the master's source relation, ordered by its key."""
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
