"""The sales force from the data warehouse.

- Salespeople: ``consumo.vendedores``. ``codigo`` is the salesperson, ``correo`` the corporate
  e-mail (filled for a minority), ``equipo`` the name of their team.
- Coordinators: the commercial master ``consumo.supervisores`` (kept by hand), never SIESA's
  ``cod_supervisor`` (2026-11-24). A coordinator's code is their ``nit``, ``correo`` their
  e-mail, and ``equipo_key`` their team in ``consumo.equipos``.
- A coordinator's salespeople are the active ones whose ``vendedores.equipo`` is the name of
  their team (``vendedores`` does not publish ``equipo_key``; team names are unique).
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
# A row of the master that coordinates a team ("Director" alone has none).
_COORDINATOR = "lower(btrim(s.\"rol\")) IN ('coordinador', 'coordinador y director')"


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
        self.teams = f'"{schema}"."equipos"'
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
            supervisor_code=_text(row["coordinator"]),
            is_active=row["estado"] == "A",
            team=_text(row["equipo"]),
        )

    def _sellers_query(self, where: str = "") -> str:
        """Salespeople with their team and the coordinator of that team in the commercial
        master (the lowest code if a team ever had two)."""

        return (
            f'SELECT v."codigo", v."nombre", v."correo", v."estado", v."equipo",'
            f' (SELECT min(btrim(s."nit")) FROM {self.coordinators} s'
            f' JOIN {self.teams} e ON e."equipo_key" = s."equipo_key"'
            f' WHERE e."nombre" = v."equipo" AND {_COORDINATOR}) AS coordinator'
            f' FROM {self.view} v {where} ORDER BY v."codigo"'
        )

    async def sellers(self) -> list[RosterEntry]:
        """Every salesperson, active or not, by code."""

        return [self._seller(row) for row in await self._fetch(self._sellers_query())]

    async def supervisors(self) -> list[RosterEntry]:
        """The coordinators of the commercial master, by code (``nit``), with their e-mail and
        team. One without a team is listed inactive: it gives no salespeople."""

        rows = await self._fetch(
            f'SELECT btrim(s."nit") AS code, max(s."nombre") AS name,'
            f' lower(max(btrim(s."correo"))) AS email, max(e."nombre") AS team'
            f" FROM {self.coordinators} s"
            f' LEFT JOIN {self.teams} e ON e."equipo_key" = s."equipo_key"'
            f" WHERE coalesce(btrim(s.\"nit\"), '') <> '' AND {_COORDINATOR}"
            " GROUP BY 1 ORDER BY 1"
        )
        return [
            RosterEntry(
                SUPERVISOR,
                row["code"],
                _text(row["name"]),
                email=_text(row["email"]),
                team=_text(row["team"]),
                is_active=row["team"] is not None,
            )
            for row in rows
        ]

    async def supervisors_by_email(self, email: str) -> list[str]:
        """Codes of the coordinators of the commercial master with this e-mail."""

        rows = await self._fetch(
            f'SELECT DISTINCT btrim(s."nit") AS code FROM {self.coordinators} s'
            ' WHERE lower(btrim(s."correo")) = $1'
            f" AND coalesce(btrim(s.\"nit\"), '') <> '' AND {_COORDINATOR} ORDER BY 1",
            email.strip().lower(),
        )
        return [row["code"] for row in rows]

    async def sellers_by_email(self, email: str) -> list[RosterEntry]:
        """Active salespeople with this e-mail (stored upper case in SIESA)."""

        rows = await self._fetch(
            self._sellers_query('WHERE lower(v."correo") = $1 AND v."estado" = \'A\''),
            email.strip().lower(),
        )
        return [self._seller(row) for row in rows]

    async def team(self, supervisor_codes: Sequence[str]) -> list[str]:
        """Codes of the active salespeople in the teams of these coordinators (master's
        ``equipo_key`` -> ``equipos.nombre`` -> ``vendedores.equipo``)."""

        if not supervisor_codes:
            return []
        rows = await self._fetch(
            f'SELECT DISTINCT btrim(v."codigo") AS code FROM {self.coordinators} s'
            f' JOIN {self.teams} e ON e."equipo_key" = s."equipo_key"'
            f' JOIN {self.view} v ON v."equipo" = e."nombre"'
            f' WHERE btrim(s."nit") = ANY($1::text[]) AND {_COORDINATOR}'
            " AND v.\"estado\" = 'A' ORDER BY 1",
            list(supervisor_codes),
        )
        return [row["code"] for row in rows]
