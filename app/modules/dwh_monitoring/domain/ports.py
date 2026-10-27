"""Contracts the monitoring use cases depend on."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from app.modules.dwh_monitoring.domain.entities import LoadCycle
from app.modules.dwh_monitoring.domain.views import ViewSpec

Row = Mapping[str, Any]


class MonitoringSource(Protocol):
    """Read-only access to the warehouse ``monitoreo`` views."""

    async def check(self) -> None:
        """Fail fast when the warehouse cannot be reached."""

    async def read_all(self, view: ViewSpec) -> list[Row]:
        """Every row of a small view."""

    def read_since(
        self, view: ViewSpec, since: datetime, batch_size: int
    ) -> AsyncIterator[list[Row]]:
        """Pages of an incremental view with ``inicio >= since``, oldest first."""

    async def read_keys(self, view: ViewSpec, keys: Sequence[Any]) -> list[Row]:
        """Rows of an incremental view by their key."""


class MonitoringCopyRepository(Protocol):
    """The platform's local copy of the views, and the log of load cycles."""

    async def replace(self, view: ViewSpec, rows: Sequence[Row], loaded_at: datetime) -> int:
        """Swap the whole local copy of a snapshot view in one transaction."""

    async def append(self, view: ViewSpec, rows: Sequence[Row], loaded_at: datetime) -> int:
        """Add rows to a history view, ignoring those already stored."""

    async def upsert(self, view: ViewSpec, rows: Sequence[Row], loaded_at: datetime) -> int:
        """Insert or update rows of an incremental view by key."""

    async def latest_start(self, view: ViewSpec) -> datetime | None:
        """Newest ``inicio`` stored for an incremental view."""

    async def open_keys(self, view: ViewSpec, loaded_before: datetime) -> list[Any]:
        """Keys of rows still running (or without an end) not refreshed since a moment."""

    async def save_cycle(self, cycle: LoadCycle) -> None:
        """Insert or update a load cycle."""

    async def last_cycle(self) -> LoadCycle | None:
        """The most recent load cycle."""


class MonitoringAccess(Protocol):
    """Who may read the monitoring screens."""

    async def is_manager(self, principal: Any) -> bool:
        """Whether the caller is a platform administrator or a member of the owning area."""
