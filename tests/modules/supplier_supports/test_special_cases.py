"""Special cases: requests for manual configuration of particular clients."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.core.config import Environment, Settings
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.special_cases import SpecialCaseService
from app.modules.supplier_supports.domain.special_cases import (
    SpecialCase,
    SpecialCaseData,
    SpecialCaseView,
)
from fastapi.testclient import TestClient

from tests.modules.supplier_supports.conftest import FAMILIA_ID, FakeLookup, FakeManagers, person


class FakeCaseRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, SpecialCase] = {}
        self.last_search: tuple | None = None

    async def create(self, case_id, data, actor, now):
        case = SpecialCase(
            case_id, f"CE-{len(self.items) + 1:04d}", data, actor, actor, 1, now, now
        )
        self.items[case_id] = case
        return case

    async def get(self, case_id):
        return self.items.get(case_id)

    async def get_by_source_key(self, source_key):
        return next((c for c in self.items.values() if c.data.source_key == source_key), None)

    async def update(self, case_id, data, expected_version, actor, now):
        current = self.items[case_id]
        if current.version != expected_version:
            raise ConflictError("versión")
        saved = replace(current, data=data, version=current.version + 1, updated_by=actor)
        self.items[case_id] = saved
        return saved

    async def view(self, case_id):
        case = self.items.get(case_id)
        return None if case is None else SpecialCaseView(case, "233", "FAMILIA SANCELA", 3)

    async def search(self, query, status, customer_nit, ids, page, size):
        self.last_search = (query, status, customer_nit, page, size)
        views = [SpecialCaseView(c, "233", "FAMILIA SANCELA", 0) for c in self.items.values()]
        return views, len(views)


@pytest.fixture
def cases():
    return FakeCaseRepository()


@pytest.fixture
def case_service(cases):
    return SpecialCaseService(cases, FakeLookup(), FakeManagers(["comercial@jdh.com.co"]))


@pytest.fixture
def manager():
    return person("comercial@jdh.com.co")


def summar() -> SpecialCaseData:
    return SpecialCaseData(
        title="Rubbermaid: costo apoyado exclusivo para Summar",
        request="El nuevo costo apoyado aplica solo para Summar",
        rule_to_program="Si cliente = Summar: costo = COSTO APOYADO",
        customer_nit="800116299",
        priority="alta",
    )


async def test_create_binds_supplier_and_client_and_starts_pending(case_service, manager) -> None:
    view = await case_service.create(manager, summar(), supplier_code="233")

    data = view.case.data
    assert (view.case.code, data.status, data.priority) == ("CE-0001", "pendiente", "alta")
    assert data.commercial_supplier_id == FAMILIA_ID
    assert data.customer_name == "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"  # from the master
    assert view.case.created_by == manager.email and view.support_count == 3


@pytest.mark.parametrize(
    ("changes", "supplier", "message"),
    [
        ({"customer_nit": "123"}, "233", "NIT de cliente desconocido"),
        ({}, "nope", "Proveedor comercial desconocido"),
        ({"status": "hecho"}, "233", "Estado inválido"),
        ({"priority": "urgente"}, "233", "Prioridad inválida"),
        ({"title": ""}, "233", "título y petición"),
    ],
)
async def test_create_rejects_invalid_cases(
    case_service, manager, changes, supplier, message
) -> None:
    with pytest.raises(InvalidInputError, match=message):
        await case_service.create(manager, replace(summar(), **changes), supplier_code=supplier)


async def test_only_managers_write(case_service) -> None:
    with pytest.raises(ForbiddenError):
        await case_service.create(person("otro@jdh.com.co"), summar())


async def test_follow_up_changes_status_and_checks_the_version(case_service, manager) -> None:
    created = await case_service.create(manager, summar(), supplier_code="233")

    moved = await case_service.update(
        manager,
        created.case.id,
        expected_version=1,
        changes={"status": "en_analisis", "assignee": "daniel@jdh.com.co"},
    )

    assert (moved.case.data.status, moved.case.data.assignee, moved.case.version) == (
        "en_analisis",
        "daniel@jdh.com.co",
        2,
    )
    assert moved.case.data.request == summar().request  # untouched
    with pytest.raises(ConflictError):
        await case_service.update(
            manager, created.case.id, expected_version=1, changes={"status": "programado"}
        )
    with pytest.raises(InvalidInputError, match="no editables"):
        await case_service.update(
            manager, created.case.id, expected_version=2, changes={"source_key": "x"}
        )
    with pytest.raises(NotFoundError):
        await case_service.update(manager, uuid4(), expected_version=1, changes={"title": "x"})


async def test_search_validates_paging_and_status(case_service, cases) -> None:
    await case_service.search(q=" summar ", status=["pendiente"], customer_nit="800.116.299")
    assert cases.last_search == ("summar", ["pendiente"], "800116299", 1, 50)

    for bad in ({"page": 0}, {"size": 500}, {"status": ["hecho"]}):
        with pytest.raises(InvalidInputError):
            await case_service.search(**bad)


async def test_the_importer_opens_each_case_once_and_never_blocks_on_unknown_data(
    case_service, cases
) -> None:
    sodexo = SpecialCaseData(
        title="Sodexo CEDI",
        request="Definir la sucursal",
        customer_name="SODEXO CEDI",
        source_key="Rubbermaid/Lista_SodexoCedi_RCP",
    )

    assert await case_service.ensure(sodexo, supplier_code="233", dry_run=True) is None
    first = await case_service.ensure(sodexo, supplier_code="233")
    again = await case_service.ensure(sodexo, supplier_code="233")

    assert first == again and len(cases.items) == 1
    assert (
        cases.items[first].created_by == "importacion"
        and cases.items[first].data.customer_nit is None
    )


# ----------------------------------------------------------------------------- HTTP

BASE = "/api/v1/supplier-special-cases"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(case_service):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person("comercial@jdh.com.co")
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.special_case_service.override(case_service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_http_lifecycle_of_a_case(client) -> None:
    created = client.post(
        BASE,
        json={
            "title": "Sodexo CEDI",
            "request": "Definir qué sucursales son el CEDI",
            "supplier_code": "233",
            "customer_nit": "800116299",
            "priority": "alta",
        },
    )
    assert created.status_code == 201
    case = created.json()
    assert case["code"] == "CE-0001" and case["status"] == "pendiente"
    assert case["supplier_name"] == "FAMILIA SANCELA" and case["support_count"] == 3

    assert client.get(f"{BASE}/{case['id']}").json()["id"] == case["id"]
    listed = client.get(BASE, params={"status": "pendiente", "q": "sodexo"}).json()
    assert listed["total"] == 1 and listed["items"][0]["code"] == "CE-0001"

    patched = client.patch(f"{BASE}/{case['id']}", json={"version": 1, "status": "programado"})
    assert patched.status_code == 200 and patched.json()["status"] == "programado"
    assert (
        client.patch(
            f"{BASE}/{case['id']}", json={"version": 1, "status": "implementado"}
        ).status_code
        == 409
    )
    assert client.patch(f"{BASE}/{case['id']}", json={"status": "implementado"}).status_code == 422
    assert client.get(f"{BASE}/{uuid4()}").status_code == 404


def test_http_rejects_bad_input(client) -> None:
    assert client.post(BASE, json={"title": "x", "request": "y"}).status_code == 422
    assert (
        client.post(BASE, json={"title": "Caso", "request": "Pedir", "status": "hecho"}).status_code
        == 422
    )
    assert client.get(BASE, params={"status": "hecho"}).status_code == 422
    assert client.get(BASE, params={"size": 500}).status_code == 422


def test_the_routes_are_documented(client) -> None:
    paths = set(client.app.openapi()["paths"])

    assert f"{BASE}" in paths and f"{BASE}/{{case_id}}" in paths
