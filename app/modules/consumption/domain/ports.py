"""Ports consumed by the read-only consumption application service."""

from __future__ import annotations

from typing import Protocol

from app.modules.consumption.domain.entities import Client


class ClientDataSource(Protocol):
    """Define the paginated client read contract."""

    async def fetch_clients(self, after_key: str | None, limit: int) -> tuple[list[Client], bool]:
        """Read clients after an optional stable key.

        Args:
            after_key (str | None): Exclusive key from the previous page.
            limit (int): Maximum number of client rows to return.

        Returns:
            tuple[list[Client], bool]: Rows and whether another page exists.
        """

        ...
