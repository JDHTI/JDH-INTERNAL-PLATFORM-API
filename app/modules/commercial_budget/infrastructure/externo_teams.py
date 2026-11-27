"""The team master in the warehouse (``externo.equipo``), written by the platform (2026-11-27).

The platform owns the teams: it creates them and sets their class; the reps
(``externo.vendedor_info``) and coordinators (``externo.supervisor``) keep coming from the
Excel upload through dwh-platform. What ``consumo.equipos`` publishes changes with the next
dbt run; the platform reads ``externo.equipo`` itself so a change shows at once.

Rules agreed with the DWH (role ``svc_plataforma_externo``):
- never delete a team: the foreign keys are ``ON DELETE SET NULL`` and would unassign its reps;
- never rename one and never rewrite its ``uuid``: dbt keys on both;
- a hidden team is only hidden in the platform (``vendor_master_teams``); the table has no
  active flag.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.errors import ConflictError, DependencyUnavailableError
from app.modules.commercial_budget.domain.vendor_master import normalize
from app.modules.dwh_publish.domain.ports import DwhRejected, DwhUnavailable
from app.modules.dwh_publish.infrastructure.writer import WarehouseWriterConnection

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ORIGIN = "plataforma"


class ExternoTeamWriter:
    """Read and write ``externo.equipo`` with the platform's writer role."""

    def __init__(
        self,
        connection: WarehouseWriterConnection,
        company_id: int = 1,
        schema: str = "externo",
    ) -> None:
        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError(f"Invalid schema: {schema}")
        self.connection = connection
        self.company_id = company_id
        self.schema = schema

    @property
    def configured(self) -> bool:
        """Whether the writer role is set (otherwise the teams stay as in the 2026-11-27
        upload flow: created in the platform, written by the next upload)."""

        return self.connection.configured

    async def teams(self) -> dict[str, str | None]:
        """Every team of the master: name → class."""

        async with self._transaction("leer") as connection:
            rows = await connection.fetch(f"select nombre, clase from {self.schema}.equipo")
        found: dict[str, str | None] = {}
        for row in rows:
            name = normalize(row["nombre"])
            if name:
                found[name] = normalize(row["clase"])
        return found

    async def create(self, name: str, team_class: str) -> None:
        """Insert a team.

        Raises:
            ConflictError: The table already has it (an upload or someone else created it).
            DependencyUnavailableError: The warehouse could not take the write.
        """

        async with self._transaction("crear el equipo en") as connection:
            created = await connection.fetchval(
                f"insert into {self.schema}.equipo (nombre, clase, id_cia, origen) "
                "values ($1, $2, $3, $4) on conflict (nombre) do nothing returning uuid",
                name,
                team_class,
                self.company_id,
                ORIGIN,
            )
        if created is None:
            raise ConflictError(f"El equipo {name} ya está en el maestro del DWH")

    async def set_class(self, name: str, team_class: str) -> bool:
        """Change a team's class; whether the team was there."""

        async with self._transaction("cambiar la clase en") as connection:
            status = await connection.execute(
                f"update {self.schema}.equipo set clase = $2, actualizado_en = now() "
                "where nombre = $1 and clase is distinct from $2",
                name,
                team_class,
            )
            if status.endswith(" 0"):
                return bool(
                    await connection.fetchval(
                        f"select true from {self.schema}.equipo where nombre = $1", name
                    )
                )
        return True

    @asynccontextmanager
    async def _transaction(self, action: str) -> AsyncIterator[Any]:
        """The writer's transaction, its failures as the API's 503."""

        try:
            async with self.connection.transaction() as connection:
                yield connection
        except (DwhUnavailable, DwhRejected) as exc:
            logger.warning("externo.equipo: could not %s the warehouse: %s", action, exc)
            raise DependencyUnavailableError(
                f"No se pudo {action} el maestro de equipos del DWH; intenta de nuevo en un momento"
            ) from exc


__all__ = ["ORIGIN", "ExternoTeamWriter"]
