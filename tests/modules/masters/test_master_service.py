"""The master catalog read live from the DWH, its cache by sequence run and access."""

from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.masters.application.service import MasterService
from app.modules.masters.domain.entities import (
    Freshness,
    MasterDetail,
    MasterField,
    MasterSummary,
)
from app.modules.masters.domain.ports import WarehouseUnavailable
from app.modules.masters.infrastructure.cache import MasterReadCache
from tests.modules.fakes import principal

ANA = principal("ana@jdh.com.co")
MORNING = datetime(2026, 11, 5, 8, 42, tzinfo=UTC)
NOON = datetime(2026, 11, 5, 13, 1, tzinfo=UTC)
RUN_1, RUN_2 = uuid4(), uuid4()


def _definition(slug: str, sequence: str | None, *extra: MasterField) -> MasterDetail:
    summary = MasterSummary(
        uuid5(NAMESPACE_DNS, slug),
        slug,
        slug.title(),
        None,
        "siesa",
        f"consumo.{slug}",
        None,
        [],
        None,
        None,
        dwh_sequence=sequence,
    )
    fields = [
        MasterField(f"{slug}_key", "Clave", "text", "key", False, False, None),
        MasterField("nit", "NIT", "text", "code", True, False, None),
        MasterField("razon_social", "Nombre", "text", "name", True, False, None),
        MasterField("estado", "Estado", "text", "active", True, False, None),
        *extra,
    ]
    return MasterDetail(summary, fields)


def _attribute(name: str, pii: bool = False) -> MasterField:
    return MasterField(name, name, "text", "attribute", True, pii, None)


class FakeRepository:
    def __init__(self) -> None:
        self.masters = {
            "clientes": _definition(
                "clientes", "identidad", _attribute("ciudad"), _attribute("telefono", pii=True)
            ),
            "productos": _definition("productos", "reporte_ventas", _attribute("marca")),
        }

    async def list_definitions(self):
        return list(self.masters.values())

    async def get_definition(self, slug):
        return self.masters.get(slug)


class FakeWarehouse:
    def __init__(self) -> None:
        self.rows = {
            "clientes": [
                {
                    "clientes_key": "1-900",
                    "nit": "900",
                    "razon_social": " Acme  SAS ",
                    "estado": "A",
                    "ciudad": "Cali",
                    "telefono": "300",
                }
            ],
            "productos": [],
        }
        self.states = {
            "identidad": Freshness("identidad", RUN_1, MORNING),
            "reporte_ventas": Freshness("reporte_ventas", RUN_1, NOON),
        }
        self.calls: list[tuple] = []
        self.down = False
        self.refreshed: list[str] = []

    def _check(self) -> None:
        if self.down:
            raise WarehouseUnavailable("relation does not exist")

    async def count(self, spec, query, is_active):
        self.calls.append(("count", spec.slug, query, is_active))
        self._check()
        return len(self.rows[spec.slug])

    async def page(self, spec, query, is_active, offset, limit):
        self.calls.append(("page", spec.slug, query, is_active, offset, limit))
        self._check()
        return self.rows[spec.slug][offset : offset + limit]

    async def freshness(self, sequences, refresh=False):
        if refresh:
            self.refreshed.extend(sequences)
        return {name: self.states[name] for name in sequences if name in self.states}


class FakeAccess:
    def __init__(self, visible=None, private=True, by_area=None) -> None:
        self.visible = visible
        self.private = private
        self.by_area = by_area or {}

    async def visible_masters(self, principal):
        return self.visible

    async def area_masters(self, slug):
        return self.by_area.get(slug, frozenset())

    async def can_see_private(self, principal):
        return self.private


def _service(access=None, warehouse=None, cache=None) -> MasterService:
    return MasterService(
        FakeRepository(), warehouse or FakeWarehouse(), cache or MasterReadCache(), access
    )


async def test_unknown_retired_or_malformed_slug_is_not_found() -> None:
    service = _service()

    with pytest.raises(NotFoundError, match="Maestro no encontrado"):
        await service.get_master("terceros", ANA)
    with pytest.raises(NotFoundError):
        await service.get_master("../etc", ANA)
    with pytest.raises(NotFoundError):
        await service.list_records("nope", None, None, 0, 50, ANA)


async def test_records_come_from_the_dwh_with_a_stable_id() -> None:
    warehouse = FakeWarehouse()
    service = _service(warehouse=warehouse)

    page = await service.list_records(" Clientes ", "  acme   sas ", True, 0, 50, ANA)
    (record,) = page.items

    assert ("page", "clientes", "acme sas", True, 0, 50) in warehouse.calls
    assert record.id == uuid5(uuid5(NAMESPACE_DNS, "clientes"), "1-900")
    assert (record.code, record.name, record.is_active) == ("900", "Acme SAS", True)
    assert record.attributes == {"ciudad": "Cali", "telefono": "300"}
    assert record.synced_at == MORNING and page.last_synced_at == MORNING
    assert page.total == 1 and not page.is_stale
    with pytest.raises(InvalidInputError, match="límite"):
        await service.list_records("clientes", None, None, 0, 201, ANA)
    with pytest.raises(InvalidInputError, match="desplazamiento"):
        await service.list_records("clientes", None, None, -1, 50, ANA)


