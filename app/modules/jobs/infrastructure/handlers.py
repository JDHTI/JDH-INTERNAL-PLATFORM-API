"""The work behind each job key: the master syncs of the masters modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.modules.commercial_masters.application.sync import MasterSyncService
from app.modules.jobs.domain.ports import JobHandler
from app.modules.jobs.domain.registry import IDENTITY_MASTERS
from app.modules.masters.application.sync import MasterCatalogSync


def _catalog(build: Callable[[], MasterCatalogSync], only: Sequence[str]) -> JobHandler:
    async def handler() -> Mapping[str, Any]:
        reports = await build().run(only=only, apply=True)
        return {
            "masters": [report.as_detail() for report in reports],
            "errors": [f"{r.slug}: {r.error}" for r in reports if r.error],
        }

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


def build_job_handlers(
    catalog_sync: Callable[[], MasterCatalogSync],
    commercial_sync: Callable[[], MasterSyncService],
) -> dict[str, JobHandler]:
    """Bind every job key of the registry to its work.

    Args:
        catalog_sync (Callable[[], MasterCatalogSync]): Builds the catalog sync.
        commercial_sync (Callable[[], MasterSyncService]): Builds the commercial sync.

    Returns:
        dict[str, JobHandler]: Job key -> handler.
    """

    return {
        "masters-sync": _catalog(catalog_sync, IDENTITY_MASTERS),
        "masters-sync-products": _catalog(catalog_sync, ("productos",)),
        "commercial-sync-identity": _commercial(commercial_sync, ("suppliers", "customers")),
        "commercial-sync-products": _commercial(
            commercial_sync, ("commercial_suppliers", "products")
        ),
        "commercial-sync-supply": _commercial(commercial_sync, ("unit_costs", "product_suppliers")),
    }
