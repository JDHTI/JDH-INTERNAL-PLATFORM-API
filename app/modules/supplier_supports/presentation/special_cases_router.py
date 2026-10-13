"""Endpoints of the special cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.special_cases import (
    MAX_PAGE_SIZE,
    SpecialCaseService,
)
from app.modules.supplier_supports.domain.special_cases import SpecialCaseData, SpecialCaseView

router = APIRouter(dependencies=[Depends(rate_limit("medium"))])
_service = Depends(Provide[ApplicationContainer.v1_container.special_case_service])
_principal = Depends(get_current_principal)

CaseStatus = Literal["pendiente", "en_analisis", "programado", "implementado", "descartado"]
CasePriority = Literal["baja", "media", "alta"]


class SpecialCaseCreate(BaseModel):
    """A new request. ``supplier_code`` is the commercial supplier code (Cod. Proveedor)."""

    title: str = Field(min_length=3, max_length=200)
    request: str = Field(min_length=3, max_length=4000)
    supplier_code: str | None = Field(default=None, max_length=20)
    customer_nit: str | None = Field(default=None, max_length=30)
    customer_name: str | None = Field(default=None, max_length=300)
    rule_to_program: str | None = Field(default=None, max_length=4000)
    status: CaseStatus = "pendiente"
    priority: CasePriority = "media"
    assignee: str | None = Field(default=None, max_length=320)
    resolution: str | None = Field(default=None, max_length=4000)


class SpecialCasePatch(BaseModel):
    """Fields to change; anything omitted is left as it is, ``null`` clears it."""

    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=3, max_length=200)
    request: str | None = Field(default=None, min_length=3, max_length=4000)
    supplier_code: str | None = Field(default=None, max_length=20)
    customer_nit: str | None = Field(default=None, max_length=30)
    customer_name: str | None = Field(default=None, max_length=300)
    rule_to_program: str | None = Field(default=None, max_length=4000)
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    assignee: str | None = Field(default=None, max_length=320)
    resolution: str | None = Field(default=None, max_length=4000)


class SpecialCaseResponse(BaseModel):
    """A special case with its supplier and the number of supports linked to it."""

    id: UUID
    code: str
    title: str
    request: str
    rule_to_program: str | None
    status: str
    priority: str
    assignee: str | None
    resolution: str | None
    supplier_code: str | None
    supplier_name: str | None
    customer_nit: str | None
    customer_name: str | None
    support_count: int
    source_key: str | None
    created_by: str
    updated_by: str
    version: int
    created_at: datetime
    updated_at: datetime


class SpecialCasePageResponse(BaseModel):
    """A page of cases."""

    items: list[SpecialCaseResponse]
    total: int
    page: int
    size: int


def _response(view: SpecialCaseView) -> SpecialCaseResponse:
    case, data = view.case, view.case.data
    return SpecialCaseResponse(
        id=case.id,
        code=case.code,
        title=data.title,
        request=data.request,
        rule_to_program=data.rule_to_program,
        status=data.status,
        priority=data.priority,
        assignee=data.assignee,
        resolution=data.resolution,
        supplier_code=view.supplier_code,
        supplier_name=view.supplier_name,
        customer_nit=data.customer_nit,
        customer_name=data.customer_name,
        support_count=view.support_count,
        source_key=data.source_key,
        created_by=case.created_by,
        updated_by=case.updated_by,
        version=case.version,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


@router.get("", response_model=SpecialCasePageResponse, summary="List special cases")
@inject
async def list_cases(
    q: str | None = Query(default=None, max_length=100),
    status: list[CaseStatus] = Query(default=[]),  # noqa: B008
    customer_nit: str | None = Query(default=None, max_length=30),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    _: Principal = _principal,
    service: SpecialCaseService = _service,
) -> SpecialCasePageResponse:
    """Search special cases by text, status and client NIT, newest first."""

    result = await service.search(
        q=q, status=status, customer_nit=customer_nit, page=page, size=size
    )
    return SpecialCasePageResponse(
        items=[_response(view) for view in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post("", response_model=SpecialCaseResponse, status_code=201, summary="Open a case")
@inject
async def create_case(
    body: SpecialCaseCreate,
    principal: Principal = _principal,
    service: SpecialCaseService = _service,
) -> SpecialCaseResponse:
    """Open a request for a particular client. Managers only."""

    data = SpecialCaseData(
        title=body.title,
        request=body.request,
        customer_nit=body.customer_nit,
        customer_name=body.customer_name,
        rule_to_program=body.rule_to_program,
        status=body.status,
        priority=body.priority,
        assignee=body.assignee,
        resolution=body.resolution,
    )
    return _response(await service.create(principal, data, supplier_code=body.supplier_code))


@router.get("/{case_id}", response_model=SpecialCaseResponse, summary="Get a case")
@inject
async def get_case(
    case_id: UUID,
    _: Principal = _principal,
    service: SpecialCaseService = _service,
) -> SpecialCaseResponse:
    """Return one case."""

    return _response(await service.get(case_id))


@router.patch("/{case_id}", response_model=SpecialCaseResponse, summary="Update a case")
@inject
async def update_case(
    case_id: UUID,
    body: SpecialCasePatch,
    principal: Principal = _principal,
    service: SpecialCaseService = _service,
) -> SpecialCaseResponse:
    """Change only the fields sent. A stale ``version`` is a 409. Managers only."""

    sent = body.model_fields_set - {"version", "supplier_code"}
    changes: dict[str, Any] = {name: getattr(body, name) for name in sent}
    view = await service.update(
        principal,
        case_id,
        expected_version=body.version,
        changes=changes,
        supplier_code=body.supplier_code,
    )
    return _response(view)


__all__ = ["router"]
