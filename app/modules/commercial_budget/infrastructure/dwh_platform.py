"""The client of dwh-platform (the warehouse's API): the rep master upload.

dwh-platform owns the master (``externo.vendedor_info``, ``externo.equipo``,
``externo.supervisor``, ``externo.unidad_negocio``), resolves each document against the ERP,
upserts it and then runs dbt so ``consumo.vendedores`` publishes it. The platform only forwards
the workbook, server to server: the API key never reaches the browser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)
UPLOAD_PATH = "/maestro-vendedores/cargar"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class PlatformResponse:
    """What dwh-platform answered: the status and its JSON (or its text as ``detail``)."""

    status_code: int
    payload: dict[str, Any]


class DwhPlatformClient:
    """Forwards the rep master workbook to dwh-platform."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client.

        Args:
            base_url (str): dwh-platform's URL; blank means not configured.
            api_key (str): Its ``X-API-Key`` (blank in development).
            timeout (float): Seconds for the upload (it resolves every row synchronously).
            transport (httpx.AsyncBaseTransport | None): A fake transport (tests).
        """

        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def upload(self, filename: str, content: bytes) -> PlatformResponse:
        """Send the workbook.

        Raises:
            DependencyUnavailableError: Not configured, or dwh-platform cannot be reached.
        """

        if not self.configured:
            raise DependencyUnavailableError(
                "La carga del maestro no está configurada (DWH_PLATFORM_URL)"
            )
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}{UPLOAD_PATH}",
                    headers=headers,
                    files={"archivo": (filename, content, XLSX)},
                )
        except httpx.HTTPError as exc:
            logger.warning("vendor master: dwh-platform unreachable: %s", exc)
            raise DependencyUnavailableError(
                "No fue posible contactar a dwh-platform; intenta de nuevo en unos minutos"
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:2000]}
        if not isinstance(body, dict):
            body = {"detail": body}
        return PlatformResponse(response.status_code, body)


__all__ = ["DwhPlatformClient", "PlatformResponse", "UPLOAD_PATH"]
