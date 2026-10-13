from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import DependencyUnavailableError
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import (
    ImageUpload,
    RequestType,
    SanitizedImage,
)
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

NOW = datetime(2026, 9, 25, tzinfo=UTC)


class FakeRequestRepository:
    def __init__(self, request_type: RequestType, site: Site) -> None:
        self.request_type = request_type
        self.site = site
        self.requests = {}
        self.attachments = []

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
        request = self.requests.get(request_id)
        if request is not None:
            request.attachments = [
                item for item in self.attachments if item.request_id == request_id
            ]
        return request

    async def list_requests(self, filters, page, size):
        raise NotImplementedError

    async def add_attachments(self, attachments):
        self.attachments.extend(attachments)

    async def list_attachments(self, request_id):
        return [item for item in self.attachments if item.request_id == request_id]

    async def get_attachment(self, request_id, attachment_id):
        return next(
            (
                item
                for item in self.attachments
                if item.request_id == request_id and item.id == attachment_id
            ),
            None,
        )

    async def delete_request(self, request_id):
        self.requests.pop(request_id, None)
        self.attachments = [item for item in self.attachments if item.request_id != request_id]


class FakeSiteRepository:
    def __init__(self, site: Site) -> None:
        self.site = site

    async def list_sites(self, include_inactive=False):
        return [self.site]

    async def get_by_id(self, site_id):
        return self.site if site_id == self.site.id else None

    async def get_by_code(self, code):
        return self.site if code == self.site.code else None


class FakeImageProcessor:
    async def sanitize(self, filename, content_type, content):
        return SanitizedImage(
            original_filename=filename or "evidence.png",
            extension="png",
            content_type="image/png",
            content=b"sanitized:" + content,
        )


class FakeMalwareScanner:
    def __init__(self) -> None:
        self.scanned = []

    async def scan(self, content):
        self.scanned.append(content)


class FakeStorage:
    def __init__(self, fail_on_put: int | None = None) -> None:
        self.fail_on_put = fail_on_put
        self.put_count = 0
        self.objects = {}
        self.deleted = []

    async def put_object(self, key, body, content_type=None):
        self.put_count += 1
        if self.fail_on_put == self.put_count:
            raise OSError("storage unavailable")
        self.objects[key] = body

    async def get_object(self, key):
        return self.objects[key]

    async def delete_object(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


def _fixtures():
    request_type = RequestType(
        id=uuid4(),
        code="peticion",
        name="Petición",
        allows_document_request=True,
        allows_attachments=True,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    site = Site(uuid4(), "bogota", "Bogotá", True, NOW, NOW, "BG")
    repository = FakeRequestRepository(request_type, site)
    service = PqrsfService(
        repository,
        SiteService(FakeSiteRepository(site)),
        storage=FakeStorage(),
        image_processor=FakeImageProcessor(),
        malware_scanner=FakeMalwareScanner(),
    )
    return service, repository, request_type, site


def _values(request_type: RequestType, site: Site, key: str):
    return {
        "request_type_id": request_type.id,
        "site_id": site.id,
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


@pytest.mark.asyncio
async def test_create_request_persists_sanitized_attachment_metadata() -> None:
    service, repository, request_type, site = _fixtures()
    values = _values(request_type, site, str(uuid4()))

    result = await service.create_request(
        **values,
        idempotency_payload={key: str(value) for key, value in values.items()},
        images=[ImageUpload("evidence.png", "image/png", b"raw")],
    )

    assert len(repository.attachments) == 1
    attachment = repository.attachments[0]
    assert attachment.request_id == result.request.id
    assert attachment.storage_key.startswith(f"pqrsf/{result.request.id}/")
    assert attachment.checksum_sha256
    assert result.request.attachments == [attachment]


@pytest.mark.asyncio
async def test_attachment_storage_failure_rolls_back_request_and_objects() -> None:
    service, repository, request_type, site = _fixtures()
    storage = FakeStorage(fail_on_put=2)
    service.storage = storage
    values = _values(request_type, site, str(uuid4()))

    with pytest.raises(DependencyUnavailableError, match="guardar las im"):
        await service.create_request(
            **values,
            idempotency_payload={key: str(value) for key, value in values.items()},
            images=[
                ImageUpload("one.png", "image/png", b"one"),
                ImageUpload("two.png", "image/png", b"two"),
            ],
        )

    assert repository.requests == {}
    assert repository.attachments == []
    assert len(storage.deleted) == 1
