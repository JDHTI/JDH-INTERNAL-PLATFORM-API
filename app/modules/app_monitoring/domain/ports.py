"""Contracts the application monitoring use cases depend on."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.app_monitoring.domain.entities import Application, CheckResult, Target


class Prober(Protocol):
    """Sends the request of one check."""

    async def probe(self, application: Application, target: Target) -> CheckResult:
        """Request the target and classify the answer; never raises for a failed call."""


class ApplicationRepository(Protocol):
    """The registered applications and the history of their checks."""

    async def list(self, only_enabled: bool = False) -> list[Application]:
        """Every application, in display order."""

    async def get(self, application_id: UUID) -> Application | None:
        """One application."""

    async def slug_taken(self, slug: str, exclude: UUID | None = None) -> bool:
        """Whether another application already uses a slug."""

    async def add(self, application: Application) -> Application:
        """Store a new application."""

    async def update(self, application: Application) -> Application:
        """Store the changed fields of an application."""

    async def delete(self, application_id: UUID) -> bool:
        """Remove an application and its checks."""

    async def save_results(self, results: Sequence[CheckResult]) -> None:
        """Append checks to the history."""

    async def latest(self) -> dict[tuple[UUID, Target], CheckResult]:
        """The newest check of every application and target."""

    async def summary_since(self, since: datetime) -> dict[tuple[UUID, Target], dict[str, Any]]:
        """Per application and target: ``total``, ``up``, ``latencia_media_ms`` since a moment."""

    async def history(
        self,
        application_id: UUID,
        target: Target | None,
        start: datetime,
        end: datetime,
        page: int,
        size: int,
    ) -> tuple[list[CheckResult], int]:
        """Checks of one application, newest first."""

    async def purge(self, before: datetime) -> int:
        """Drop checks older than a moment."""


class MonitoringAccess(Protocol):
    """Who may read and edit the monitoring screens."""

    async def is_manager(self, principal: Any) -> bool:
        """Whether the caller may manage the module (register, edit, check)."""

    async def can_read(self, principal: Any) -> bool:
        """Whether the caller may read the screens."""
