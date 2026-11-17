"""Office documents to PDF through Gotenberg (LibreOffice in its own container)."""

from __future__ import annotations

import logging

import httpx

from app.core.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)


class GotenbergConverter:
    """Converts a .docx to PDF with ``POST /forms/libreoffice/convert``."""

    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        """Create the converter.

        Args:
            base_url (str): Gotenberg root, such as ``http://gotenberg:3000``; blank turns
                the conversion off (every call fails as unavailable).
            timeout_seconds (float): A long document takes a few seconds.
        """

        self.base_url = base_url.strip().rstrip("/")
        self.timeout = timeout_seconds

    async def to_pdf(self, filename: str, content: bytes) -> bytes:
        """The PDF of the document.

        Raises:
            DependencyUnavailableError: Gotenberg is not configured, down or failed.
        """

        if not self.base_url:
            raise DependencyUnavailableError("La conversión a PDF no está configurada")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/forms/libreoffice/convert",
                    files={"files": (filename, content)},
                )
        except httpx.HTTPError as exc:
            logger.warning("Gotenberg did not answer: %s", exc)
            raise DependencyUnavailableError("El conversor a PDF no responde") from exc
        if response.status_code != 200 or not response.content.startswith(b"%PDF"):
            logger.warning("Gotenberg answered %s: %s", response.status_code, response.text[:300])
            raise DependencyUnavailableError("El conversor a PDF no pudo convertir el documento")
        return response.content

    async def check(self) -> bool:
        """Whether Gotenberg answers its health check."""

        if not self.base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200
