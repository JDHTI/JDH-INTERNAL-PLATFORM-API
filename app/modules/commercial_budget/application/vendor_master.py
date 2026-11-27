"""The rep master upload (Maestro de vendedores), moved from the supplier platform.

- ``read`` on ``maestro-vendedores`` downloads the template and sees the uploads; ``manage``
  uploads. On by default in BI; an administrator switches it on in other areas.
- The platform never processes the workbook: dwh-platform resolves each row against the ERP
  and upserts it (reps missing from the file do not change), then dbt publishes it in
  ``consumo.vendedores``. Its answer (counts, rejected rows, warnings) comes back as it is.
- Every upload is recorded (who, when, the file's name and size, what dwh-platform answered);
  the file itself is never kept: it carries personal data.
- dwh-platform has no simulation: every upload writes. ``preview`` (2026-11-26) reads the
  file with the loader's rules and compares it with what the warehouse publishes, so BI
  sees every change before uploading (``domain/vendor_master.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.modules.commercial_budget.domain.ports import Policy
from app.modules.commercial_budget.domain.vendor_master import (
    PLATFORM,
    MasterFile,
    Preview,
    TeamEntry,
    TeamOverride,
    allowed_teams,
    normalize,
    preview,
    team_catalog,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)
BOGOTA = ZoneInfo("America/Bogota")
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

    async def recent_sellers(self, months: Sequence[str]) -> dict[str, int]: ...

    async def team_overrides(self) -> list[TeamOverride]: ...

    async def save_team(
        self,
        name: str,
        *,
        team_class: str | None,
        is_active: bool,
        note: str | None,
        actor: str,
        now: datetime,
    ) -> TeamOverride: ...


class MasterState(Protocol):
    """The rep master as the warehouse publishes it today."""

    async def current(self) -> Any: ...

    async def teams(self) -> dict[str, tuple[str | None, int]]: ...

    async def template(self) -> Any: ...


class VendorMasterService:
    """Forward the rep master to dwh-platform and keep the history."""

    def __init__(
        self,
        client: PlatformClient,
        history: UploadHistory,
        policy: Policy,
        template: Callable[..., bytes],
        max_upload_bytes: int = 10 * 1024 * 1024,
        reader: Callable[[bytes], MasterFile] | None = None,
        state: MasterState | None = None,
        today: Callable[[], date] | None = None,
        standardizer: Callable[[bytes, Mapping[str, str | None]], bytes] | None = None,
    ) -> None:
        self.client = client
        self.history = history
        self.policy = policy
        self.template_builder = template
        self.max_upload_bytes = max_upload_bytes
        self.reader = reader
        self.state = state
        self.standardizer = standardizer
        self._today = today or (lambda: datetime.now(BOGOTA).date())

    # --- the team master (2026-11-27) ---

    async def _catalog(self) -> list[TeamEntry]:
        warehouse = await self.state.teams() if self.state is not None else {}
        return team_catalog(warehouse, await self.history.team_overrides())

    async def teams(self, principal: Principal) -> list[TeamEntry]:
        """The team master: the warehouse's teams and the ones created in the platform."""

        await self._require_read(principal)
        return await self._catalog()

    async def create_team(
        self, principal: Principal, name: str, team_class: str | None, note: str | None
    ) -> TeamEntry:
        """A new team, offered by the template until an upload creates it in the warehouse.

        Raises:
            ConflictError: The master already has a team with that name.
            InvalidInputError: No name or no class.
        """

        await self._require_manage(principal)
        clean, klass = normalize(name), normalize(team_class)
        if clean is None:
            raise InvalidInputError("Escribe el nombre del equipo")
        if klass is None:
            raise InvalidInputError("Escribe la clase del equipo (por ejemplo JUNIOR o MAYORISTAS)")
        if any(item.name == clean for item in await self._catalog()):
            raise ConflictError(f"El equipo {clean} ya está en el maestro")
        saved = await self.history.save_team(
            clean, team_class=klass, is_active=True, note=_note(note),
            actor=principal.email, now=datetime.now(UTC),
        )  # fmt: skip
        return TeamEntry(saved.name, saved.team_class, PLATFORM, True, 0, saved.note)

    async def update_team(
        self,
        principal: Principal,
        name: str,
        *,
        team_class: str | None = None,
        is_active: bool | None = None,
        note: str | None = None,
    ) -> TeamEntry:
        """Change a team's class (the next upload writes it) or hide it from the template.

        Raises:
            NotFoundError: No such team in the master.
        """

        await self._require_manage(principal)
        clean = normalize(name)
        found = next((item for item in await self._catalog() if item.name == clean), None)
        if found is None:
            raise NotFoundError(f"El equipo {name} no está en el maestro")
        saved = await self.history.save_team(
            found.name,
            team_class=normalize(team_class) or found.team_class,
            is_active=found.is_active if is_active is None else is_active,
            note=_note(note) if note is not None else found.note,
            actor=principal.email,
            now=datetime.now(UTC),
        )
        return TeamEntry(
            saved.name, saved.team_class, found.source, saved.is_active, found.reps, saved.note
        )

    async def _require_manage(self, principal: Principal) -> None:
        if not await self.policy.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar el maestro de vendedores")

    def _check_file(self, filename: str | None, content: bytes) -> str:
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
        return name

    async def preview(self, principal: Principal, filename: str | None, content: bytes) -> Preview:
        """What uploading the file would change, compared with the warehouse today (nothing
        is written): each rep's team, class, regional and business unit, new and emptied
        teams, rows the loader would reject, reps with sales the file leaves out and
        coordinators whose team links to none of its reps."""

        await self._require_read(principal)
        self._check_file(filename, content)
        if self.reader is None or self.state is None:
            raise InvalidInputError("La simulación no está disponible")
        master = self.reader(content)
        warehouse = await self.state.current()
        today = self._today()
        month = f"{today.year:04d}-{today.month:02d}"
        previous = (
            f"{today.year - 1:04d}-12"
            if today.month == 1
            else f"{today.year:04d}-{today.month - 1:02d}"
        )
        recent = await self.history.recent_sellers([month, previous])
        overrides = await self.history.team_overrides()
        return preview(
            master,
            warehouse.reps,
            warehouse.coordinators,
            centers=warehouse.centers,
            regionals=warehouse.regionals,
            recent=recent,
            teams=allowed_teams(team_catalog(warehouse.teams, overrides)),
        )

    async def status(self, principal: Principal) -> VendorMasterStatus:
        manage = await self.policy.is_manager(principal)
        read = manage or await self.policy.can_read(principal)
        return VendorMasterStatus(self.client.configured, read, manage)

    async def _require_read(self, principal: Principal) -> None:
        if not await self.policy.can_read(principal) and not await self.policy.is_manager(
            principal
        ):
            raise ForbiddenError("No tienes permiso para consultar el maestro de vendedores")

    async def template(self, principal: Principal, empty: bool = False) -> bytes:
        """The template with the team master's list (2026-11-27), filled with the current
        master unless ``empty``."""

        await self._require_read(principal)
        if self.state is None:
            return self.template_builder()
        teams = allowed_teams(await self._catalog())
        data = None if empty else await self.state.template()
        return self.template_builder(teams, data)

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
        name = self._check_file(filename, content)
        if self.reader is not None and self.state is not None:
            # The teams come from the master (2026-11-27): a row with another team stops the
            # upload, and every class is written as the master has it.
            found = await self.preview(principal, filename, content)
            outside = [item for item in found.rejected if "maestro de equipos" in item.reason]
            if outside:
                rows = "; ".join(f"{item.sheet} fila {item.row}" for item in outside[:10])
                raise InvalidInputError(
                    f"{len(outside)} filas tienen un equipo que no está en el maestro ({rows}). "
                    "Escógelo de la lista de la plantilla o créalo primero en Equipos."
                )
            if self.standardizer is not None:
                content = self.standardizer(content, allowed_teams(await self._catalog()))
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


def _note(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    return text[:500] or None


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
