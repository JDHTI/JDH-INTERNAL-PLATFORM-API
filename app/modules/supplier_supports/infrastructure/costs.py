"""Adapters of the support costs: the local table and the DWH reads."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.infrastructure.database import Database
from app.modules.quotations.domain.entities import CostInfo
from app.modules.quotations.domain.pricing import StockPlan
from app.modules.quotations.infrastructure.dwh_source import WarehouseQuotationSource
from app.modules.supplier_supports.application.costs import BranchPlan, CostTarget
from app.modules.supplier_supports.domain.entities import SupportCost
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSupportCostModel,
    SupplierSupportModel,
)

logger = logging.getLogger(__name__)
_CHUNK = 1000
# NITs per query to ``consumo.clientes``.
_NIT_CHUNK = 2000

S = SupplierSupportModel
C = SupplierSupportCostModel


class SqlAlchemyCostRepository:
    """The live supports with a SKU and their stored DWH cost."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def targets(self) -> list[CostTarget]:
        """Every support that is not voided and has a JDH SKU."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(S.id, S.product_sku, S.customer_nit).where(
                    S.status != "anulado", S.product_sku.is_not(None)
                )
            )
            return [CostTarget(row[0], row[1], row[2]) for row in rows.all()]

    async def save(self, costs: Sequence[SupportCost], now: datetime) -> int:
        """Upsert the costs (one row per support)."""

        rows = [
            {
                "support_id": cost.support_id,
                "product_sku": cost.product_sku,
                "plan_key": cost.plan_key,
                "plan_label": cost.plan_label,
                "unit_cost": cost.unit_cost,
                "source": cost.source,
                "quote_cost": cost.quote_cost,
                "inventory_cost": cost.inventory_cost,
                "snapshot_date": cost.snapshot_date,
                "computed_at": now,
            }
            for cost in costs
        ]
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(rows), _CHUNK):
                statement = insert(C).values(rows[start : start + _CHUNK])
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[C.support_id],
                        set_={
                            name: statement.excluded[name]
                            for name in rows[0]
                            if name != "support_id"
                        },
                    )
                )
        return len(rows)


class WarehouseCostSource:
    """Clients' price lists and the costs of a plan, read from the DWH (read-only user)."""

    def __init__(
        self,
        connection: DataProviderConnection,
        quotations: WarehouseQuotationSource,
        schema: str = "consumo",
        timeout: float | None = 30.0,
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): The read-only DWH pool.
            quotations (WarehouseQuotationSource): The quoter's reads (its cost query).
            schema (str): Schema of the ``consumo`` views.
            timeout (float | None): Seconds per query.
        """

        if not schema.isidentifier():
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.quotations = quotations
        self.schema = schema
        self.timeout = timeout

    async def branches(self, nits: Sequence[str]) -> dict[str, list[BranchPlan]]:
        """Each client's branches with their price list and billing center."""

        found: dict[str, list[BranchPlan]] = {}
        for start in range(0, len(nits), _NIT_CHUNK):
            try:
                rows = await self.connection.fetch(
                    'SELECT "nit", "lista_precio_id", "cod_co_factura", "estado", '
                    f'"estado_sucursal" FROM "{self.schema}"."clientes" '
                    'WHERE "nit" = ANY($1::text[])',
                    list(nits[start : start + _NIT_CHUNK]),
                    timeout=self.timeout,
                )
            except DataProviderUnavailable as exc:
                logger.warning("supports costs: warehouse query failed: %s", exc.__cause__ or exc)
                raise DependencyUnavailableError("No se pudo consultar el datawarehouse") from exc
            for row in rows:
                found.setdefault(str(row["nit"]).strip(), []).append(
                    BranchPlan(
                        price_list_id=_strip(row["lista_precio_id"]),
                        billing_center=_strip(row["cod_co_factura"]),
                        is_active=row["estado"] == "A" and row["estado_sucursal"] == "A",
                    )
                )
        return found

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]:
        """The quoter's cost query for these SKUs and warehouses."""

        result: dict[str, CostInfo] = {}
        for start in range(0, len(skus), _CHUNK):
            result.update(await self.quotations.costs(skus[start : start + _CHUNK], plan))
        return result


def _strip(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


__all__ = ["SqlAlchemyCostRepository", "WarehouseCostSource"]