async def test_the_cache_holds_until_the_sequence_runs_again() -> None:
    warehouse = FakeWarehouse()
    service = _service(warehouse=warehouse)

    await service.list_records("clientes", None, None, 0, 50, ANA)
    await service.list_records("clientes", None, None, 0, 50, ANA)
    assert len(warehouse.calls) == 2  # one count and one page

    warehouse.states["identidad"] = Freshness("identidad", RUN_2, NOON)
    page = await service.list_records("clientes", None, None, 0, 50, ANA)
    assert len(warehouse.calls) == 4
    assert page.last_synced_at == NOON and not page.is_stale


async def test_while_the_dwh_reloads_the_last_answer_is_served() -> None:
    warehouse = FakeWarehouse()
    service = _service(warehouse=warehouse)
    await service.list_records("clientes", None, None, 0, 50, ANA)
    calls = len(warehouse.calls)

    # A new run started and failed half way: the views are being re-created.
    warehouse.states["identidad"] = Freshness("identidad", RUN_2, NOON, reloading=True)
    page = await service.list_records("clientes", None, None, 0, 50, ANA)

    assert len(warehouse.calls) == calls
    assert page.is_stale and page.last_synced_at == MORNING and page.total == 1


async def test_when_the_dwh_does_not_answer() -> None:
    warehouse = FakeWarehouse()
    service = _service(warehouse=warehouse)
    first = await service.list_masters(ANA)
    await service.list_records("clientes", None, None, 0, 50, ANA)

    warehouse.down = True
    warehouse.states["identidad"] = Freshness("identidad", RUN_2, NOON)
    page = await service.list_records("clientes", None, None, 0, 50, ANA)
    assert page.is_stale and page.items[0].code == "900"

    # Nothing cached for this search: 503, and the list still answers without a count.
    with pytest.raises(DependencyUnavailableError, match="no respondió"):
        await service.list_records("clientes", "otro", None, 0, 50, ANA)
    cold = await _service(warehouse=warehouse).list_masters(ANA)
    assert [m.record_count for m in first] == [1, 0]
    assert [m.record_count for m in cold] == [None, None]


async def test_the_list_counts_each_master_and_says_when_the_dwh_published() -> None:
    service = _service()

    clientes, productos = await service.list_masters(ANA)

    assert (clientes.record_count, clientes.last_synced_at) == (1, MORNING)
    assert (productos.dwh_sequence, productos.last_synced_at) == ("reporte_ventas", NOON)
    assert not clientes.is_stale


async def test_warming_reads_only_the_masters_of_the_sequence() -> None:
    warehouse = FakeWarehouse()
    cache = MasterReadCache()
    service = _service(warehouse=warehouse, cache=cache)

    result = await service.warm("identidad")

    assert result == {"masters": [{"slug": "clientes", "total": 1, "error": None}], "errors": []}
    assert warehouse.refreshed == ["identidad"]
    assert {call[1] for call in warehouse.calls} == {"clientes"}
    # The default page a person opens first is already cached.
    calls = len(warehouse.calls)
    await service.list_records("clientes", None, None, 0, 50, ANA)
    assert len(warehouse.calls) == calls

    warehouse.down = True
    warehouse.states["reporte_ventas"] = Freshness("reporte_ventas", RUN_2, NOON)
    failed = await service.warm("reporte_ventas")
    assert failed["errors"] == [f"productos: {failed['masters'][0]['error']}"]


async def test_a_person_reads_only_the_masters_of_their_areas() -> None:
    service = _service(FakeAccess(visible=frozenset({"clientes"})))

    assert [m.slug for m in await service.list_masters(ANA)] == ["clientes"]
    assert (await service.get_master("clientes", ANA)).summary.slug == "clientes"
    with pytest.raises(ForbiddenError):
        await service.get_master("productos", ANA)
    with pytest.raises(ForbiddenError):
        await service.list_records("productos", None, None, 0, 50, ANA)

    assert await _service(FakeAccess(visible=frozenset())).list_masters(ANA) == []


async def test_the_area_filter_narrows_the_list() -> None:
    service = _service(FakeAccess(by_area={"cartera": frozenset({"productos"}), "bi": None}))

    assert [m.slug for m in await service.list_masters(ANA, "cartera")] == ["productos"]
    assert [m.slug for m in await service.list_masters(ANA, "bi")] == ["clientes", "productos"]
    assert await service.list_masters(ANA, "tesoreria") == []


async def test_personal_data_is_left_out_for_readers() -> None:
    service = _service(FakeAccess(private=False))

    detail = await service.get_master("clientes", ANA)
    page = await service.list_records("clientes", None, None, 0, 50, ANA)

    assert "telefono" not in [f.name for f in detail.fields]
    assert detail.summary.record_count == 1
    assert page.items[0].attributes == {"ciudad": "Cali"}

    full = await _service(FakeAccess(private=True)).list_records("clientes", None, None, 0, 50, ANA)
    assert full.items[0].attributes == {"ciudad": "Cali", "telefono": "300"}
