"""The rep master upload (Maestro de vendedores), moved from the supplier platform.

- ``read`` on ``maestro-vendedores`` downloads the template and sees the uploads; ``manage``
  uploads. On by default in BI; an administrator switches it on in other areas.
- The platform never processes the workbook: dwh-platform resolves each row against the ERP
  and upserts it (reps missing from the file do not change), then dbt publishes it in
  ``consumo.vendedores``. Its answer (counts, rejected rows, warnings) comes back as it is.
- Every upload is recorded (who, when, the file's name and size, what dwh-platform answered);
  the file itself is never kept: it carries personal data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    PayloadTooLargeError,
)
from app.modules.commercial_budget.domain.ports import Policy
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)
LOADED = "cargado"
REJECTED = "rechazado"
FAILED = "error"


@dataclass(frozen=True)
class VendorUpload:
    id: UUID
    filename: str
    size: int
    status: str
    http_status: int | None
    rejected_rows: int
    warnings: int
    summary: dict[str, Any]
    actor: str
    at: datetime


@dataclass(frozen=True)
class VendorMasterStatus:
    configured: bool
    can_read: bool
    can_manage: bool


class PlatformAnswer(Protocol):
    status_code: int
    payload: dict[str, Any]


class PlatformClient(Protocol):
    @property
    def configured(self) -> bool: ...

    async def upload(self, filename: str, content: bytes) -> PlatformAnswer: ...


class UploadHistory(Protocol):
    async def add(self, **fields: Any) -> VendorUpload: ...

    async def list(self, limit: int) -> list[VendorUpload]: ...


class VendorMasterService:
    """Forward the rep master to dwh-platform and keep the history."""

    def __init__(
        self,
        client: PlatformClient,
        history: UploadHistory,
        policy: Policy,
        template: Callable[[], bytes],
        max_upload_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.client = client
        self.history = history
        self.policy = policy
        self.template_builder = template
        self.max_upload_bytes = max_upload_bytes

    async def status(self, principal: Principal) -> VendorMasterStatus:
        manage = await self.policy.is_manager(principal)
        read = manage or await self.policy.can_read(principal)
        return VendorMasterStatus(self.client.configured, read, manage)

    async def _require_read(self, principal: Principal) -> None:
        if not await self.policy.can_read(principal) and not await self.policy.is_manager(
            principal
        ):
            raise ForbiddenError("No tienes permiso para consultar el maestro de vendedores")

    async def template(self, principal: Principal) -> bytes:
        await self._require_read(principal)
        return self.template_builder()

    async def uploads(self, principal: Principal, limit: int = 20) -> list[VendorUpload]:
        await self._require_read(principal)
        return await self.history.list(max(1, min(limit, 100)))

    async def upload(
        self, principal: Principal, filename: str | None, content: bytes
    ) -> dict[str, Any]:
        """Forward the workbook and return dwh-platform's answer.

        Raises:
            ForbiddenError: Without ``manage``.
            InvalidInputError: Not an .xlsx, or dwh-platform rejected the file (its message).
            PayloadTooLargeError: Over the size limit.
            DependencyUnavailableError: Not configured, unreachable or failing.
        """

        if not await self.policy.is_manager(principal):
            raise ForbiddenError("No tienes permiso para cargar el maestro de vendedores")
        name = (filename or "").strip()
        if not name.lower().endswith(".xlsx"):
            raise InvalidInputError(
                "El archivo debe ser un .xlsx (plantilla Maestro Vendedores JDH)"
            )
        if len(content) > self.max_upload_bytes:
            raise PayloadTooLargeError(
                f"El archivo supera {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        if not content:
            raise InvalidInputError("El archivo está vacío")
        record = {"filename": name[:255], "size": len(content), "actor": principal.email}
        try:
            answer = await self.client.upload(name, content)
        except DependencyUnavailableError as exc:
            await self._record(record, FAILED, None, {"detail": str(exc)})
            raise
        payload = answer.payload
        if answer.status_code >= 400:
            detail = _detail(payload)
            if answer.status_code in (400, 413, 415, 422):
                await self._record(record, REJECTED, answer.status_code, {"detail": detail})
                raise InvalidInputError(detail or "dwh-platform rechazó el archivo")
            logger.warning("vendor master: dwh-platform answered %s", answer.status_code)
            await self._record(record, FAILED, answer.status_code, {"detail": detail})
            raise DependencyUnavailableError(
                "dwh-platform no pudo procesar la carga"
                + (f": {detail}" if detail and answer.status_code < 500 else "")
            )
        await self._record(record, LOADED, answer.status_code, payload)
        return payload

    async def _record(
        self, record: dict[str, Any], status: str, http_status: int | None, payload: dict
    ) -> None:
        rejected = payload.get("filas_rechazadas")
        warnings = payload.get("advertencias")
        summary = {
            key: value
            for key, value in payload.items()
            if key not in ("filas_rechazadas", "advertencias")
        }
        try:
            await self.history.add(
                **record,
                status=status,
                http_status=http_status,
                rejected_rows=len(rejected) if isinstance(rejected, list) else 0,
                warnings=len(warnings) if isinstance(warnings, list) else 0,
                summary=summary,
                at=datetime.now(UTC),
            )
        except Exception:  # noqa: BLE001 - the upload happened; losing its record must not hide it
            logger.exception("vendor master: the upload could not be recorded")


def _detail(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:1000]
    if detail is not None:
        return str(detail)[:1000]
    return ""


__all__ = [
    "FAILED",
    "LOADED",
    "REJECTED",
    "VendorMasterService",
    "VendorMasterStatus",
    "VendorUpload",
]
