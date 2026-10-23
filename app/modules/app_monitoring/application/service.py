"""Use cases of the TI area's application monitoring (Monitoreo → Aplicativos).

The screens read the stored checks, never the applications on a request; only the
background loop and ``check_now`` send requests.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.app_monitoring.domain.entities import (
    Application,
    CheckResult,
    CheckStatus,
    Target,
    worst,
)
from app.modules.app_monitoring.domain.ports import ApplicationRepository, MonitoringAccess, Prober
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 500
DEFAULT_DAYS = 1
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")
# Fields a caller may set; anything else in a change is ignored by the router.
EDITABLE = (
    "name",
    "slug",
    "description",
    "base_url",
    "health_path",
    "ui_path",
    "check_ui",
    "enabled",
    "verify_tls",
    "timeout_s",
    "slow_ms",
    "sort_order",
)


def slugify(text: str) -> str:
    """``Catálogo (Tienda)`` → ``catalogo-tienda``."""

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:60]


def _targets(application: Application) -> list[Target]:
    return [Target.API, Target.UI] if application.check_ui else [Target.API]


class AppMonitoringService:
    """Register applications, check them and read their status."""

    def __init__(
        self,
        repository: ApplicationRepository,
        prober: Prober,
        access: MonitoringAccess,
        timezone: str = "America/Bogota",
        check_enabled: bool = False,
        check_interval: int = 60,
        retention_days: int = 30,
        request_check: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (ApplicationRepository): Applications and check history.
            prober (Prober): Sends the requests.
            access (MonitoringAccess): Who may read and edit the screens.
            timezone (str): Zone of the business day used by date filters.
            check_enabled (bool): Whether the background checks run in this process.
            check_interval (int): Seconds between background checks.
            retention_days (int): Days of checks kept.
            request_check (Callable[[], None] | None): Wakes the background checks.
            clock (Callable[[], datetime]): Current time, replaceable in tests.
        """

        self.repository = repository
        self.prober = prober
        self.access = access
        self.zone = ZoneInfo(timezone)
        self.timezone = timezone
        self.check_enabled = check_enabled
        self.check_interval = check_interval
        self.retention = timedelta(days=retention_days)
        self.request_check_now = request_check
        self.clock = clock

    # --- Helpers ---

    async def _authorize(self, principal: Principal) -> None:
        if not await self.access.is_manager(principal):
            raise ForbiddenError("El monitoreo de aplicativos es del área de TI")

    async def _get(self, application_id: UUID) -> Application:
        application = await self.repository.get(application_id)
        if application is None:
            raise NotFoundError("El aplicativo no existe")
        return application

    def _range(self, start: date | None, end: date | None) -> tuple[datetime, datetime]:
        """Inclusive business days to ``[start, end)``; today by default."""

        last = end or self.clock().astimezone(self.zone).date()
        first = start or (last - timedelta(days=DEFAULT_DAYS - 1))
        if first > last:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        return (
            datetime.combine(first, time.min, tzinfo=self.zone),
            datetime.combine(last + timedelta(days=1), time.min, tzinfo=self.zone),
        )

    @staticmethod
    def _validate(application: Application) -> Application:
        name = application.name.strip()
        if not name:
            raise InvalidInputError("El aplicativo necesita un nombre")
        parts = urlsplit(application.base_url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise InvalidInputError("La URL base debe ser http(s)://servidor")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise InvalidInputError(
                "La URL base es solo el servidor; la ruta va en ruta_health o ruta_ui"
            )
        if parts.username or parts.password:
            raise InvalidInputError("La URL base no puede llevar usuario ni clave")
        for label, path in (
            ("ruta_health", application.health_path),
            ("ruta_ui", application.ui_path),
        ):
            if not path.startswith("/"):
                raise InvalidInputError(f"{label} debe empezar por /")
        if not _SLUG.match(application.slug):
            raise InvalidInputError("El slug usa minúsculas, números y guiones (2 a 60)")
        if not 1 <= application.timeout_s <= 60:
            raise InvalidInputError("El timeout va de 1 a 60 segundos")
        if not 100 <= application.slow_ms <= 60000:
            raise InvalidInputError("El umbral de lentitud va de 100 a 60000 ms")
        return replace(
            application,
            name=name,
            base_url=f"{parts.scheme}://{parts.netloc}",
            description=(application.description or "").strip() or None,
        )

    def _view(
        self,
        application: Application,
        latest: Mapping[tuple[UUID, Target], CheckResult],
        summary: Mapping[tuple[UUID, Target], dict[str, Any]],
    ) -> dict[str, Any]:
        """An application with the newest check and the last 24 h of each target."""

        targets: dict[str, dict[str, Any] | None] = {}
        for target in Target:
            if target is Target.UI and not application.check_ui:
                targets[target.value] = None
                continue
            last = latest.get((application.id, target))
            stats = summary.get((application.id, target)) or {"total": 0, "up": 0}
            targets[target.value] = {
                "url": application.url(target),
                "ultimo": last,
                "disponibilidad_24h": (
                    round(stats["up"] * 100 / stats["total"], 2) if stats["total"] else None
                ),
                "chequeos_24h": stats["total"],
                "latencia_media_24h_ms": stats.get("latencia_media_ms"),
            }
        status = None
        if application.enabled:
            status = worst(
                [
                    latest[(application.id, target)].status
                    if (application.id, target) in latest
                    else None
                    for target in _targets(application)
                ]
            )
        return {"aplicativo": application, "estado": status, **targets}

    # --- Checks ---

    async def _probe(self, application: Application) -> list[CheckResult]:
        return list(
            await asyncio.gather(
                *(self.prober.probe(application, target) for target in _targets(application))
            )
        )

    async def check_all(self) -> list[CheckResult]:
        """Check every enabled application at once, store the results, purge old ones.

        Returns:
            list[CheckResult]: One result per application and target.
        """

        applications = await self.repository.list(only_enabled=True)
        batches = await asyncio.gather(*(self._probe(app) for app in applications))
        results = [result for batch in batches for result in batch]
        await self.repository.save_results(results)
        await self.repository.purge(self.clock() - self.retention)
        return results

    async def check_now(self, application_id: UUID, principal: Principal) -> dict[str, Any]:
        """Check one application right away (enabled or not) and store the result.

        Args:
            application_id (UUID): The application.
            principal (Principal): The caller.

        Returns:
            dict[str, Any]: The application with its fresh status.
        """

        await self._authorize(principal)
        application = await self._get(application_id)
        results = await self._probe(application)
        await self.repository.save_results(results)
        return await self._single(application)

    async def request_check(self, principal: Principal) -> None:
        """Ask the background loop to check every application now.

        Raises:
            ConflictError: If the background checks are off in this process.
        """

        await self._authorize(principal)
        if not self.check_enabled or self.request_check_now is None:
            raise ConflictError("Los chequeos automáticos de aplicativos están apagados")
        self.request_check_now()

    # --- Screens ---

    async def _single(self, application: Application) -> dict[str, Any]:
        latest = await self.repository.latest()
        summary = await self.repository.summary_since(self.clock() - timedelta(hours=24))
        return self._view(application, latest, summary)

    async def applications(self, principal: Principal) -> list[dict[str, Any]]:
        """Every application with its status, for the table and the cards."""

        await self._authorize(principal)
        applications = await self.repository.list()
        latest = await self.repository.latest()
        summary = await self.repository.summary_since(self.clock() - timedelta(hours=24))
        return [self._view(application, latest, summary) for application in applications]

    async def application(self, application_id: UUID, principal: Principal) -> dict[str, Any]:
        """One application with its status."""

        await self._authorize(principal)
        return await self._single(await self._get(application_id))

    async def overview(self, principal: Principal) -> dict[str, Any]:
        """Counts by status and the background loop's settings."""

        items = await self.applications(principal)
        enabled = [item for item in items if item["aplicativo"].enabled]
        checked = [
            last.checked_at
            for item in enabled
            for target in Target
            if item[target.value] and (last := item[target.value]["ultimo"])
        ]
        return {
            "total": len(items),
            "habilitados": len(enabled),
            "arriba": sum(1 for item in enabled if item["estado"] is CheckStatus.UP),
            "degradados": sum(1 for item in enabled if item["estado"] is CheckStatus.DEGRADED),
            "caidos": sum(1 for item in enabled if item["estado"] is CheckStatus.DOWN),
            "sin_datos": sum(1 for item in enabled if item["estado"] is None),
            "estado": worst([item["estado"] for item in enabled]),
            "ultimo_chequeo": max(checked, default=None),
            "chequeo_automatico": {
                "habilitado": self.check_enabled,
                "intervalo_s": self.check_interval,
            },
            "aplicativos": items,
        }

    async def history(
        self,
        application_id: UUID,
        principal: Principal,
        target: Target | None,
        start: date | None,
        end: date | None,
        page: int,
        size: int,
    ) -> tuple[list[CheckResult], int]:
        """Checks of one application, newest first (today by default)."""

        await self._authorize(principal)
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página desde 1 y tamaño entre 1 y {MAX_PAGE_SIZE}")
        await self._get(application_id)
        first, last = self._range(start, end)
        return await self.repository.history(application_id, target, first, last, page, size)

    # --- Registry ---

    async def create(self, principal: Principal, values: Mapping[str, Any]) -> dict[str, Any]:
        """Register an application; its slug comes from the name when not given.

        Args:
            principal (Principal): The caller.
            values (Mapping[str, Any]): Fields of ``EDITABLE``; ``name`` and ``base_url``
                are required.

        Returns:
            dict[str, Any]: The new application, not checked yet.
        """

        await self._authorize(principal)
        if not values.get("name") or not values.get("base_url"):
            raise InvalidInputError("El aplicativo necesita nombre y URL base")
        fields = {
            key: value for key, value in values.items() if key in EDITABLE and value is not None
        }
        fields.setdefault("slug", slugify(fields["name"]))
        application = self._validate(Application(id=uuid4(), **fields))
        if await self.repository.slug_taken(application.slug):
            raise ConflictError(f"Ya hay un aplicativo con el slug {application.slug}")
        created = await self.repository.add(application)
        return await self._single(created)

    async def update(
        self, application_id: UUID, principal: Principal, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Change some fields of an application.

        Args:
            application_id (UUID): The application.
            principal (Principal): The caller.
            changes (Mapping[str, Any]): Only the fields to change.

        Returns:
            dict[str, Any]: The application with its status.
        """

        await self._authorize(principal)
        current = await self._get(application_id)
        fields = {key: value for key, value in changes.items() if key in EDITABLE}
        for required in ("name", "slug", "base_url", "health_path", "ui_path"):
            if required in fields and fields[required] is None:
                raise InvalidInputError(f"{required} no puede quedar vacío")
        application = self._validate(replace(current, **fields))
        if application.slug != current.slug and await self.repository.slug_taken(
            application.slug, exclude=application.id
        ):
            raise ConflictError(f"Ya hay un aplicativo con el slug {application.slug}")
        return await self._single(await self.repository.update(application))

    async def delete(self, application_id: UUID, principal: Principal) -> None:
        """Remove an application and its history."""

        await self._authorize(principal)
        if not await self.repository.delete(application_id):
            raise NotFoundError("El aplicativo no existe")
