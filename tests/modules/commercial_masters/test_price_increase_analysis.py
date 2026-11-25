"""Price increase analysis: how much each reference moved in a period, cached per version."""

import asyncio
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.errors import DependencyUnavailableError, InvalidInputError
from app.main import create_app
from app.modules.commercial_masters.application.price_increases import (
    MAX_CACHED_WINDOWS,
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
from app.modules.commercial_masters.domain.price_increases import (
    FilterOptions,
    MonthOption,
    Placement,
    PriceIncreaseFilters,
    PriceIncreasePage,
    Scope,
    SupplierOption,
    place_options,
    scope_of,
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
        "supplier_code": "100",
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
    # A placeholder cost of one peso is no cost. Another supplier.
    row("E", date(2026, 4, 1), "1", "30000", "subio", supplier_code="200", supplier_name="ALPINA"),
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


def test_increases_chained_on_the_same_day_follow_the_workbook_order() -> None:
    # sku 15143 on 2021-09-24: 15,519 to 15,984.36, then to 16,463.89 (the same dates).
    second = row("F", date(2021, 9, 24), "15984.36", "16463.89", "subio", occurrence=2)
    first = row("F", date(2021, 9, 24), "15519", "15984.36", "subio")

    result = analyze([second, first], None)[0]

    assert (result.start_cost, result.end_cost) == (Decimal(15519), Decimal("16463.89"))
    assert result.increases == 2


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


def test_a_window_leaves_out_what_comes_after_it() -> None:
    # February to April 2026: A's second increase (May) is after the window.
    result = {item.sku: item for item in analyze(ROWS, date(2026, 2, 1), date(2026, 4, 30))}

    assert set(result) == {"A", "C", "E"}
    a = result["A"]
    assert (a.start_cost, a.end_cost) == (Decimal(1000), Decimal(1050))
    assert a.accumulated_pct == Decimal("0.05") and a.increases == 1
    assert a.last_increased_on == date(2026, 2, 1)
    assert len(in_period(ROWS, date(2026, 2, 1), date(2026, 4, 30))) == 3
    # Open at the start: everything up to the last day.
    assert {item.sku for item in analyze(ROWS, None, date(2025, 12, 31))} == {"A", "D"}


def test_periods_end_today() -> None:
    assert period_start("1m", TODAY) == date(2026, 10, 11)
    assert period_start("1m", date(2026, 3, 31)) == date(2026, 2, 28)
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
    def __init__(self, rows=ROWS) -> None:
        self.reads = 0
        self.stamp = (8, datetime(2026, 11, 11, 8, 0))
        self.rows = list(rows)
        self.pages = []

    async def version(self):
        return self.stamp

    async def analysis_rows(self):
        self.reads += 1
        return list(self.rows)

    async def page(self, filters, page, size):
        self.pages.append(filters)
        return PriceIncreasePage((), 0, page, size, {})

    async def filter_options(self):
        return FilterOptions(
            suppliers=(SupplierOption("200", "ALPINA", 1), SupplierOption("100", "DIVERSEY", 4)),
            months=(MonthOption("2026-06", 1, 1), MonthOption("2026-05", 1, 1)),
        )


@pytest.fixture
def repository() -> FakeAnalysisRepository:
    return FakeAnalysisRepository()


@pytest.fixture
def cache() -> AnalysisCache:
    return AnalysisCache()


class FakePlacements:
    """A in Antioquia (installation 1); B in Bogotá (8); E in both."""

    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.reads = 0

    async def placements(self):
        self.reads += 1
        if self.down:
            raise DependencyUnavailableError("down")
        return [
            Placement("A", "001", "1", "ANTIOQUIA", "PRINCIPAL"),
            Placement("B", "003", "8", "CENTRO", "BOGOTA"),
            Placement("E", "001", "1", "ANTIOQUIA", "PRINCIPAL"),
            Placement("E", "003", "8", "CENTRO", "BOGOTA"),
            # An installation with warehouses in two centers.
            Placement("E", "004", "7", "COSTA", "CARTAGENA"),
            Placement("C", "001", "7", "ANTIOQUIA", None),
        ]


def service_for(repository, cache, placements=None) -> PriceIncreaseService:
    return PriceIncreaseService(
        repository=repository,
        managers=FakeManagers([]),
        today=lambda: TODAY,
        cache=cache,
        placements=placements,
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

    # A calendar month too.
    await ask(service_for(repository, cache), since=date(2026, 2, 1), until=date(2026, 2, 28))

    assert repository.reads == 1
    assert first.total == 4 and (first.since, first.until) == (SINCE, None)
    assert [item.sku for item in first.items][0] == "A"
    assert len(cache.periods) == 3

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
    # The supplier is not searched as text: it is picked by code.
    assert (await ask(service, q="alpina")).total == 0
    by_supplier = await ask(service, supplier="200")
    assert [item.sku for item in by_supplier.items] == ["E"]
    assert (by_supplier.summary.references, by_supplier.summary.increases) == (1, 1)
    paged = await ask(service, sort="sku", descending=False, size=3, page=2)
    assert [item.sku for item in paged.items] == ["E"] and paged.total == 4
    month = await ask(service, since=date(2026, 6, 1), until=date(2026, 6, 30))
    assert [item.sku for item in month.items] == ["B"]
    assert (month.since, month.until) == (date(2026, 6, 1), date(2026, 6, 30))
    with pytest.raises(InvalidInputError):
        await ask(service, period="5y")
    with pytest.raises(InvalidInputError):
        await ask(service, movement="otro")
    with pytest.raises(InvalidInputError):
        await ask(service, since=date(2026, 6, 30), until=date(2026, 6, 1))


async def test_a_center_or_installation_analyzes_its_references_and_regional(cache) -> None:
    # A went up nationally; Bogotá got its own, higher cost for A in March.
    rows = [*ROWS, row("A", date(2026, 3, 1), "1050", "1300", "subio", regional=" bogota")]
    repository = FakeAnalysisRepository(rows)
    placements = FakePlacements()
    service = service_for(repository, cache, placements)

    whole = {item.sku: item for item in (await ask(service)).items}
    antioquia = await ask(service, co="001")
    bogota = await ask(service, co="003")
    principal = await ask(service, installation="1")
    both = await ask(service, co="003", installation="8")

    # The whole history mixes Bogotá's row in: three increases for A.
    assert whole["A"].increases == 3
    # Antioquia: its references (A, C, E), A without Bogotá's row.
    by_sku = {item.sku: item for item in antioquia.items}
    assert set(by_sku) == {"A", "C", "E"} and by_sku["A"].increases == 2
    assert by_sku["A"].end_cost == Decimal("1102.5")
    # Bogotá: B and E only (A is not set up there).
    assert {item.sku for item in bogota.items} == {"B", "E"}
    assert {item.sku for item in principal.items} == {"A", "E"}
    assert {item.sku for item in both.items} == {"B", "E"}
    assert antioquia.summary.references == 3
    # Read once per process (the source caches); the history once per version.
    assert repository.reads == 1


async def test_a_bogota_reference_keeps_its_own_regional_rows(cache) -> None:
    rows = [row("B", date(2026, 3, 1), "2200", "2500", "subio", regional="BOGOTA")]
    repository = FakeAnalysisRepository([*ROWS, *rows])
    service = service_for(repository, cache, FakePlacements())

    bogota = {item.sku: item for item in (await ask(service, co="003")).items}

    assert bogota["B"].increases == 2 and bogota["B"].changes == 3


async def test_the_history_listing_takes_the_scope(cache) -> None:
    repository = FakeAnalysisRepository()
    service = service_for(repository, cache, FakePlacements())

    await service.page(PriceIncreaseFilters(sku="A"), 1, 50, co="001")
    await service.page(PriceIncreaseFilters(sku="A"), 1, 50)

    scoped, plain = repository.pages
    assert scoped.scope == Scope(frozenset({"A", "C", "E"}), frozenset({"001"}))
    assert scoped.scope.regionals == {"ANTIOQUIA"} and plain.scope is None
    assert scoped.scope.holds("A", None) and scoped.scope.holds("A", " antioquia ")
    assert scoped.scope.holds("A", "NACIONAL") and not scoped.scope.holds("A", "BOGOTA")
    assert not scoped.scope.holds("B", None)


async def test_without_the_warehouse_the_filter_fails_and_the_options_go_without_it(
    repository, cache
) -> None:
    service = service_for(repository, cache, FakePlacements(down=True))

    with pytest.raises(DependencyUnavailableError):
        await ask(service, co="001")
    options = await service.filter_options()

    assert options.cos == () and options.installations == () and options.suppliers
    # Without a source at all, the filter is not available either.
    with pytest.raises(DependencyUnavailableError):
        await ask(service_for(repository, cache), installation="8")
    # No center nor installation: the warehouse is not asked.
    assert (await ask(service)).total == 4


def test_the_options_list_centers_and_installations() -> None:
    cos, installations = place_options(asyncio.run(FakePlacements().placements()))

    assert [(item.code, item.name) for item in cos] == [
        ("001", "ANTIOQUIA"),
        ("003", "CENTRO"),
        ("004", "COSTA"),
    ]
    assert [(item.code, item.name, item.cos) for item in installations] == [
        ("8", "BOGOTA", ("003",)),
        ("7", "CARTAGENA", ("001", "004")),
        ("1", "PRINCIPAL", ("001",)),
    ]
    assert scope_of([], "009", None) == Scope(frozenset(), frozenset({"009"}))
    assert scope_of([], "009", None).regionals == {"BARRANQUILLA"}


async def test_the_cache_keeps_a_bounded_number_of_windows(repository, cache) -> None:
    service = service_for(repository, cache)
    for day in range(1, MAX_CACHED_WINDOWS + 6):
        await ask(service, since=date(2026, 1, 1), until=date(2026, 1, 1) + timedelta(days=day))

    assert len(cache.periods) == MAX_CACHED_WINDOWS and repository.reads == 1


# ------------------------------------------------------------------ http


def test_the_analysis_route(repository, cache) -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        uuid4(), "bi@jdh.com.co", "BI", True
    )
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.price_increase_service.override(
            service_for(repository, cache, FakePlacements())
        ),
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
        assert item["supplier_code"] == "100" and body["until"] is None
        # Two years take June 2025 in: gaps of 245 and 90 days, median 168.
        assert item["frequency_days"] == 168 and item["start_cost"] == "900"
        bad = client.get("/api/v1/commercial/price-increases/analysis", params={"period": "5y"})
        assert bad.status_code == 422
        month = client.get(
            "/api/v1/commercial/price-increases/analysis",
            params={"since": "2026-05-01", "until": "2026-05-31", "supplier": "100"},
        ).json()
        assert (month["since"], month["until"], month["total"]) == ("2026-05-01", "2026-05-31", 1)
        backwards = client.get(
            "/api/v1/commercial/price-increases/analysis",
            params={"since": "2026-05-31", "until": "2026-05-01"},
        )
        assert backwards.status_code == 422
        options = client.get("/api/v1/commercial/price-increases/options")
        assert options.status_code == 200
        assert options.json() == {
            "suppliers": [
                {"code": "200", "name": "ALPINA", "references": 1},
                {"code": "100", "name": "DIVERSEY", "references": 4},
            ],
            "months": [
                {"month": "2026-06", "changes": 1, "references": 1},
                {"month": "2026-05", "changes": 1, "references": 1},
            ],
            "cos": [
                {"code": "001", "name": "ANTIOQUIA"},
                {"code": "003", "name": "CENTRO"},
                {"code": "004", "name": "COSTA"},
            ],
            "installations": [
                {"code": "8", "name": "BOGOTA", "cos": ["003"]},
                {"code": "7", "name": "CARTAGENA", "cos": ["001", "004"]},
                {"code": "1", "name": "PRINCIPAL", "cos": ["001"]},
            ],
        }
        scoped = client.get(
            "/api/v1/commercial/price-increases/analysis", params={"co": "003", "period": "all"}
        )
        assert scoped.status_code == 200
        assert {item["sku"] for item in scoped.json()["items"]} == {"B", "E"}
        too_long = client.get(
            "/api/v1/commercial/price-increases/analysis", params={"co": "x" * 11}
        )
        assert too_long.status_code == 422
