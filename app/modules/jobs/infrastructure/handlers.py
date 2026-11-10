"""The work behind each job key: the master caches and the commercial syncs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.modules.commercial_masters.application.sync import MasterSyncService
from app.modules.jobs.domain.ports import JobHandler
from app.modules.masters.application.service import MasterService
from app.modules.supplier_supports.application.costs import SupportCostService


def _warm(build: Callable[[], MasterService], sequence: str) -> JobHandler:
    async def handler() -> Mapping[str, Any]:
        return await build().warm(sequence)

    return handler


def _commercial(build: Callable[[], MasterSyncService], only: Sequence[str]) -> JobHandler:
    async def handler() -> Mapping[str, Any]:
        reports = await build().run(list(only), apply=True)
        return {
            "masters": [
                {
                    "master": report.master,
                    "read": report.read,
                    "upserted": report.upserted,
                    "deactivated": report.deactivated,
                    **report.extra,
                }
                for report in reports
            ],
            "errors": [],
        }

    return handler


def _support_costs(build: Callable[[], SupportCostService]) -> JobHandler:
    async def handler() -> Mapping[str, Any]:
        return await build().run()

    return handler


def build_job_handlers(
    masters: Callable[[], MasterService],
    commercial_sync: Callable[[], MasterSyncService],
    support_costs: Callable[[], SupportCostService],
) -> dict[str, JobHandler]:
    """Bind every job key of the registry to its work.

    Args:
        masters (Callable[[], MasterService]): Builds the master catalog service.
        commercial_sync (Callable[[], MasterSyncService]): Builds the commercial sync.
        support_costs (Callable[[], SupportCostService]): Builds the supports' DWH costs.

    Returns:
        dict[str, JobHandler]: Job key -> handler.
    """

    return {
        "masters-warm-identity": _warm(masters, "identidad"),
        "masters-warm-products": _warm(masters, "reporte_ventas"),
        "commercial-sync-identity": _commercial(commercial_sync, ("suppliers", "customers")),
        "commercial-sync-products": _commercial(
            commercial_sync, ("commercial_suppliers", "products")
        ),
        "commercial-sync-supply": _commercial(commercial_sync, ("unit_costs", "product_suppliers")),
        "supplier-supports-costs": _support_costs(support_costs),
    }
