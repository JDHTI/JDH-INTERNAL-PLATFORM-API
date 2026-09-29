"""Persistence contract for the master catalog."""

from __future__ import annotations

from typing import Protocol

from app.modules.masters.domain.entities import MasterDetail, MasterRecordPage, MasterSummary


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
