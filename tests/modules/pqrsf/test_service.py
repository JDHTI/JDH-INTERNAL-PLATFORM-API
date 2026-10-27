from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import (
    Cause,
    DocumentCategory,
    DocumentOption,
    ImageUpload,
    ProductInput,
    ProductMaster,
    ProductOption,
    RequestType,
    SanitizedImage,
    Subject,
)
from app.modules.pqrsf.domain.tracking import Dependency
from app.modules.pqrsf.presentation.router import _product_lines
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
    def __init__(self, request_type: RequestType, subjects: list[Subject] | None = None) -> None:
        self.request_type = request_type
        self.subjects = subjects or []
        self.requests = {}

    async def list_request_types(self, active_only=True):
        return [self.request_type]

    async def list_subjects(self, active_only=True):
        return self.subjects

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


# --- Classification: subject, cause and product lines ---

QUALITY = uuid4()
DISTRIBUTION = uuid4()
PRODUCT_SUBJECT_ID = uuid4()


def _cause(name: str, **values) -> Cause:
    defaults = {
        "dependency_id": QUALITY,
        "route": "supplier",
        "requires_product": False,
        "allows_product": False,
        "is_other": False,
    }
    return Cause(
        id=uuid4(),
        subject_id=PRODUCT_SUBJECT_ID,
        code=name.lower().replace(" ", "_"),
        name=name,
        description=None,
        **{**defaults, **values},
    )


EXPIRED = _cause(
    "Producto vencido",
    dependency_id=DISTRIBUTION,
    route="internal",
    requires_product=True,
    allows_product=True,
)
OTHER_CAUSE = _cause("Otro", route="none", allows_product=True, is_other=True)
NO_PRODUCT = _cause("Actitud de servicio", route="internal")
PRODUCT_SUBJECT = Subject(
    id=PRODUCT_SUBJECT_ID,
    code="calidad_producto",
    name="Calidad del producto",
    description=None,
    request_type_codes=("queja", "reclamo"),
    dependency_id=QUALITY,
    is_other=False,
    causes=(EXPIRED, OTHER_CAUSE, NO_PRODUCT),
)
OTHER_SUBJECT = Subject(
    id=uuid4(),
    code="otro_novedad",
    name="Otro",
    description=None,
    request_type_codes=("queja", "reclamo"),
    dependency_id=QUALITY,
    is_other=True,
)


class FakeProducts:
    async def search(self, query, limit):
        return [ProductOption("9834", "Aceite suavizante")][:limit]

    async def resolve(self, skus):
        known = {"9834": ProductMaster("9834", "Aceite suavizante", "P01", "Unilever Andina")}
        return {sku: known[sku] for sku in skus if sku in known}


def _claims_service() -> tuple[PqrsfService, RequestType, Site]:
    request_type = replace(_type(), code="queja", name="Queja", allows_document_request=False)
    site = Site(uuid4(), "bogota", "Bogotá", True, NOW, NOW, "BG")

    repository = FakePqrsfRepository(request_type, [PRODUCT_SUBJECT, OTHER_SUBJECT])
    service = PqrsfService(
        repository, SiteService(FakeSiteRepository(site)), products=FakeProducts()
    )
    return service, request_type, site


def _line(**values) -> ProductInput:
    defaults = {
        "invoice_number": "FE-1020",
        "sku": "9834",
        "lot": "L-77",
        "expiry_date": date(2026, 12, 1),
        "affected_quantity": 3,
    }
    return ProductInput(**{**defaults, **values})


def _claim(request_type: RequestType, site: Site, **overrides) -> dict[str, object]:
    values = {
        **_payload(request_type, site, str(uuid4())),
        "subject": None,
        "description": None,
        "subject_id": PRODUCT_SUBJECT.id,
        "cause_id": EXPIRED.id,
        "products": [_line()],
        "pickup_order_requested": "no",
    }
    return {**values, **overrides}


