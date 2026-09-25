from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.errors import ConflictError, InvalidInputError
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import RequestType
from app.modules.sites.domain.entities import Site

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _type(allows_reason: bool = True) -> RequestType:
    return RequestType(
        id=uuid4(),
        code="peticion" if allows_reason else "sugerencia",
        name="Petición" if allows_reason else "Sugerencia",
        allows_reason=allows_reason,
        allows_attachments=allows_reason,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


class FakePqrsfRepository:
    def __init__(self, request_type: RequestType) -> None:
        self.request_type = request_type
        self.requests = {}

    async def list_request_types(self, active_only=True):
        return [self.request_type]

    async def get_request_type(self, request_type_id):
        return self.request_type if request_type_id == self.request_type.id else None

    async def get_by_idempotency_key(self, key):
        return next(
            (value for value in self.requests.values() if value.idempotency_key == key), None
        )

    async def create_request(self, request):
        stored = replace(request, created_at=NOW, updated_at=NOW)
        self.requests[stored.id] = stored
        return stored

    async def get_request(self, request_id):
        return self.requests.get(request_id)

    async def list_requests(self, filters, page, size):
        raise NotImplementedError


class FakeSiteRepository:
    def __init__(self, site: Site) -> None:
        self.site = site

    async def list_sites(self, include_inactive=False):
        return [self.site] if include_inactive or self.site.is_active else []

    async def get_by_id(self, site_id):
        return self.site if site_id == self.site.id else None

    async def get_by_code(self, code):
        return self.site if code == self.site.code else None

    async def create_site(self, code, name, is_active):
        raise NotImplementedError

    async def save_site(self, site):
        return site

    async def delete_site(self, site_id):
        raise NotImplementedError


def _service(allows_reason: bool = True) -> tuple[PqrsfService, RequestType, Site]:
    request_type = _type(allows_reason)
    site = Site(uuid4(), "bogota", "Bogotá", True, NOW, NOW)
    from app.modules.sites.application.service import SiteService

    return (
        PqrsfService(FakePqrsfRepository(request_type), SiteService(FakeSiteRepository(site))),
        request_type,
        site,
    )


def _payload(request_type: RequestType, site: Site, key: str) -> dict[str, object]:
    return {
        "request_type_id": str(request_type.id),
        "site_id": str(site.id),
        "full_name": "Ana Pérez",
        "business_name": "Comercial SAS",
        "nit": "900123456-1",
        "address": "Carrera 1 # 2-3",
        "department": "Antioquia",
        "city": "Medellín",
        "email": "ana@example.com",
        "reason": "Necesito información",
        "description": "Descripción de prueba",
        "idempotency_key": key,
    }


def _create(service: PqrsfService, request_type: RequestType, site: Site, values):
    return service.create_request(
        request_type_id=request_type.id,
        site_id=site.id,
        **{
            key: value for key, value in values.items() if key not in {"request_type_id", "site_id"}
        },
        idempotency_payload=values,
    )


@pytest.mark.asyncio
async def test_create_request_returns_same_record_for_an_idempotent_retry() -> None:
    service, request_type, site = _service()
    key = str(uuid4())
    values = _payload(request_type, site, key)

    first = await _create(service, request_type, site, values)
    second = await _create(service, request_type, site, values)

    assert first.replayed is False
    assert second.replayed is True
    assert first.request.id == second.request.id
    assert first.request.reference.startswith("PQRSF-")


@pytest.mark.asyncio
async def test_same_idempotency_key_with_other_values_is_rejected() -> None:
    service, request_type, site = _service()
    key = str(uuid4())
    values = _payload(request_type, site, key)
    await _create(service, request_type, site, values)

    changed = {**values, "description": "Otro contenido"}
    with pytest.raises(ConflictError, match="otros datos"):
        await _create(service, request_type, site, changed)


@pytest.mark.asyncio
async def test_reason_is_required_only_for_types_that_allow_it() -> None:
    service, request_type, site = _service()
    values = _payload(request_type, site, str(uuid4()))
    with pytest.raises(InvalidInputError, match="motivo es obligatorio"):
        await _create(service, request_type, site, {**values, "reason": None})

    suggestion_service, suggestion_type, suggestion_site = _service(False)
    suggestion_values = _payload(suggestion_type, suggestion_site, str(uuid4()))
    suggestion_values["reason"] = None
    created = await _create(suggestion_service, suggestion_type, suggestion_site, suggestion_values)
    assert created.request.reason is None
