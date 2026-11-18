"""DWH cost of the supports with the quoter's rule, and how the figures use it."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.modules.quotations.domain.entities import CostInfo
from app.modules.supplier_supports.application.costs import (
    BranchPlan,
    CostTarget,
    SupportCostService,
    plan_key,
)
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import derive

D = Decimal


class FakeRepository:
    def __init__(self, targets, fields=None):
        self._targets = targets
        self.saved = []
        self.fields = fields or {}

    async def margin_fields(self, nits):
        return {nit: self.fields[nit] for nit in nits if nit in self.fields}

    async def targets(self, support_ids=None):
        if support_ids is None:
            return self._targets
        return [target for target in self._targets if target.support_id in set(support_ids)]

    async def save(self, costs, now):
        self.saved = list(costs)
        return len(costs)


class FakeWarehouse:
    """Costs by warehouse plan: the cold room of Medellín is the most expensive."""

    def __init__(self):
        self.calls = []

    async def branches(self, nits):
        return {
            "900": [
                BranchPlan("001", "001", True),  # Antioquia list
                BranchPlan("003", "003", True),  # Centro list
                BranchPlan("002", "002", False),  # inactive branch: ignored
            ],
            "901": [BranchPlan("1M", "001", True)],  # Mayoristas Antioquia (MDE1/MDE3)
            # A list outside the skill: the center gives the warehouses (Centro).
            "902": [BranchPlan("77X", "003", True)],
            "903": [BranchPlan("77X", "003", True)],
        }

    async def costs(self, skus, plan):
        self.calls.append(plan_key(plan))
        by_plan = {
            "MDE1/MDE3": CostInfo(
                "1128", D("100.4"), None, D("98"), D(5), "ABARROTES", True, date(2026, 11, 10)
            ),
            "BTA9/BTA8": CostInfo(
                "1128", D("90"), None, D("120.2"), D(5), "ABARROTES", True, date(2026, 11, 10)
            ),
            "lista1-mayor": CostInfo(
                "1128", D("1"), None, D("110"), D(5), "ABARROTES", True, date(2026, 11, 10)
            ),
        }
        info = by_plan.get(plan_key(plan))
        return {sku: info for sku in skus if info is not None}


async def test_the_highest_cost_of_the_clients_plans_with_the_list_factor() -> None:
    client, everyone = uuid4(), uuid4()
    repo = FakeRepository([CostTarget(client, "1128", "900"), CostTarget(everyone, "1128", None)])
    warehouse = FakeWarehouse()

    summary = await SupportCostService(repo, warehouse).run()

    costs = {cost.support_id: cost for cost in repo.saved}
    # Antioquia 100.4 (quotation) vs Centro 120.2 (inventory): the higher, rounded up, times
    # the general family's 1,004 (the quoter's very cost).
    assert costs[client].unit_cost == D("121.4840") and costs[client].source == "inventario"
    assert costs[client].plan_key == "BTA9/BTA8"
    # No client: list 1, highest among its warehouses; a 1-peso quotation never counts.
    assert costs[everyone].unit_cost == D("110.4400") and costs[everyone].plan_key == "lista1-mayor"
    assert "OCC1/OCC16" not in warehouse.calls  # the inactive branch
    assert summary["with_cost"] == 2 and summary["without_cost"] == 0


async def test_the_wholesale_family_carries_no_factor() -> None:
    by_list, by_margin, general = uuid4(), uuid4(), uuid4()
    repo = FakeRepository(
        [
            CostTarget(by_list, "1128", "901"),  # a wholesale list of the skill (1M)
            CostTarget(by_margin, "1128", "902"),  # a list outside it: the margin list decides
            CostTarget(general, "1128", "903"),  # same list, Food service margin
        ],
        fields={"902": "margin_wholesale", "903": "margin_list"},
    )

    await SupportCostService(repo, FakeWarehouse()).run()

    costs = {cost.support_id: cost.unit_cost for cost in repo.saved}
    assert costs[by_list] == D("101.0000")  # 100.4 rounded up, no factor
    assert costs[by_margin] == D("121.0000") and costs[general] == D("121.4840")


def test_the_figures_use_the_dwh_cost_per_unit() -> None:
    data = SupportData(
        kind="tactico_cliente",
        calc_method="pct_costo",
        supplier_value=D("0.10"),
        reference_cost=D("999"),
        value_unit="caja",
        pack_factor=D("12"),
        target_margin_pct=D("0.2"),
    )

    derived = derive(data, dwh_unit_cost=D("100"), product_pack_factor=D("12"))

    assert derived.cost_basis == "dwh" and derived.cost.unit == D("100")
    assert derived.supplier_amount == D("10.0")  # 10 % of 100 per unit
    assert derived.supplier_amount_per.box == D("120.0")  # a box of 12
    assert derive(data).cost_basis == "referencia"
    assert derive(data).cost.unit == D("83.25")  # 999 per box / 12


def test_the_supported_margin_sits_on_the_recomposed_margin() -> None:
    # 10 % over cost; the product's optimal margin (margin master) is 20 %.
    data = SupportData(
        kind="tactico_cliente",
        calc_method="pct_costo",
        supplier_value=D("0.10"),
        value_unit="unidad",
        supported_margin_pct=D("0.12"),
    )

    derived = derive(data, dwh_unit_cost=D("80"), product_margin=D("0.20"))

    assert derived.list_price.unit == D("100")  # 80 / (1 - 20 %)
    assert derived.net_cost == D("72.00")  # 80 - 10 % of 80
    # With JDH's minimum the recomposed margin is the one at the minimum price: that margin.
    assert round(derived.margin_recomposed, 6) == D("0.12")
    assert derived.recomposed_list_margin == derived.margin_recomposed
    assert derived.supported_price.quantize(D("0.01")) == D("81.82")  # 72 / (1 - 12 %)
    assert derived.customer_discount_pct is None  # disabled for now
    assert derived.product_margin_pct == D("0.20") and derived.supported_margin_pct == D("0.12")
    # Without a JDH margin there is no minimum price; the suggested one is PL - $Apoyo.
    plain = derive(replace(data, supported_margin_pct=None), D("80"), D("0.20"))
    assert plain.supported_price is None and plain.suggested_price == D("92.00")


def test_a_sku_that_is_the_box_is_not_divided_again() -> None:
    # Ohra: the SKU is a case of 18 bottles (pack factor 1 in the master); the sheet says 18.
    data = SupportData(
        kind="tactico_cliente",
        calc_method="costo_neto",
        supplier_value=D("100000"),
        value_unit="caja",
        pack_factor=D("18"),
    )

    derived = derive(data, dwh_unit_cost=D("108000"), product_pack_factor=D("1"))

    assert derived.net_cost == D("100000") and derived.supplier_amount == D("8000")
