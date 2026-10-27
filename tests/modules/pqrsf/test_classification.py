from dataclasses import replace
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.pqrsf.application.classification import ClassificationService
from app.modules.pqrsf.domain.entities import Subject

PRINCIPAL = object()
QUALITY = uuid4()


class FakeRequests:
    def __init__(self) -> None:
        self.subjects: dict = {}
        self.causes: dict = {}

    async def list_subjects(self, active_only=True):
        result = []
        for subject in self.subjects.values():
            causes = tuple(
                cause
                for cause in self.causes.values()
                if cause.subject_id == subject.id and (cause.is_active or not active_only)
            )
            if subject.is_active or not active_only:
                result.append(replace(subject, causes=causes))
        return result

    async def save_subject(self, subject):
        self.subjects[subject.id] = replace(subject, causes=())
        return self.subjects[subject.id]

    async def save_cause(self, cause):
        self.causes[cause.id] = cause
        return cause


class FakeCases:
    async def get_dependency(self, dependency_id):
        return object() if dependency_id == QUALITY else None


class FakeManagers:
    def __init__(self, manager: bool = True) -> None:
        self.manager = manager

    async def is_manager(self, principal):
        return self.manager


def _service(manager: bool = True) -> ClassificationService:
    return ClassificationService(FakeRequests(), FakeCases(), FakeManagers(manager))


async def _subject(service: ClassificationService, code: str = "calidad_producto") -> Subject:
    return await service.create_subject(
        PRINCIPAL,
        code=code,
        name="Calidad del producto",
        request_type_codes=["queja", "reclamo"],
        description="Estado o calidad del producto recibido.",
        dependency_id=QUALITY,
    )


@pytest.mark.asyncio
async def test_a_manager_creates_a_subject_and_its_causes() -> None:
    service = _service()
    subject = await _subject(service)

    cause = await service.create_cause(
        subject.id,
        PRINCIPAL,
        code="vencido",
        name="Producto vencido",
        route="internal",
        requires_product=True,
    )

    [listed] = await service.list_subjects(PRINCIPAL, include_inactive=False)
    assert listed.request_type_codes == ("queja", "reclamo")
    assert [c.code for c in listed.causes] == ["vencido"]
    # Requiring products implies offering them.
    assert cause.allows_product is True


@pytest.mark.asyncio
async def test_only_managers_change_the_catalog() -> None:
    with pytest.raises(ForbiddenError):
        await _subject(_service(manager=False))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "error", "message"),
    [
        ({"request_type_codes": []}, InvalidInputError, "al menos un tipo"),
        ({"request_type_codes": ["otro"]}, InvalidInputError, "desconocido"),
        ({"dependency_id": uuid4()}, InvalidInputError, "dependencia no existe"),
        ({"code": "Con Espacios"}, InvalidInputError, "minúsculas"),
        ({"description": "x" * 121}, InvalidInputError, "120"),
    ],
)
async def test_a_subject_is_validated(values, error, message) -> None:
    service = _service()
    base = {"code": "nuevo", "name": "Nuevo", "request_type_codes": ["queja"]}

    with pytest.raises(error, match=message):
        await service.create_subject(PRINCIPAL, **{**base, **values})


@pytest.mark.asyncio
async def test_codes_are_unique() -> None:
    service = _service()
    subject = await _subject(service)
    await service.create_cause(subject.id, PRINCIPAL, code="vencido", name="Vencido")

    with pytest.raises(ConflictError, match="asunto"):
        await _subject(service)
    other = await _subject(service, code="entrega")
    with pytest.raises(ConflictError, match="causal"):
        await service.create_cause(other.id, PRINCIPAL, code="vencido", name="Otro vencido")


@pytest.mark.asyncio
async def test_deactivating_hides_it_from_the_form_but_not_from_managers() -> None:
    service = _service()
    subject = await _subject(service)

    await service.update_subject(
        subject.id, PRINCIPAL, fields=frozenset({"is_active"}), is_active=False
    )

    assert await service.list_subjects(PRINCIPAL, include_inactive=False) == []
    assert len(await service.list_subjects(PRINCIPAL, include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_a_cause_cannot_require_products_it_does_not_admit() -> None:
    service = _service()
    subject = await _subject(service)
    cause = await service.create_cause(
        subject.id, PRINCIPAL, code="vencido", name="Vencido", requires_product=True
    )

    with pytest.raises(InvalidInputError, match="debe admitirlos"):
        await service.update_cause(
            cause.id, PRINCIPAL, fields=frozenset({"allows_product"}), allows_product=False
        )
    with pytest.raises(NotFoundError):
        await service.update_cause(uuid4(), PRINCIPAL, fields=frozenset())


@pytest.mark.asyncio
async def test_the_description_can_be_cleared() -> None:
    service = _service()
    subject = await _subject(service)

    updated = await service.update_subject(
        subject.id, PRINCIPAL, fields=frozenset({"description"}), description=None
    )

    assert updated.description is None
