"""The cost of every support from the DWH, with the quoter's rule (daily job).

The rule is the quoter's (``quotations/domain/pricing.py``, skill ``lista-precios``): the
highest of the supplier's quotation in force and the inventory average cost, per inventory
unit, rounded up to pesos. Which warehouses: the client's price list (or billing operation
center) gives them, as when quoting that client; a client with several branches gets the
highest of their plans; a support for every client uses list 1, the highest among its
warehouses. The price lists' factor (1,004) is not applied: the support's cost is the
purchase cost the supplier's percentage is negotiated on.

The result is stored locally (``supplier_support_costs``) so the screens never wait for the
DWH; the job runs when the warehouse publishes the supply data (``abastecimiento``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.modules.quotations.domain import pricing, vocabulary
from app.modules.quotations.domain.entities import CostInfo
from app.modules.quotations.domain.pricing import StockPlan
from app.modules.supplier_supports.domain.entities import SupportCost

ONE = Decimal(1)


@dataclass(frozen=True)
class CostTarget:
    """A support whose SKU needs a cost."""

    support_id: UUID
    product_sku: str
    customer_nit: str | None


@dataclass(frozen=True)
class BranchPlan:
    """The price list and billing center of one branch of a client."""

    price_list_id: str | None
    billing_center: str | None
    is_active: bool


class CostRepository(Protocol):
    """Where the costs are kept."""

    async def targets(self) -> list[CostTarget]: ...

    async def save(self, costs: Sequence[SupportCost], now: datetime) -> int: ...


class CostWarehouse(Protocol):
    """What the DWH says about clients and costs."""

    async def branches(self, nits: Sequence[str]) -> dict[str, list[BranchPlan]]: ...

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]: ...


def plan_key(plan: StockPlan) -> str:
    """A stable name of a warehouse plan."""

    if plan.highest:
        return "lista1-mayor"
    primary, cold = plan.warehouses or ("", "")
    return f"{primary}/{cold}"


GENERAL_PLAN = pricing.stock_plan(vocabulary.GENERAL_PRICE_LIST, None)


def client_plans(branches: Sequence[BranchPlan]) -> list[StockPlan]:
    """The plans of a client's active branches (all of them if none is active)."""

    chosen = [branch for branch in branches if branch.is_active] or list(branches)
    plans: dict[str, StockPlan] = {}
    for branch in chosen:
        plan = pricing.stock_plan(branch.price_list_id, branch.billing_center)
        plans.setdefault(plan_key(plan), plan)
    return list(plans.values()) or [GENERAL_PLAN]


class SupportCostService:
    """Compute and store the DWH cost of every live support with a SKU."""

    def __init__(
        self,
        repository: CostRepository,
        warehouse: CostWarehouse,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (CostRepository): The supports and their stored costs.
            warehouse (CostWarehouse): Clients and costs in the DWH.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.repository = repository
        self.warehouse = warehouse
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> Mapping[str, Any]:
        """Refresh every cost; returns a summary for the job's run.

        Raises:
            DependencyUnavailableError: If the DWH cannot be read (the job retries later).
        """

        targets = await self.repository.targets()
        nits = sorted({target.customer_nit for target in targets if target.customer_nit})
        branches = await self.warehouse.branches(nits) if nits else {}
        plans_of: dict[UUID, list[StockPlan]] = {}
        skus_by_plan: dict[str, tuple[StockPlan, set[str]]] = {}
        for target in targets:
            plans = (
                client_plans(branches.get(target.customer_nit, []))
                if target.customer_nit
                else [GENERAL_PLAN]
            )
            plans_of[target.support_id] = plans
            for plan in plans:
                skus_by_plan.setdefault(plan_key(plan), (plan, set()))[1].add(target.product_sku)

        infos: dict[str, dict[str, CostInfo]] = {}
        for key, (plan, skus) in skus_by_plan.items():
            infos[key] = await self.warehouse.costs(sorted(skus), plan)

        costs: list[SupportCost] = []
        without = 0
        for target in targets:
            best: tuple[Decimal, StockPlan, CostInfo, str] | None = None
            fallback: tuple[StockPlan, CostInfo | None] | None = None
            for plan in plans_of[target.support_id]:
                info = infos[plan_key(plan)].get(target.product_sku)
                fallback = fallback or (plan, info)
                if info is None:
                    continue
                cost, source = pricing.effective_cost(info.quote_cost, info.inventory_cost, ONE)
                if cost is not None and source and (best is None or cost > best[0]):
                    best = (cost, plan, info, source)
            if best is None:
                without += 1
                plan, info = fallback or (GENERAL_PLAN, None)
                costs.append(_cost(target, plan, info, None, None))
                continue
            cost, plan, info, source = best
            costs.append(_cost(target, plan, info, cost, source))
        saved = await self.repository.save(costs, self._clock())
        return {
            "supports": len(targets),
            "with_cost": saved - without,
            "without_cost": without,
            "plans": sorted(skus_by_plan),
            "clients": len(nits),
        }


def _cost(
    target: CostTarget,
    plan: StockPlan,
    info: CostInfo | None,
    cost: Decimal | None,
    source: str | None,
) -> SupportCost:
    return SupportCost(
        support_id=target.support_id,
        product_sku=target.product_sku,
        plan_key=plan_key(plan),
        plan_label=plan.label,
        unit_cost=cost,
        source=source,
        quote_cost=info.quote_cost if info else None,
        inventory_cost=info.inventory_cost if info else None,
        snapshot_date=info.snapshot_date if info else None,
    )


__all__ = [
    "BranchPlan",
    "CostTarget",
    "SupportCostService",
    "client_plans",
    "plan_key",
]
