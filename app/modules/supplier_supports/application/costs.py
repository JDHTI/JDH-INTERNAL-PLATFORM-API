"""The cost of every support from the DWH, with the quoter's rule (daily job).

The rule is the quoter's (``quotations/domain/pricing.py``, skill ``lista-precios``): the
highest of the supplier's quotation in force and the inventory average cost, per inventory
unit, rounded up to pesos. Which warehouses: the client's price list (or billing operation
center) gives them, as when quoting that client; a client with several branches gets the
highest of their plans; a support for every client uses list 1, the highest among its
warehouses. The cost carries the factor of the client's list family, as the quoter's
(``pricing.COST_FACTOR``: 1,004, none for the wholesale lists; BI, 2026-11-18), so the
master, the preview and the order validator use the quoter's very cost. The family is the
list's when the skill names it, else the client's margin list's.

The result is stored locally (``supplier_support_costs``) so the screens never wait for the
DWH; the job runs when the warehouse publishes the supply data (``abastecimiento``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.modules.order_validation.domain.rules import BASIS
from app.modules.quotations.domain import pricing, vocabulary
from app.modules.quotations.domain.entities import CostInfo
from app.modules.quotations.domain.pricing import StockPlan
from app.modules.supplier_supports.domain.entities import SupportCost

ONE = Decimal(1)
logger = logging.getLogger(__name__)


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

    async def targets(self, support_ids: Sequence[UUID] | None = None) -> list[CostTarget]: ...

    async def save(self, costs: Sequence[SupportCost], now: datetime) -> int: ...

    async def margin_fields(self, nits: Sequence[str]) -> dict[str, str]:
        """``{nit: margin list field}`` of the clients (``customer_margin_field``)."""
        ...


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

    return [plan for plan, _ in client_options(branches)]


def client_options(
    branches: Sequence[BranchPlan], basis: str | None = None
) -> list[tuple[StockPlan, Decimal]]:
    """The plans of a client's active branches, each with the cost factor of its list family
    (the list's when the skill names it, else ``basis``, the client's margin list)."""

    chosen = [branch for branch in branches if branch.is_active] or list(branches)
    options: dict[str, tuple[StockPlan, Decimal]] = {}
    for branch in chosen:
        plan = pricing.stock_plan(branch.price_list_id, branch.billing_center)
        options.setdefault(plan_key(plan), (plan, cost_factor(branch.price_list_id, basis)))
    return list(options.values()) or [(GENERAL_PLAN, cost_factor(None, basis))]


def cost_factor(price_list_id: str | None, basis: str | None) -> Decimal:
    """The quoter's factor on the cost of a list family (1,004; 1 for the wholesale lists)."""

    return pricing.COST_FACTOR.get(pricing.family(price_list_id, basis), ONE)


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
        costs, without, plans, clients = await self.compute(targets)
        saved = await self.repository.save(costs, self._clock())
        return {
            "supports": len(targets),
            "with_cost": saved - without,
            "without_cost": without,
            "plans": plans,
            "clients": clients,
        }

    async def refresh(self, support_ids: Sequence[UUID]) -> int:
        """Compute now the cost of these supports (a new or edited support does not wait for
        the daily job). Best effort: the DWH being down leaves them for the job."""

        if not support_ids:
            return 0
        try:
            targets = await self.repository.targets(support_ids)
            if not targets:
                return 0
            costs, *_ = await self.compute(targets)
            return await self.repository.save(costs, self._clock())
        except Exception as exc:  # noqa: BLE001 - runs after the response; the job retries daily
            logger.warning(
                "supports costs: refresh of %d supports skipped: %s", len(support_ids), exc
            )
            return 0

    async def cost_of(self, product_sku: str, customer_nit: str | None) -> SupportCost:
        """The cost a support of this SKU and customer would get (form preview).

        Raises:
            DependencyUnavailableError: If the DWH cannot be read.
        """

        costs, *_ = await self.compute([CostTarget(uuid4(), product_sku, customer_nit)])
        return costs[0]

    async def compute(
        self, targets: Sequence[CostTarget]
    ) -> tuple[list[SupportCost], int, list[str], int]:
        """The cost of each target; also how many had none, the plans and the clients read."""

        nits = sorted({target.customer_nit for target in targets if target.customer_nit})
        branches = await self.warehouse.branches(nits) if nits else {}
        fields = await self.repository.margin_fields(nits) if nits else {}
        plans_of: dict[UUID, list[tuple[StockPlan, Decimal]]] = {}
        skus_by_plan: dict[str, tuple[StockPlan, set[str]]] = {}
        for target in targets:
            options = (
                client_options(
                    branches.get(target.customer_nit, []),
                    BASIS.get(fields.get(target.customer_nit, ""), "lista"),
                )
                if target.customer_nit
                else [(GENERAL_PLAN, cost_factor(None, None))]
            )
            plans_of[target.support_id] = options
            for plan, _ in options:
                skus_by_plan.setdefault(plan_key(plan), (plan, set()))[1].add(target.product_sku)

        infos: dict[str, dict[str, CostInfo]] = {}
        for key, (plan, skus) in skus_by_plan.items():
            infos[key] = await self.warehouse.costs(sorted(skus), plan)

        costs: list[SupportCost] = []
        without = 0
        for target in targets:
            best: tuple[Decimal, StockPlan, CostInfo, str] | None = None
            fallback: tuple[StockPlan, CostInfo | None] | None = None
            # A plan without stock of the SKU does not compete when another has some.
            own = pricing.prefer_stocked(
                {
                    plan_key(plan): {target.product_sku: infos[plan_key(plan)][target.product_sku]}
                    for plan, _ in plans_of[target.support_id]
                    if target.product_sku in infos[plan_key(plan)]
                }
            )
            for plan, factor in plans_of[target.support_id]:
                info = own.get(plan_key(plan), {}).get(target.product_sku)
                fallback = fallback or (plan, info)
                if info is None:
                    continue
                cost, source = pricing.effective_cost(info.quote_cost, info.inventory_cost, factor)
                if cost is not None and source and (best is None or cost > best[0]):
                    best = (cost, plan, info, source)
            if best is None:
                without += 1
                plan, info = fallback or (GENERAL_PLAN, None)
                costs.append(_cost(target, plan, info, None, None))
                continue
            cost, plan, info, source = best
            costs.append(_cost(target, plan, info, cost, source))
        return costs, without, sorted(skus_by_plan), len(nits)


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
    "client_options",
    "client_plans",
    "cost_factor",
    "plan_key",
]
