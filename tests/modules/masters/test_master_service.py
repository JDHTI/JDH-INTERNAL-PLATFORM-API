import pytest
from app.core.errors import InvalidInputError, NotFoundError
from app.modules.masters.application.service import MasterService
from app.modules.masters.domain.entities import MasterDetail, MasterRecordPage, MasterSummary


class FakeMasterRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        summary = MasterSummary(
            None, "clientes", "Clientes", None, "siesa", None, None, [], 0, None
        )  # type: ignore[arg-type]
        self.masters = {"clientes": MasterDetail(summary)}

    async def list_masters(self):
        return [m.summary for m in self.masters.values()]

    async def get_master(self, slug):
        return self.masters.get(slug)

    async def list_records(self, slug, query, is_active, offset, limit):
        self.calls.append((slug, query, is_active, offset, limit))
        if slug not in self.masters:
            return None
        return MasterRecordPage([], 0, offset, limit)


@pytest.mark.asyncio
async def test_unknown_or_malformed_slug_is_not_found() -> None:
    service = MasterService(FakeMasterRepository())

    with pytest.raises(NotFoundError, match="Maestro no encontrado"):
        await service.get_master("nope")
    with pytest.raises(NotFoundError):
        await service.get_master("../etc")
    with pytest.raises(NotFoundError):
        await service.list_records("nope", None, None, 0, 50)


@pytest.mark.asyncio
async def test_records_query_is_normalized_and_limit_is_bounded() -> None:
    repository = FakeMasterRepository()
    service = MasterService(repository)

    await service.list_records(" Clientes ", "  acme   sas ", True, 0, 50)
    await service.list_records("clientes", "   ", None, 10, 200)

    assert repository.calls == [
        ("clientes", "acme sas", True, 0, 50),
        ("clientes", None, None, 10, 200),
    ]
    with pytest.raises(InvalidInputError, match="límite"):
        await service.list_records("clientes", None, None, 0, 201)
    with pytest.raises(InvalidInputError, match="desplazamiento"):
        await service.list_records("clientes", None, None, -1, 50)