@pytest.mark.asyncio
async def test_a_claim_takes_subject_cause_and_products_with_master_data() -> None:
    service, request_type, site = _claims_service()

    created = await _create(service, request_type, site, _claim(request_type, site))

    request = created.request
    assert request.description is None
    assert request.case_subject == "Calidad del producto · Producto vencido"
    assert request.dependency_id == DISTRIBUTION
    [product] = request.products
    assert (product.description, product.supplier_name) == ("Aceite suavizante", "Unilever Andina")
    assert request.pickup_order_requested == "no"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"subject_id": None}, "Selecciona el asunto"),
        ({"subject_id": uuid4()}, "no está disponible"),
        ({"cause_id": None}, "Selecciona la causal"),
        ({"cause_id": uuid4()}, "no pertenece"),
        ({"products": []}, "al menos un producto"),
        ({"products": [_line(sku="0000")]}, "no existe en el maestro"),
        ({"products": [_line(affected_quantity=0)]}, "mayor que cero"),
        ({"pickup_order_requested": None}, "orden de recogida"),
        ({"pickup_order_number": "OR-55"}, "solo si ya la solicitaste"),
        ({"cause_id": NO_PRODUCT.id}, "no admite productos"),
    ],
)
async def test_a_claim_is_validated_against_the_catalog(overrides, message) -> None:
    service, request_type, site = _claims_service()

    with pytest.raises(InvalidInputError, match=message):
        await _create(service, request_type, site, _claim(request_type, site, **overrides))


@pytest.mark.asyncio
async def test_other_requires_a_description() -> None:
    service, request_type, site = _claims_service()
    values = _claim(
        request_type, site, cause_id=OTHER_CAUSE.id, products=[], pickup_order_requested=None
    )

    with pytest.raises(InvalidInputError, match="Otro"):
        await _create(service, request_type, site, values)

    created = await _create(
        service, request_type, site, {**values, "description": "Llegó con olor extraño"}
    )
    assert created.request.case_subject == "Calidad del producto · Otro"


@pytest.mark.asyncio
async def test_a_subject_without_causes_takes_none() -> None:
    service, request_type, site = _claims_service()
    values = _claim(
        request_type,
        site,
        subject_id=OTHER_SUBJECT.id,
        cause_id=None,
        products=[],
        pickup_order_requested=None,
        subject="Cobro doble",
        description="Nos facturaron dos veces",
    )

    created = await _create(service, request_type, site, values)

    assert created.request.case_subject == "Otro: Cobro doble"
    assert created.request.dependency_id == QUALITY
    with pytest.raises(InvalidInputError, match="no tiene causales"):
        await _create(service, request_type, site, {**values, "cause_id": EXPIRED.id})


@pytest.mark.asyncio
async def test_the_pickup_order_number_goes_with_yes() -> None:
    service, request_type, site = _claims_service()
    values = _claim(request_type, site, pickup_order_requested="yes", pickup_order_number=" OR-55 ")

    created = await _create(service, request_type, site, values)

    assert created.request.pickup_order_number == "OR-55"


@pytest.mark.asyncio
async def test_types_without_subjects_keep_the_typed_subject() -> None:
    service, request_type, site = _service()
    values = {**_payload(request_type, site, str(uuid4())), "subject_id": PRODUCT_SUBJECT.id}

    with pytest.raises(InvalidInputError, match="no aplican"):
        await _create(service, request_type, site, values)


@pytest.mark.asyncio
async def test_product_search_needs_two_characters() -> None:
    service, _, _ = _claims_service()

    with pytest.raises(InvalidInputError, match="2 caracteres"):
        await service.search_products(" a ")
    assert [p.sku for p in await service.search_products("aceite")] == ["9834"]


def test_the_form_sends_product_lines_as_json() -> None:
    [line] = _product_lines(
        '[{"invoice_number": "FE-1", "sku": "9834", "lot": "L1",'
        ' "expiry_date": "2026-12-01", "affected_quantity": 2}]'
    )
    assert (line.sku, line.expiry_date, line.affected_quantity) == ("9834", date(2026, 12, 1), 2)
    assert _product_lines(None) == [] and _product_lines("  ") == []
    for broken in (
        "no es json",
        '[{"sku": "9834"}]',
        '[{"invoice_number": "F", "sku": "1", '
        '"lot": "L", "expiry_date": "2026-12-01", "affected_quantity": 0}]',
    ):
        with pytest.raises(InvalidInputError, match="productos"):
            _product_lines(broken)


