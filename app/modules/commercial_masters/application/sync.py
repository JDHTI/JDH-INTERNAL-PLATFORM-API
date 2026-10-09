"""Copy the commercial masters from the data warehouse into the platform."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.application import mapping
from app.modules.commercial_masters.domain.entities import SyncReport, clean_nit
from app.modules.commercial_masters.domain.ports import MasterSyncRepository, WarehouseSource

MASTERS = (
    "commercial_suppliers",
    "suppliers",
    "customers",
    "products",
    "unit_costs",
    "product_suppliers",
)
# Existing master_definitions rows (migration 0011) that mirror these syncs.
_DEFINITION_SLUGS = {
    "suppliers": "proveedores",
    "customers": "clientes",
    "products": "productos",
}
NITS_PER_SUPPLIER = 10

Row = Mapping[str, Any]
RowMapper = Callable[[Row], dict[str, Any] | None]


class MasterSyncService:
    """Read ``consumo.*`` in pages and upsert them by warehouse key.

    Full syncs only: SIESA's own timestamps do not mark real modifications, so
    there is no incremental mode. A row that disappears from the warehouse is
    deactivated (or deleted for the purchase bridge) instead of kept stale.
    """

    def __init__(
        self,
        source: WarehouseSource,
        repository: MasterSyncRepository,
        batch_size: int = 2000,
    ) -> None:
        """Create the service.

        Args:
            source (WarehouseSource): Read-only warehouse access.
            repository (MasterSyncRepository): Local master persistence.
            batch_size (int): Rows per warehouse page and per upsert.
        """

        self.source = source
        self.repository = repository
        self.batch_size = batch_size

    async def run(self, only: Sequence[str] | None = None, apply: bool = False) -> list[SyncReport]:
        """Sync the selected masters in dependency order.

        Args:
            only (Sequence[str] | None): Subset of :data:`MASTERS`; all when ``None``.
            apply (bool): Write to the platform; a dry run only reads and counts.

        Returns:
            list[SyncReport]: One report per master.

        Raises:
            InvalidInputError: If a master name is unknown.
        """

        selected = list(only) if only else list(MASTERS)
        unknown = sorted(set(selected) - set(MASTERS))
        if unknown:
            raise InvalidInputError(f"Maestros desconocidos: {', '.join(unknown)}")
        reports = []
        for name in MASTERS:
            if name in selected:
                reports.append(await self._sync(name, apply))
        return reports

    async def _sync(self, name: str, apply: bool) -> SyncReport:
        started = datetime.now(UTC)
        extra: dict[str, int] = {}
        if name == "commercial_suppliers":
            read, upserted = await self._commercial_suppliers(started, apply, extra)
        elif name == "suppliers":
            read, upserted = await self._paged(
                self.source.suppliers(self.batch_size),
                mapping.supplier_row,
                self.repository.upsert_suppliers,
                started,
                apply,
            )
        elif name == "unit_costs":
            read, upserted = await self._unit_costs(apply)
        elif name == "customers":
            read, upserted = await self._paged(
                self.source.customers(self.batch_size),
                mapping.customer_row,
                self.repository.upsert_customers,
                started,
                apply,
            )
        elif name == "products":
            read, upserted = await self._paged(
                self.source.products(self.batch_size),
                mapping.product_row,
                self.repository.upsert_products,
                started,
                apply,
            )
        else:
            read, upserted = await self._paged(
                self.source.product_suppliers(self.batch_size),
                mapping.product_supplier_row,
                self.repository.upsert_product_suppliers,
                started,
                apply,
            )
        deactivated = 0
        if apply and read and name != "unit_costs":
            # Only after a non-empty read: an empty warehouse answer must never
            # wipe the local master.
            deactivated = await self.repository.deactivate_unseen(name, started)
            slug = _DEFINITION_SLUGS.get(name)
            if slug:
                await self.repository.touch_master_definition(slug, started)
        return SyncReport(
            master=name,
            read=read,
            upserted=upserted,
            deactivated=deactivated,
            started_at=started,
            finished_at=datetime.now(UTC),
            extra=extra,
        )

    async def _paged(
        self,
        pages: AsyncIterator[list[Row]],
        mapper: RowMapper,
        upsert: Callable[[Sequence[Row], datetime], Any],
        started: datetime,
        apply: bool,
    ) -> tuple[int, int]:
        read = upserted = 0
        async for page in pages:
            read += len(page)
            rows = [mapped for row in page if (mapped := mapper(row)) is not None]
            if apply and rows:
                upserted += await upsert(rows, started)
            elif rows:
                upserted += len(rows)
        return read, upserted

    async def _unit_costs(self, apply: bool) -> tuple[int, int]:
        costs = await self.source.unit_costs()
        rows = [mapped for row in costs if (mapped := mapping.unit_cost_row(row))]
        if apply and rows:
            await self.repository.update_unit_costs(rows)
        return len(costs), len(rows)

    async def _commercial_suppliers(
        self, started: datetime, apply: bool, extra: dict[str, int]
    ) -> tuple[int, int]:
        labels = await self.source.commercial_suppliers()
        rows = [mapped for row in labels if (mapped := mapping.commercial_supplier_row(row))]
        nits = await self.source.commercial_supplier_nits(NITS_PER_SUPPLIER)
        nit_rows = _primary_nits(nits)
        extra["nits"] = len(nit_rows)
        if apply and rows:
            await self.repository.upsert_commercial_suppliers(rows, started)
            await self.repository.replace_commercial_supplier_nits(nit_rows)
        return len(labels), len(rows)


def _primary_nits(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Flag the NIT with most linked products of each code as primary."""

    best: dict[str, int] = {}
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        code = mapping.text(row.get("cod_proveedor"))
        nit = clean_nit(row.get("nit"))
        if code is None or nit is None:
            continue
        links = int(row.get("productos") or 0)
        cleaned.append({"code": code, "nit": nit, "product_links": links})
        best[code] = max(best.get(code, 0), links)
    primary_seen: set[str] = set()
    for item in sorted(cleaned, key=lambda value: -value["product_links"]):
        code = item["code"]
        item["is_primary"] = code not in primary_seen and item["product_links"] == best[code]
        if item["is_primary"]:
            primary_seen.add(code)
    return cleaned
