"""Microsoft Graph with application permissions: token and request shared by every client."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"


class GraphAppClient:
    """Talk to Graph with client credentials; one instance is shared and reuses its token."""

    # Names the credentials in the error a rejected secret raises ("del correo").
    credentials_label = "de la aplicación"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> None:
        """Create the client.

        Args:
            tenant_id (str): Directory that owns the app registration.
            client_id (str): Application (client) identifier.
            client_secret (str): Secret value (never its identifier).
            http (httpx.AsyncClient | None): Injected transport for tests.
            timeout (float): Seconds allowed per request.
        """

        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._token_value: str | None = None
        self._token_expires = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Release the HTTP connections."""

        await self._http.aclose()

    async def _token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token_value and time.monotonic() < self._token_expires:
                return self._token_value
            try:
                response = await self._http.post(
                    f"{LOGIN_URL}/{self._tenant_id}/oauth2/v2.0/token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": "https://graph.microsoft.com/.default",
                        "grant_type": "client_credentials",
                    },
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError("No se pudo contactar a Microsoft Entra") from exc
            if response.status_code != 200:
                logger.error("Graph token request failed: %s", response.status_code)
                raise DependencyUnavailableError(
                    f"Microsoft Entra rechazó las credenciales {self.credentials_label}"
                )
            payload = response.json()
            self._token_value = payload["access_token"]
            # Renew a minute early so a request never carries a token about to expire.
            self._token_expires = time.monotonic() + int(payload.get("expires_in", 3600)) - 60
            return self._token_value

    async def _request(
        self,
        method: str,
        url: str,
        *,
        missing: tuple[int, ...] = (),
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Call Graph, renewing the token once on 401.

        Returns ``None`` when the status is one of ``missing`` (a 404, an expired cursor).
        """

        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", **(headers or {})},
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError("No se pudo contactar a Microsoft Graph") from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in missing:
                return None
            if response.status_code >= 400:
                logger.error(
                    "Graph %s %s failed: %s %s",
                    method,
                    url.split("?")[0],
                    response.status_code,
                    response.text[:300],
                )
                raise DependencyUnavailableError(
                    f"Microsoft Graph respondió {response.status_code}"
                )
            return response
        raise DependencyUnavailableError("Microsoft Graph rechazó el token")  # pragma: no cover
