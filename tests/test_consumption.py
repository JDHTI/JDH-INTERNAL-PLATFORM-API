from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import Environment, Settings
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.main import create_app
from app.modules.consumption.application.cursor import ClientCursorCodec, InvalidCursor
from app.modules.consumption.application.service import ConsumptionService
from app.modules.consumption.domain.entities import Client, ClientPage
from app.modules.consumption.infrastructure.repository import WarehouseClientRepository
from app.modules.identity.application.service import AuthenticatedUser
from fastapi.testclient import TestClient


def _client(key: str) -> Client:
    return Client(
        client_key=key,
        nit="900123456",
        razon_social="Cliente de prueba",
        sucursal="Principal",
        nombre_establecimiento="Establecimiento",
        zona_logistica="Centro",
        departamento="Cundinamarca",
        ciudad="Bogotá",
        nombre_vendedor="Vendedor",
        telefono="6010000000",
        celular="3000000000",
        direccion="Carrera 1",
        correo="cliente@example.test",
        unidad_negocio="Retail",
        canal="Tradicional",
    )


class FakeClientSource:
    def __init__(self, pages: dict[str | None, tuple[list[Client], bool]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, int]] = []

    async def fetch_clients(self, after_key: str | None, limit: int) -> tuple[list[Client], bool]:
        self.calls.append((after_key, limit))
        return self.pages[after_key]


@pytest.mark.asyncio
async def test_consumption_service_encodes_and_decodes_cursor() -> None:
    source = FakeClientSource({None: ([_client("001")], True), "001": ([_client("002")], False)})
    service = ConsumptionService(source, ClientCursorCodec("test-secret", "postgres"))

    first_page = await service.list_clients(limit=1)
    second_page = await service.list_clients(limit=1, cursor=first_page.next_cursor)

    assert first_page.next_cursor
    assert first_page.items[0].client_key == "001"
    assert second_page.items[0].client_key == "002"
    assert source.calls == [(None, 1), ("001", 1)]


@pytest.mark.asyncio
async def test_consumption_service_rejects_cursor_from_another_provider() -> None:
    source = FakeClientSource({None: ([], False)})
    service = ConsumptionService(source, ClientCursorCodec("test-secret", "postgres"))
    foreign_cursor = ClientCursorCodec("test-secret", "other-provider").encode("001")

    with pytest.raises(InvalidCursor):
        await service.list_clients(limit=10, cursor=foreign_cursor)


@pytest.mark.asyncio
async def test_disabled_provider_fails_without_opening_a_connection() -> None:
    provider = DataProviderConnection(
        provider="postgres",
        enabled=False,
        host="unused",
        port=5432,
        database="unused",
        user="unused",
        password="unused",
    )

    with pytest.raises(DataProviderUnavailable):
        await provider.connect()


class FakeProviderConnection:
    def __init__(self, rows: list[dict[str, str | None]]) -> None:
        self.rows = rows
        self.query: str | None = None
        self.args: tuple[object, ...] = ()

    async def fetch(self, query: str, *args: object) -> list[dict[str, str | None]]:
        self.query = query
        self.args = args
        return self.rows


@pytest.mark.asyncio
async def test_warehouse_repository_reads_only_requested_columns_and_one_lookahead() -> None:
    row = {"cliente_key": "001"}
    row.update(
        {
            column: None
            for column in (
                "nit",
                "razon_social",
                "sucursal",
                "nombre_establecimiento",
                "zona_logistica",
                "departamento",
                "ciudad",
                "nombre_vendedor",
                "telefono",
                "celular",
                "direccion",
                "correo",
                "unidad_negocio",
                "canal",
            )
        }
    )
    connection = FakeProviderConnection([row])
    repository = WarehouseClientRepository(connection, "consumo", "clientes")  # type: ignore[arg-type]

    clients, has_more = await repository.fetch_clients(None, 10)

    assert not has_more
    assert clients[0].client_key == "001"
    assert '"consumo"."clientes"' in connection.query
    assert connection.args == (11,)
    assert "COUNT" not in connection.query.upper()


class FakeIdentityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def current_user(
        self, access_token: str | None, refresh_token: str | None
    ) -> AuthenticatedUser:
        user = SimpleNamespace(
            id=uuid4(),
            tenant_id="tenant",
            entra_object_id="object",
            name="Development User",
            email="dev@example.test",
            job_title="Engineer",
            role=SimpleNamespace(code="user"),
            area_id=None,
            area=None,
            photo_key=None,
            photo_content_type=None,
        )
        return AuthenticatedUser(user)


class FakeConsumptionService:
    async def list_clients(self, limit: int, cursor: str | None = None) -> ClientPage:
        return ClientPage(items=(_client("001"),), next_cursor=None, has_more=False)


def test_clients_endpoint_requires_sso_and_returns_page() -> None:
    settings = Settings(
        ENVIRONMENT=Environment.DEV,
        ENTRA_TENANT_ID="tenant",
        ENTRA_CLIENT_ID="client",
        ENTRA_API_AUDIENCE="api://client",
        AUTH_COOKIE_SECURE=False,
    )
    app = create_app(settings)
    identity_service = FakeIdentityService(settings)

    with app.state.container.v1_container.identity_service.override(identity_service):
        with app.state.container.v1_container.consumption_service.override(
            FakeConsumptionService()
        ):
            with TestClient(app) as client:
                response = client.get("/api/v1/clientes?limit=1")

    assert response.status_code == 200
    assert response.json()["items"][0]["razon_social"] == "Cliente de prueba"
    assert "cliente_key" not in response.json()["items"][0]
    assert response.json()["has_more"] is False
