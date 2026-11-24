"""The SIESA sales force from the data warehouse (``consumo.vendedores``).

``codigo`` is the salesperson; ``cod_supervisor`` and ``supervisor`` name their supervisor,
a separate catalog (no supervisor code is a salesperson code). ``correo`` is the corporate
e-mail, filled for a minority of salespeople.

The supervisors' e-mail is in ``consumo.supervisores`` (the commercial coordinators and
directors, kept by hand): ``nit`` is the ``cod_supervisor`` of ``vendedores`` and ``correo`` the
corporate e-mail (2026-11-24, Juan Rojas: supervisor 8104570 only there).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.salespeople.domain.entities import SELLER, SUPERVISOR, RosterEntry

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


class WarehouseSalesRoster:
    """Salespeople and supervisors, read live (the view has a few hundred rows)."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 30.0
    ) -> None:
        """Create the roster.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float): Seconds per query.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.view = f'"{schema}"."vendedores"'
        self.coordinators = f'"{schema}"."supervisores"'
        self.timeout = timeout

    async def _fetch(self, query: str, *args: object):
        try:
            return await self.connection.fetch(query, *args, timeout=self.timeout)
        except DataProviderUnavailable as exc:
            logger.warning("salespeople: warehouse query failed: %s", exc.__cause__ or exc)
            raise DependencyUnavailableError(
                "No se pudo consultar el datawarehouse; inténtalo de nuevo en unos minutos"
            ) from exc

    @staticmethod
    def _seller(row) -> RosterEntry:
        return RosterEntry(
            kind=SELLER,
            code=str(row["codigo"]).strip(),
            name=_text(row["nombre"]),
            email=(_text(row["correo"]) or "").lower() or None,
            supervisor_code=_text(row["cod_supervisor"]),
            is_active=row["estado"] == "A",
        )

    async def sellers(self) -> list[RosterEntry]:
        """Every salesperson, active or not, by code."""

        rows = await self._fetch(
            f'SELECT "codigo", "nombre", "correo", "cod_supervisor", "estado" FROM {self.view}'
            ' ORDER BY "codigo"'
        )
        return [self._seller(row) for row in rows]

    async def supervisors(self) -> list[RosterEntry]:
        """Each supervisor code once, with the name the warehouse gives it and the e-mail of
        ``supervisores`` when it has one (none when that master cannot be read)."""

        rows = await self._fetch(
            f'SELECT trim("cod_supervisor") AS code, max("supervisor") AS name,'
            f" bool_or(\"estado\" = 'A') AS active FROM {self.view}"
            " WHERE coalesce(trim(\"cod_supervisor\"), '') <> '' GROUP BY 1 ORDER BY 1"
        )
        emails = await self._coordinator_emails()
        return [
            RosterEntry(
                SUPERVISOR,
                row["code"],
                _text(row["name"]),
                email=emails.get(row["code"]),
                is_active=bool(row["active"]),
            )
            for row in rows
        ]

    async def _coordinator_emails(self) -> dict[str, str]:
        """Supervisor code -> lowercase e-mail, from ``supervisores``. A code with two e-mails
        keeps none: it cannot tell who holds it."""

        try:
            rows = await self._fetch(
                f'SELECT trim("nit"::text) AS code, lower(trim("correo")) AS email'
                f" FROM {self.coordinators}"
                " WHERE coalesce(trim(\"nit\"::text), '') <> ''"
                " AND coalesce(trim(\"correo\"), '') <> ''"
            )
        except DependencyUnavailableError:
            return {}
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(row["code"], set()).add(row["email"])
        return {code: next(iter(emails)) for code, emails in found.items() if len(emails) == 1}

    async def supervisors_by_email(self, email: str) -> list[str]:
        """Supervisor codes whose ``supervisores`` e-mail is this one."""

        rows = await self._fetch(
            f'SELECT DISTINCT trim("nit"::text) AS code FROM {self.coordinators}'
            ' WHERE lower(trim("correo")) = $1'
            " AND coalesce(trim(\"nit\"::text), '') <> '' ORDER BY 1",
            email.strip().lower(),
        )
        return [row["code"] for row in rows]

    async def sellers_by_email(self, email: str) -> list[RosterEntry]:
        """Active salespeople with this e-mail (stored upper case in SIESA)."""

        rows = await self._fetch(
            f'SELECT "codigo", "nombre", "correo", "cod_supervisor", "estado" FROM {self.view}'
            ' WHERE lower("correo") = $1 AND "estado" = \'A\' ORDER BY "codigo"',
            email.strip().lower(),
        )
        return [self._seller(row) for row in rows]

    async def team(self, supervisor_codes: Sequence[str]) -> list[str]:
        """Codes of the active salespeople of these supervisors."""

        if not supervisor_codes:
            return []
        rows = await self._fetch(
            f'SELECT "codigo" FROM {self.view}'
            ' WHERE trim("cod_supervisor") = ANY($1::text[]) AND "estado" = \'A\''
            ' ORDER BY "codigo"',
            list(supervisor_codes),
        )
        return [str(row["codigo"]).strip() for row in rows]
