"""General monitoring board: the applications and the data warehouse in one view."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.modules.app_monitoring.domain.entities import CheckStatus, worst
from app.modules.identity.domain.principal import Principal

# The copy of the warehouse counts as stale after this many missed load intervals.
_STALE_INTERVALS = 3


class ApplicationsOverview(Protocol):
    async def overview(self, principal: Principal) -> dict[str, Any]: ...


class WarehouseOverview(Protocol):
    async def overview(self, principal: Principal) -> dict[str, Any]: ...


def warehouse_status(overview: dict[str, Any], now: datetime) -> CheckStatus | None:
    """Traffic light of the warehouse from its monitoring overview.

    Down when the last load failed or the copy is older than a few load intervals;
    degraded when tables are late, a load was partial or a sequence's last run
    failed; ``None`` before the first load.
    """

    load = overview["carga"]
    last = load["ultima"]
    if last is None:
        return None
    if last.status is not None and last.status.value == "failed":
        return CheckStatus.DOWN
    succeeded = load["ultima_exitosa"]
    limit = timedelta(seconds=load["intervalo_s"] * _STALE_INTERVALS)
    if load["habilitada"] and (succeeded is None or now - succeeded > limit):
        return CheckStatus.DOWN
    freshness = overview["frescura"] or {}
    failed_run = any(row["ultima_estado"] == "failed" for row in overview["datos_listos_hoy"])
    late = (freshness.get("atrasadas") or 0) + (freshness.get("nunca_extraidas") or 0)
    if late or failed_run or (last.status is not None and last.status.value == "partial"):
        return CheckStatus.DEGRADED
    return CheckStatus.UP


class MonitoringBoardService:
    """Summarize every monitored platform for the TI general board."""

    def __init__(
        self,
        applications: ApplicationsOverview,
        warehouse: WarehouseOverview,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            applications (ApplicationsOverview): The application monitoring.
            warehouse (WarehouseOverview): The data warehouse monitoring.
            clock (Callable[[], datetime]): Current time, replaceable in tests.
        """

        self.applications = applications
        self.warehouse = warehouse
        self.clock = clock

    async def board(self, principal: Principal) -> dict[str, Any]:
        """Overall status, the applications and the warehouse.

        Both overviews check the caller is from TI or an administrator.

        Args:
            principal (Principal): The caller.

        Returns:
            dict[str, Any]: ``estado_general``, ``aplicativos`` and ``datawarehouse``.
        """

        now = self.clock()
        apps = await self.applications.overview(principal)
        dwh = await self.warehouse.overview(principal)
        dwh_status = warehouse_status(dwh, now)
        freshness = dwh["frescura"] or {}
        return {
            "generado_en": now,
            "estado_general": worst([apps["estado"], dwh_status]),
            "aplicativos": apps,
            "datawarehouse": {
                "estado": dwh_status,
                "pct_fresca_programadas": freshness.get("pct_fresca_programadas"),
                "atrasadas": freshness.get("atrasadas"),
                "nunca_extraidas": freshness.get("nunca_extraidas"),
                "secuencias_activas": dwh["secuencias_activas"],
                "secuencias_en_curso": dwh["secuencias_en_curso"],
                "secuencias_fallidas": sum(
                    1 for row in dwh["datos_listos_hoy"] if row["ultima_estado"] == "failed"
                ),
                "ultima_carga": dwh["carga"]["ultima"],
                "ultima_carga_exitosa": dwh["carga"]["ultima_exitosa"],
            },
        }
