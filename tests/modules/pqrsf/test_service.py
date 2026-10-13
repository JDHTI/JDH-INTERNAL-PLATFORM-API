from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import (
    DocumentCategory,
    DocumentOption,
    ImageUpload,
    RequestType,
)
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

NOW = datetime(2026, 9, 25, tzinfo=UTC)

CATEGORY = DocumentCategory(
    id=uuid4(),
    code="producto",
    name="Producto y fabricante",
    dependency_id=uuid4(),
    documents=[
        DocumentOption(uuid4(), "Ficha Técnica"),
        DocumentOption(uuid4(), "Hoja de Seguridad"),
    ],
)
OTHER_CATEGORY = DocumentCategory(
    id=uuid4(),
    code="personal",
    name="Personal y capacitación",
    dependency_id=None,
    documents=[DocumentOption(uuid4(), "Copia Registro de Capacitaciones")],
)


def _type(petition: bool = True) -> RequestType:
    return RequestType(
        id=uuid4(),
        code="peticion" if petition else "sugerencia",
        name="Petición" if petition else "Sugerencia",
        allows_document_request=petition,
        allows_attachments=petition,
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

    async def list_document_categories(self):
        return [CATEGORY, OTHER_CATEGORY]

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


def _service(petition: bool = True) -> tuple[PqrsfService, RequestType, Site]:
    request_type = _type(petition)
    site = Site(uuid4(), "bogota", "Bogotá", True, NOW, NOW, "BG")

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
        "subject": "Necesito información",
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


def _document_values(request_type: RequestType, site: Site, **overrides):
    return {
        **_payload(request_type, site, str(uuid4())),
        "document_request": True,
        "document_category_id": CATEGORY.id,
        "document_type_ids": [CATEGORY.documents[0].id],
        **overrides,
    }


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
@pytest.mark.parametrize("petition", [True, False])
async def test_subject_is_required_for_every_type(petition: bool) -> None:
    service, request_type, site = _service(petition)
    values = _payload(request_type, site, str(uuid4()))

    with pytest.raises(InvalidInputError, match="asunto"):
        await _create(service, request_type, site, {**values, "subject": "   "})

    created = await _create(service, request_type, site, values)
    assert created.request.subject == "Necesito información"


@pytest.mark.asyncio
async def test_catalog_offers_the_document_categories() -> None:
    service, _, _ = _service()

    _, _, categories = await service.catalog()

    assert [category.code for category in categories] == ["producto", "personal"]


@pytest.mark.asyncio
async def test_a_document_request_stores_the_documents_asked_for() -> None:
    service, request_type, site = _service()
    other = "Certificado de la planta de Cali"
    values = _document_values(request_type, site, document_other=other)

    created = await _create(service, request_type, site, values)

    request = created.request
    assert request.document_request is True
    assert request.document_category_id == CATEGORY.id
    assert [(item.document_type_id, item.description) for item in request.documents] == [
        (CATEGORY.documents[0].id, None),
        (None, other),
    ]


@pytest.mark.asyncio
async def test_a_document_request_accepts_only_free_text() -> None:
    service, request_type, site = _service()
    values = _document_values(
        request_type, site, document_type_ids=[], document_other="Carta de referencia"
    )

    created = await _create(service, request_type, site, values)

    assert [item.description for item in created.request.documents] == ["Carta de referencia"]


@pytest.mark.asyncio
async def test_a_document_request_takes_no_evidence() -> None:
    service, request_type, site = _service()
    values = _document_values(request_type, site)
    image = ImageUpload(filename="foto.png", content_type="image/png", content=b"x")

    with pytest.raises(InvalidInputError, match="no admite evidencias"):
        await service.create_request(
            request_type_id=request_type.id,
            site_id=site.id,
            **{k: v for k, v in values.items() if k not in {"request_type_id", "site_id"}},
            idempotency_payload=values,
            images=[image],
        )


@pytest.mark.asyncio
async def test_only_types_that_allow_it_take_a_document_request() -> None:
    service, request_type, site = _service(petition=False)
    values = _document_values(request_type, site)

    with pytest.raises(InvalidInputError, match="no admite solicitud de documentos"):
        await _create(service, request_type, site, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"document_category_id": None}, "categoría"),
        ({"document_category_id": uuid4()}, "no está disponible"),
        ({"document_type_ids": [], "document_other": None}, "qué documento"),
        ({"document_type_ids": [OTHER_CATEGORY.documents[0].id]}, "no pertenece"),
        (
            {"document_type_ids": [CATEGORY.documents[0].id, CATEGORY.documents[0].id]},
            "no puede repetirse",
        ),
    ],
)
async def test_a_document_request_is_validated_against_the_catalog(overrides, message) -> None:
    service, request_type, site = _service()
    values = _document_values(request_type, site, **overrides)

    with pytest.raises(InvalidInputError, match=message):
        await _create(service, request_type, site, values)


@pytest.mark.asyncio
async def test_documents_are_rejected_when_the_request_is_not_for_documents() -> None:
    service, request_type, site = _service()
    values = {
        **_payload(request_type, site, str(uuid4())),
        "document_category_id": CATEGORY.id,
    }

    with pytest.raises(InvalidInputError, match="solo se indican"):
        await _create(service, request_type, site, values)
