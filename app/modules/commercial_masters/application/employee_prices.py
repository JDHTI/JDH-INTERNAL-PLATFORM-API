"""Build the JDH employee price list from the master and the MDE1 costs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.commercial_masters.domain.employee_prices import (
    DEFAULT_EMPLOYEE_MARGIN,
    EMPLOYEE_WAREHOUSE,
    EmployeePrice,
    EmployeePriceList,
    employee_price,
    sale_vat,
    with_vat,
)
from app.modules.commercial_masters.domain.ports import (
    CommercialMasterRepository,
    WarehouseCostSource,
)

logger = logging.getLogger(__name__)


class EmployeePriceService:
    """The employee price list: every active product priced from its MDE1 cost."""

    def __init__(self, repository: CommercialMasterRepository, costs: WarehouseCostSource) -> None:
        """Create the service.

        Args:
            repository (CommercialMasterRepository): Products and their employee margin.
            costs (WarehouseCostSource): Average costs by warehouse.
        """

        self.repository = repository
        self.costs = costs

    async def price_list(self) -> EmployeePriceList:
        """Price every active product with an MDE1 cost at its employee margin.

        Returns:
            EmployeePriceList: The lines by SKU and how many products had no cost.

        Raises:
            DependencyUnavailableError: If the data warehouse does not answer.
        """

        try:
            costs = {
                item.sku: item for item in await self.costs.warehouse_costs(EMPLOYEE_WAREHOUSE)
            }
        except DataProviderUnavailable as exc:
            logger.warning("employee prices: warehouse query failed: %r", exc.__cause__ or exc)
            raise DependencyUnavailableError(
                "No se pudo consultar el datawarehouse; inténtalo de nuevo en unos minutos"
            ) from exc
        items: list[EmployeePrice] = []
        without_cost = 0
        for product in await self.repository.employee_price_products():
            cost = costs.get(product.sku)
            margin = product.margin if product.margin is not None else DEFAULT_EMPLOYEE_MARGIN
            price = employee_price(cost.cost if cost else None, margin)
            if cost is None or price is None:
                without_cost += 1
                continue
            # SIESA's product as of today wins; the master's copy only when SIESA says nothing.
            vat = sale_vat(cost.vat_pct, cost.tax_group)
            if vat is None and cost.tax_group is None:
                vat = product.vat_pct
            items.append(
                EmployeePrice(
                    sku=product.sku,
                    description=product.description,
                    brand=product.brand,
                    inventory_unit=product.inventory_unit,
                    cost=cost.cost,
                    margin=margin,
                    price=price,
                    vat_pct=vat,
                    price_with_vat=with_vat(price, vat) or price,
                    tax_group_id=cost.tax_group_id,
                    tax_group=cost.tax_group,
                )
            )
        dates = [item.snapshot_date for item in costs.values() if item.snapshot_date]
        return EmployeePriceList(
            warehouse=EMPLOYEE_WAREHOUSE,
            generated_at=datetime.now(UTC),
            snapshot_date=max(dates) if dates else None,
            items=tuple(items),
            without_cost=without_cost,
            unknown_vat=sum(1 for item in items if item.vat_pct is None),
        )
