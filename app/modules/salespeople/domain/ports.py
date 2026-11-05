"""Ports of the salespeople module: the links store and the warehouse roster."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.salespeople.domain.entities import RosterEntry, SalespersonLink


class LinkRepository(Protocol):
    """Stores who holds each salesperson or supervisor code."""

    async def list_for_user(self, user_id: UUID) -> list[SalespersonLink]:
        """The links of a user."""
        ...

    async def list_all(self) -> list[SalespersonLink]:
        """Every link, excluded codes included."""
        ...

    async def get(self, link_id: UUID) -> SalespersonLink | None:
        """One link."""
        ...

    async def get_by_code(self, kind: str, code: str) -> SalespersonLink | None:
        """The link of a code, if any."""
        ...

    async def put(
        self,
        kind: str,
        code: str,
        user_id: UUID | None,
        source: str,
        created_by_id: UUID | None,
    ) -> SalespersonLink:
        """Create or replace the link of a code (one row per kind and code)."""
        ...

    async def create_missing(self, rows: Sequence[tuple[str, str, UUID]], source: str) -> int:
        """Insert ``(kind, code, user_id)`` links whose code has no row yet; return how many."""
        ...


class SalesRoster(Protocol):
    """The SIESA sales force as the warehouse holds it (``consumo.vendedores``)."""

    async def sellers(self) -> list[RosterEntry]:
        """Every salesperson, active or not."""
        ...

    async def supervisors(self) -> list[RosterEntry]:
        """Every supervisor code with its name."""
        ...

    async def sellers_by_email(self, email: str) -> list[RosterEntry]:
        """Active salespeople with this e-mail."""
        ...

    async def team(self, supervisor_codes: Sequence[str]) -> list[str]:
        """Codes of the active salespeople of these supervisors."""
        ...


class UserLookup(Protocol):
    """Platform users by e-mail and id."""

    async def require_user(self, email: str):
        """The active or inactive user with this e-mail, or ``NotFoundError``."""
        ...

    async def get_users(self, user_ids):
        """Several users by id."""
        ...

    async def active_emails(self) -> dict[str, UUID]:
        """Lowercase e-mail -> id of every active user."""
        ...
