from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.masters.application.service import MasterService
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterField,
    MasterRecord,
    MasterRecordPage,
    MasterSummary,
)
from tests.modules.fakes import principal

ANA = principal("ana@jdh.com.co")


def _summary(slug: str) -> MasterSummary:
    return MasterSummary(uuid4(), slug, slug.title(), None, "siesa", None, None, [], 0, None)


def _field(name: str, pii: bool = False) -> MasterField:
    return MasterField(name, name, "text", "attribute", True, pii, None)


class FakeMasterRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.masters = {
            "clientes": MasterDetail(
                _summary("clientes"), [_field("ciudad"), _field("telefono", pii=True)]
            ),
            "productos": MasterDetail(_summary("productos"), [_field("marca")]),
        }
        self.records = [
            MasterRecord(
                uuid4(), 1, "900", "Acme", True, "k1", {"ciudad": "Cali", "telefono": "300"}, None
            )
        ]

    async def list_masters(self):
        return [m.summary for m in self.masters.values()]

    async def get_master(self, slug):
        return self.masters.get(slug)

    async def list_records(self, slug, query, is_active, offset, limit):
        self.calls.append((slug, query, is_active, offset, limit))
        if slug not in self.masters:
            return None
        items = self.records[offset : offset + limit] if slug == "clientes" else []
        return MasterRecordPage(items, len(items), offset, limit)


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


async def test_unknown_or_malformed_slug_is_not_found() -> None:
    service = MasterService(FakeMasterRepository())

    with pytest.raises(NotFoundError, match="Maestro no encontrado"):
        await service.get_master("nope", ANA)
    with pytest.raises(NotFoundError):
        await service.get_master("../etc", ANA)
    with pytest.raises(NotFoundError):
        await service.list_records("nope", None, None, 0, 50, ANA)


async def test_records_query_is_normalized_and_limit_is_bounded() -> None:
    repository = FakeMasterRepository()
    service = MasterService(repository)

    await service.list_records(" Clientes ", "  acme   sas ", True, 0, 50, ANA)
    await service.list_records("clientes", "   ", None, 10, 200, ANA)

    assert repository.calls == [
        ("clientes", "acme sas", True, 0, 50),
        ("clientes", None, None, 10, 200),
    ]
    with pytest.raises(InvalidInputError, match="límite"):
        await service.list_records("clientes", None, None, 0, 201, ANA)
    with pytest.raises(InvalidInputError, match="desplazamiento"):
        await service.list_records("clientes", None, None, -1, 50, ANA)


async def test_a_person_reads_only_the_masters_of_their_areas() -> None:
    service = MasterService(FakeMasterRepository(), FakeAccess(visible=frozenset({"clientes"})))

    assert [m.slug for m in await service.list_masters(ANA)] == ["clientes"]
    assert (await service.get_master("clientes", ANA)).summary.slug == "clientes"
    with pytest.raises(ForbiddenError):
        await service.get_master("productos", ANA)
    with pytest.raises(ForbiddenError):
        await service.list_records("productos", None, None, 0, 50, ANA)

    nobody = MasterService(FakeMasterRepository(), FakeAccess(visible=frozenset()))
    assert await nobody.list_masters(ANA) == []


async def test_the_area_filter_narrows_the_list() -> None:
    access = FakeAccess(by_area={"cartera": frozenset({"productos"}), "bi": None})
    service = MasterService(FakeMasterRepository(), access)

    assert [m.slug for m in await service.list_masters(ANA, "cartera")] == ["productos"]
    assert [m.slug for m in await service.list_masters(ANA, "bi")] == ["clientes", "productos"]
    assert await service.list_masters(ANA, "tesoreria") == []


async def test_personal_data_is_left_out_for_readers() -> None:
    service = MasterService(FakeMasterRepository(), FakeAccess(private=False))

    detail = await service.get_master("clientes", ANA)
    page = await service.list_records("clientes", None, None, 0, 50, ANA)
    _, records = await service.export("clientes", None, ANA)

    assert [f.name for f in detail.fields] == ["ciudad"]
    assert page.items[0].attributes == {"ciudad": "Cali"}
    assert records[0].attributes == {"ciudad": "Cali"}

    manager = MasterService(FakeMasterRepository(), FakeAccess(private=True))
    full = await manager.list_records("clientes", None, None, 0, 50, ANA)
    assert full.items[0].attributes == {"ciudad": "Cali", "telefono": "300"}
