"""The bulk load assigns the DWH cost of its supports itself, in the same transaction; it does
not wait for the daily job (BI, 2026-11-18)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.core.errors import DependencyUnavailableError
from app.modules.quotations.domain.entities import CostInfo
from app.modules.supplier_supports.application.bulk import SupportBulkService
from app.modules.supplier_supports.application.costs import SupportCostService
from app.modules.supplier_supports.infrastructure.bulk_file import SpreadsheetSupportReader

HEADER = "Clase apoyo,Tipo apoyo,Valor apoyo,Fecha Inicio,NIT Cliente,SKU JDH\n"
ROWS = (
    "Apoyo táctico,% Apoyo en Costo,5%,01/07/2026,800116299,1128\n"
    "Actividad descuento temporal,% Apoyo en Costo,5%,01/07/2026,,8749\n"
)


class NoTargets:
    """The costs of a load never come from the stored targets (that is the job's)."""

    async def targets(self, support_ids=None):
        raise AssertionError("the load must not read the stored targets")

    async def save(self, costs, now):
        raise AssertionError("the load saves the costs with the supports")

    async def margin_fields(self, nits):
        return {}


class FakeWarehouse:
    """1128 has a cost in the DWH, 8749 has none; ``down`` makes every read fail."""

    def __init__(self) -> None:
        self.down = False
        self.skus: list[str] = []

    async def branches(self, nits):
        if self.down:
            raise DependencyUnavailableError("No se pudo consultar el datawarehouse")
        return {}

    async def costs(self, skus, plan):
        if self.down:
            raise DependencyUnavailableError("No se pudo consultar el datawarehouse")
        self.skus.extend(skus)
        info = CostInfo(
            "1128", D("1000"), None, D("980"), D(5), "ABARROTES", True, date(2026, 11, 18)
        )
        return {sku: info for sku in skus if sku == "1128"}


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def costed_bulk(service, profiles, warehouse) -> SupportBulkService:
    return SupportBulkService(
        supports=service,
        profiles=profiles,
        reader=SpreadsheetSupportReader(),
        max_rows=50,
        costs=SupportCostService(NoTargets(), warehouse),
    )


async def load(bulk, manager, content: str, **kwargs):
    return await bulk.load(
        manager, "apoyos.csv", content.encode("utf-8"), supplier_code="233", **kwargs
    )


async def test_an_applied_load_saves_the_cost_of_each_support_with_it(
    costed_bulk, repo, manager
) -> None:
    report = await load(costed_bulk, manager, HEADER + ROWS, apply=True)

    assert report.applied and (report.costs_assigned, report.costs_missing) == (1, 1)
    (plan,) = repo.loads
    by_support = {cost.support_id: cost for cost in plan.costs}
    assert set(by_support) == set(report.created)
    # The quoter's cost: the DWH's, rounded up, times the general family's 1,004.
    assert sorted((c.product_sku, c.unit_cost) for c in plan.costs) == [
        ("1128", D("1004.0000")),
        ("8749", None),
    ]


async def test_a_simulation_reads_no_cost(costed_bulk, warehouse, manager) -> None:
    report = await load(costed_bulk, manager, HEADER + ROWS)

    assert not report.applied and warehouse.skus == []
    assert (report.costs_assigned, report.costs_missing) == (0, 0)


async def test_loading_the_same_file_again_costs_its_unchanged_supports(
    costed_bulk, repo, manager
) -> None:
    await load(costed_bulk, manager, HEADER + ROWS, apply=True)

    again = await load(costed_bulk, manager, HEADER + ROWS, apply=True)

    assert again.unchanged == 2 and again.costs_assigned == 1
    assert {cost.support_id for cost in repo.loads[-1].costs} == set(repo.items)


async def test_without_the_dwh_the_load_is_not_saved(costed_bulk, repo, warehouse, manager) -> None:
    warehouse.down = True

    with pytest.raises(DependencyUnavailableError, match="la carga no se guardó"):
        await load(costed_bulk, manager, HEADER + ROWS, apply=True)

    assert repo.items == {} and repo.loads == []
