"""Price increase analysis: how much each reference moved in a period, cached per version."""

import time
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.errors import InvalidInputError
from app.main import create_app
from app.modules.commercial_masters.application.price_increases import (
    AnalysisCache,
    PriceIncreaseService,
)
from app.modules.commercial_masters.domain.price_increase_analysis import (
    AnalysisRow,
    analyze,
    in_period,
    ordered,
    period_start,
    summarize,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    PriceIncreaseFileReader,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_masters.test_http import AllowAll
from tests.modules.supplier_supports.conftest import FakeManagers

TODAY = date(2026, 11, 11)
SINCE = date(2025, 11, 11)


def row(sku: str, day: date, old: str | None, new: str | None, status=None, **values):
    base = {
        "sku": sku,
        "increased_on": day,
        "notified_on": None,
        "status": status,
        "variation_pct": None,
        "old_cost": Decimal(old) if old else None,
        "old_cost_with_tax": None,
        "new_cost": Decimal(new) if new else None,
        "new_cost_with_tax": None,
        "is_pending": False,
        "description": f"Producto {sku}",
        "supplier_name": "DIVERSEY",
        "category": "ASEO",
    }
    return AnalysisRow(**{**base, **values})


ROWS = [
    # Before the period: the cost it starts at is 1000.
    row("A", date(2025, 6, 1), "900", "1000", "subio"),
    # Up twice in the period, 90 days apart: 1000 -> 1102.5 is +10,25 %. The second row's old
    # cost is mistyped (100): it does not count, only where the cost ends.
    row("A", date(2026, 2, 1), "1000", "1050", "subio"),
    row("A", date(2026, 5, 2), "100", "1102.5", "subio"),
    # Up then back down: ends where it started.
    row("B", date(2026, 1, 10), "2000", "2200", "subio"),
    row("B", date(2026, 6, 10), "2200", "2000", "bajo", is_pending=True),
    # No state in the file and no change: flat. Only before the period: left out.
    row("C", date(2026, 3, 1), "500", "500"),
    row("D", date(2024, 3, 1), "500", "600", "subio"),
    # A placeholder cost of one peso is no cost.
    row("E", date(2026, 4, 1), "1", "30000", "subio"),
]


# ------------------------------------------------------------------ domain


def test_each_reference_compares_its_cost_at_the_end_with_the_start() -> None:
    result = {item.sku: item for item in analyze(ROWS, SINCE)}

    assert set(result) == {"A", "B", "C", "E"}
    a = result["A"]
    assert (a.start_cost, a.end_cost) == (Decimal(1000), Decimal("1102.5"))
    assert a.accumulated_pct == Decimal("0.1025") and a.movement == "subio"
    assert (a.increases, a.decreases, a.changes) == (2, 0, 2)
    assert a.frequency_days == 90 and a.last_increased_on == date(2026, 5, 2)
    # The last row's own change compares its own pair, mistyped as it is.
    assert a.last_status == "subio" and a.last_change_pct == Decimal("10.025")
    b = result["B"]
    assert b.accumulated_pct == Decimal(0) and b.movement == "estable"
    assert b.last_status == "bajo" and b.is_pending and b.frequency_days is None
    c = result["C"]
    assert c.movement == "estable" and c.last_status == "estable"
    assert result["E"].accumulated_pct is None and result["E"].movement is None


def test_the_whole_history_starts_at_the_first_old_cost() -> None:
    result = {item.sku: item for item in analyze(ROWS, None)}

    assert result["A"].start_cost == Decimal(900)
    assert result["D"].accumulated_pct == Decimal("0.2")


def test_the_summary_counts_the_period() -> None:
    analyses = analyze(ROWS, SINCE)

    summary = summarize(in_period(ROWS, SINCE), analyses)

    assert (summary.references, summary.increases, summary.pending) == (4, 4, 1)
    # The median of the increases' own changes (5 %, 10 % and the mistyped 1002,5 %; E has
    # none): the slip does not drag it as it would an average.
    assert summary.typical_increase_pct == Decimal("0.1")
    assert summary.movements == {"subio": 1, "bajo": 0, "estable": 2}


def test_periods_end_today() -> None:
    assert period_start("12m", TODAY) == SINCE
    assert period_start("24m", TODAY) == date(2024, 11, 11)
    assert period_start("all", TODAY) is None
    # A day the earlier month does not have: its last day.
    assert period_start("12m", date(2028, 2, 29)) == date(2027, 2, 28)
    with pytest.raises(ValueError):
        period_start("5y", TODAY)


def test_sorting_keeps_empty_values_last() -> None:
    analyses = analyze(ROWS, SINCE)

    assert [item.sku for item in ordered(analyses, "frequency", True)][0] == "A"
    assert [item.sku for item in ordered(analyses, "accumulated", True)] == ["A", "B", "C", "E"]
    assert [item.sku for item in ordered(analyses, "accumulated", False)] == ["B", "C", "A", "E"]
    assert [item.sku for item in ordered(analyses, "sku", True)] == ["E", "C", "B", "A"]


def test_a_workbook_sized_history_analyzes_quickly() -> None:
    """50 000 rows over 11 718 references, the size of the real workbook."""

    rows = [
        row(
            str(index % 11718),
            date(2021 + index % 6, 1 + index % 12, 1 + index % 28),
            "1000",
            "1020",
            "subio",
        )
        for index in range(50_000)
    ]
    start = time.perf_counter()
    assert len(analyze(rows, None)) == 11718
    assert time.perf_counter() - start < 3


# ------------------------------------------------------------------ service


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.reads = 0
        self.stamp = (8, datetime(2026, 11, 11, 8, 0))

    async def version(self):
        return self.stamp

    async def analysis_rows(self):
        self.reads += 1
        return list(ROWS)


@pytest.fixture
def repository() -> FakeAnalysisRepository:
    return FakeAnalysisRepository()


@pytest.fixture
def cache() -> AnalysisCache:
    return AnalysisCache()


def service_for(repository, cache) -> PriceIncreaseService:
    return PriceIncreaseService(
        repository=repository,
        managers=FakeManagers([]),
        reader=PriceIncreaseFileReader(),
        max_upload_bytes=1024,
        today=lambda: TODAY,
        cache=cache,
    )


def ask(service, **values):
    options = {
        "period": "12m",
        "q": None,
        "movement": None,
        "sort": "accumulated",
        "descending": True,
        "page": 1,
        "size": 50,
    }
    return service.analysis(**{**options, **values})


async def test_the_history_is_read_once_per_data_version(repository, cache) -> None:
    first = await ask(service_for(repository, cache))
    # Another request, another service, another period: the cache outlives them.
    await ask(service_for(repository, cache), movement="subio")
    await ask(service_for(repository, cache), period="all")

    assert repository.reads == 1
    assert first.total == 4 and first.since == SINCE
    assert [item.sku for item in first.items][0] == "A"

    repository.stamp = (9, datetime(2026, 11, 11, 9, 0))
    await ask(service_for(repository, cache))
    assert repository.reads == 2


async def test_filters_search_and_paging(repository, cache) -> None:
    service = service_for(repository, cache)

    flat = await ask(service, movement="estable")
    assert [item.sku for item in flat.items] == ["B", "C"]
    # The counts by movement are of the search, before the movement filter.
    assert flat.summary.movements == {"subio": 1, "bajo": 0, "estable": 2}

    searched = await ask(service, q="a")
    assert [item.sku for item in searched.items] == ["A"] and searched.summary.references == 1
    paged = await ask(service, sort="sku", descending=False, size=3, page=2)
    assert [item.sku for item in paged.items] == ["E"] and paged.total == 4
    with pytest.raises(InvalidInputError):
        await ask(service, period="5y")
    with pytest.raises(InvalidInputError):
        await ask(service, movement="otro")


# ------------------------------------------------------------------ http


def test_the_analysis_route(repository, cache) -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        uuid4(), "bi@jdh.com.co", "BI", True
    )
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.price_increase_service.override(service_for(repository, cache)),
        TestClient(app) as client,
    ):
        response = client.get(
            "/api/v1/commercial/price-increases/analysis",
            params={"period": "24m", "movement": "subio", "sort": "increases"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["since"] == "2024-11-11" and body["total"] == 1
        item = body["items"][0]
        assert item["sku"] == "A" and item["movement"] == "subio"
        # Two years take June 2025 in: gaps of 245 and 90 days, median 168.
        assert item["frequency_days"] == 168 and item["start_cost"] == "900"
        bad = client.get("/api/v1/commercial/price-increases/analysis", params={"period": "5y"})
        assert bad.status_code == 422
