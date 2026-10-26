"""Subjects and causes of the PQRSF forms, kept by the PQRSF managers."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.classification import (
    MAX_OPTION_DESCRIPTION,
    ClassificationService,
)
from app.modules.pqrsf.domain.entities import Cause, Subject

router = APIRouter(prefix="/pqrsf")
_classification = Depends(Provide[ApplicationContainer.v1_container.pqrsf_classification_service])
_principal = Depends(get_current_principal)

RequestTypeCode = Literal["peticion", "queja", "reclamo", "sugerencia", "felicitacion"]
Route = Literal["supplier", "internal", "none"]


class CauseAdminResponse(BaseModel):
    """A cause with what decides the treatment of its cases."""

    id: UUID
    subject_id: UUID
    code: str
    name: str
    description: str | None
    # Dependency that attends the case.
    dependency_id: UUID | None
    # ``supplier``: may be escalated to the manufacturer; ``internal``: a JDH process failure.
    route: Route
    requires_product: bool
    allows_product: bool
    is_other: bool
    sort_order: int
    is_active: bool


class SubjectAdminResponse(BaseModel):
    """A subject with its causes."""

    id: UUID
    code: str
    name: str
    description: str | None
    request_type_codes: list[RequestTypeCode]
    dependency_id: UUID | None
    is_other: bool
    sort_order: int
    is_active: bool
    causes: list[CauseAdminResponse]


class CreateSubjectRequest(BaseModel):
    """New subject."""

    code: str = Field(max_length=40)
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=MAX_OPTION_DESCRIPTION)
    # Validated by the service, so a mistake comes back with a message in Spanish.
    request_type_codes: list[str] = Field(default_factory=list, max_length=5)
    dependency_id: UUID | None = None
    is_other: bool = False
    sort_order: int = 0


class UpdateSubjectRequest(BaseModel):
    """Subject fields to change; omitted fields are kept, ``null`` clears."""

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=MAX_OPTION_DESCRIPTION)
    request_type_codes: list[str] | None = Field(default=None, max_length=5)
    dependency_id: UUID | None = None
    is_other: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CreateCauseRequest(BaseModel):
    """New cause of a subject."""

    code: str = Field(max_length=40)
    name: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=MAX_OPTION_DESCRIPTION)
    dependency_id: UUID | None = None
    route: str = Field(default="none", max_length=10)
    requires_product: bool = False
    allows_product: bool = False
    is_other: bool = False
    sort_order: int = 0


class UpdateCauseRequest(BaseModel):
    """Cause fields to change; omitted fields are kept, ``null`` clears."""

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=MAX_OPTION_DESCRIPTION)
    dependency_id: UUID | None = None
    route: str | None = Field(default=None, max_length=10)
    requires_product: bool | None = None
    allows_product: bool | None = None
    is_other: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


def _cause(value: Cause) -> CauseAdminResponse:
    return CauseAdminResponse.model_validate(value, from_attributes=True)


def _subject(value: Subject) -> SubjectAdminResponse:
    return SubjectAdminResponse(
        id=value.id,
        code=value.code,
        name=value.name,
        description=value.description,
        request_type_codes=list(value.request_type_codes),
        dependency_id=value.dependency_id,
        is_other=value.is_other,
        sort_order=value.sort_order,
        is_active=value.is_active,
        causes=[_cause(cause) for cause in value.causes],
    )


@router.get("/subjects", response_model=list[SubjectAdminResponse], summary="List subjects")
@inject
async def list_subjects(
    include_inactive: bool = Query(default=False),
    principal: Principal = _principal,
    service: ClassificationService = _classification,
) -> list[SubjectAdminResponse]:
    """Subjects with their causes, dependency and route (disabled ones for managers)."""

    return [_subject(value) for value in await service.list_subjects(principal, include_inactive)]


@router.post(
    "/subjects",
    response_model=SubjectAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create subject",
)
@inject
async def create_subject(
    body: CreateSubjectRequest,
    principal: Principal = _principal,
    service: ClassificationService = _classification,
) -> SubjectAdminResponse:
    """Add a subject to the forms (managers)."""

    return _subject(await service.create_subject(principal, **body.model_dump()))


@router.patch(
    "/subjects/{subject_id}", response_model=SubjectAdminResponse, summary="Update subject"
)
@inject
async def update_subject(
    subject_id: UUID,
    body: UpdateSubjectRequest,
    principal: Principal = _principal,
    service: ClassificationService = _classification,
) -> SubjectAdminResponse:
    """Edit or deactivate a subject; its code is fixed (managers)."""

    return _subject(
        await service.update_subject(
            subject_id, principal, fields=frozenset(body.model_fields_set), **body.model_dump()
        )
    )


@router.post(
    "/subjects/{subject_id}/causes",
    response_model=CauseAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create cause",
)
@inject
async def create_cause(
    subject_id: UUID,
    body: CreateCauseRequest,
    principal: Principal = _principal,
    service: ClassificationService = _classification,
) -> CauseAdminResponse:
    """Add a cause to a subject (managers)."""

    return _cause(await service.create_cause(subject_id, principal, **body.model_dump()))


@router.patch("/causes/{cause_id}", response_model=CauseAdminResponse, summary="Update cause")
@inject
async def update_cause(
    cause_id: UUID,
    body: UpdateCauseRequest,
    principal: Principal = _principal,
    service: ClassificationService = _classification,
) -> CauseAdminResponse:
    """Edit or deactivate a cause; its code and subject are fixed (managers)."""

    return _cause(
        await service.update_cause(
            cause_id, principal, fields=frozenset(body.model_fields_set), **body.model_dump()
        )
    )