class FakeVideos:
    async def inspect(self, filename, content_type, content):
        return SanitizedImage(filename, "mp4", "video/mp4", content)


class FakeScanner:
    async def scan(self, content):
        return None


@pytest.mark.asyncio
async def test_videos_follow_the_evidence_rules_of_the_type() -> None:
    service, request_type, site = _service()
    service.video_inspector, service.malware_scanner = FakeVideos(), FakeScanner()
    clip = ImageUpload(filename="caja.mp4", content_type="video/mp4", content=b"v")
    values = _payload(request_type, site, str(uuid4()))

    with pytest.raises(InvalidInputError, match="Máximo 1 video"):
        await service.create_request(
            request_type_id=request_type.id,
            site_id=site.id,
            **{k: v for k, v in values.items() if k not in {"request_type_id", "site_id"}},
            idempotency_payload=values,
            videos=[clip, clip],
        )
    documents = _document_values(request_type, site)
    with pytest.raises(InvalidInputError, match="no admite evidencias"):
        await service.create_request(
            request_type_id=request_type.id,
            site_id=site.id,
            **{k: v for k, v in documents.items() if k not in {"request_type_id", "site_id"}},
            idempotency_payload=documents,
            videos=[clip],
        )
    no_evidence, other_type, other_site = _service(petition=False)
    no_evidence.video_inspector = FakeVideos()
    plain = _payload(other_type, other_site, str(uuid4()))
    with pytest.raises(InvalidInputError, match="no admite videos"):
        await no_evidence.create_request(
            request_type_id=other_type.id,
            site_id=other_site.id,
            **{k: v for k, v in plain.items() if k not in {"request_type_id", "site_id"}},
            idempotency_payload=plain,
            videos=[clip],
        )


# --- Area picked by the requester ---

COMMERCIAL = uuid4()


class FakeAreas(FakePqrsfRepository):
    async def list_areas(self):
        return [Dependency(COMMERCIAL, "comercial", "Comercial", 1, True, NOW, NOW)]


def _area_service(asks_area: bool = True) -> tuple[PqrsfService, RequestType, Site]:
    request_type = replace(_type(), asks_area=asks_area)
    site = Site(uuid4(), "bogota", "Bogotá", True, NOW, NOW, "BG")

    return (
        PqrsfService(FakeAreas(request_type), SiteService(FakeSiteRepository(site))),
        request_type,
        site,
    )


@pytest.mark.asyncio
async def test_the_area_is_required_where_the_type_asks_for_it() -> None:
    service, request_type, site = _area_service()
    values = _payload(request_type, site, str(uuid4()))

    with pytest.raises(InvalidInputError, match="Selecciona el área"):
        await _create(service, request_type, site, values)
    with pytest.raises(InvalidInputError, match="no está disponible"):
        await _create(service, request_type, site, {**values, "area_id": uuid4()})

    created = await _create(service, request_type, site, {**values, "area_id": COMMERCIAL})
    assert created.request.area.name == "Comercial"
    # Nothing else decides: the area attends it.
    assert created.request.dependency_id == COMMERCIAL


@pytest.mark.asyncio
async def test_a_type_that_does_not_ask_for_the_area_refuses_it() -> None:
    service, request_type, site = _area_service(asks_area=False)
    values = {**_payload(request_type, site, str(uuid4())), "area_id": COMMERCIAL}

    with pytest.raises(InvalidInputError, match="no pide área"):
        await _create(service, request_type, site, values)


@pytest.mark.asyncio
async def test_the_document_category_outranks_the_area() -> None:
    service, request_type, site = _area_service()
    values = _document_values(request_type, site, area_id=COMMERCIAL)

    created = await _create(service, request_type, site, values)

    assert created.request.dependency_id == COMMERCIAL  # the fake does not load categories
    request = replace(created.request, document_dependency_id=CATEGORY.dependency_id)
    assert request.dependency_id == CATEGORY.dependency_id
