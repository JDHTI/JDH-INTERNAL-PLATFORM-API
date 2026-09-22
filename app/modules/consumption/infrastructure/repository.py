"""PostgreSQL adapter for the read-only consumption views."""

from __future__ import annotations

import re

from app.infrastructure.data_provider import DataProviderConnection
from app.modules.consumption.domain.entities import Client

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CLIENT_COLUMNS = (
    "cliente_key",
    "nit",
    "razon_social",
    "sucursal",
    "nombre_establecimiento",
    "zona_logistica",
    "departamento",
    "ciudad",
    "nombre_vendedor",
    "telefono",
    "celular",
    "direccion",
    "correo",
    "unidad_negocio",
    "canal",
)


class WarehouseClientRepository:
    """Read clients from the configured provider's consumption view."""

    def __init__(
        self,
        connection: DataProviderConnection,
        schema: str = "consumo",
        view: str = "clientes",
    ) -> None:
        """Create the repository with a provider connection and safe identifiers.

        Args:
            connection (DataProviderConnection): External provider connection.
            schema (str): Consumption schema containing the view.
            view (str): Client view name.

        Raises:
            ValueError: If the configured schema or view is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema) or not _IDENTIFIER.fullmatch(view):
            raise ValueError("Invalid data provider relation")
        self.connection = connection
        self.relation = f'"{schema}"."{view}"'

    async def check(self) -> None:
        """Verify that the configured client view can be reached."""

        await self.connection.check()

    async def fetch_clients(self, after_key: str | None, limit: int) -> tuple[list[Client], bool]:
        """Read a bounded client page ordered by its stable source key.

        Args:
            after_key (str | None): Exclusive key from the previous page.
            limit (int): Maximum number of client rows to return.

        Returns:
            tuple[list[Client], bool]: Mapped rows and whether another page exists.
        """

        columns = ", ".join(f'"{column}"' for column in _CLIENT_COLUMNS)
        if after_key is None:
            query = (
                f"SELECT {columns} FROM {self.relation} "
                'WHERE "cliente_key" IS NOT NULL '
                'ORDER BY "cliente_key" ASC LIMIT $1'
            )
            rows = await self.connection.fetch(query, limit + 1)
        else:
            query = (
                f"SELECT {columns} FROM {self.relation} "
                'WHERE "cliente_key" IS NOT NULL AND "cliente_key" > $1 '
                'ORDER BY "cliente_key" ASC LIMIT $2'
            )
            rows = await self.connection.fetch(query, after_key, limit + 1)

        has_more = len(rows) > limit
        return [self._to_client(row) for row in rows[:limit]], has_more

    @staticmethod
    def _to_client(row: object) -> Client:
        values = {column: row[column] for column in _CLIENT_COLUMNS}  # type: ignore[index]
        client_key = values.pop("cliente_key")
        if not isinstance(client_key, str) or not client_key:
            raise ValueError("Client view returned an invalid cliente_key")
        return Client(client_key=client_key, **values)
