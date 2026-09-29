"""Authenticated endpoints for the follow-up of PQRSF cases."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.dto import (
    CaseChanges,
    CaseDetail,
    CaseView,
    EventView,
    ItemChanges,
)
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.domain.entities import RequestType
from app.modules.pqrsf.domain.tracking import (
    ActivityFilters,
    CaseFilters,
    CaseInput,
    CaseItem,
    CaseStatus,
    CaseSummary,
    Dependency,
    DocumentType,
    ItemInput,
)
from app.modules.pqrsf.presentation.router import PqrsfRequestResponse, _request
from app.modules.sites.domain.entities import Site

# Interactive inbox behind a session: the limit is per IP and offices share one
# behind NAT, so it uses the relaxed profile.
router = APIRouter(prefix="/pqrsf", dependencies=[Depends(rate_limit("relaxed"))])
_tracking = Depends(Provide[ApplicationContainer.v1_container.pqrsf_tracking_service])
_pqrsf = Depends(Provide[ApplicationContainer.v1_container.pqrsf_service])
_principal = Depends(get_current_principal)

SortOption = Literal[
    "received_on", "-received_on", "due_on", "-due_on", "code", "-code", "updated_at", "-updated_at"
]

# --- Response models ---


class TrackingRequestTypeResponse(BaseModel):
    """Request type with its follow-up settings."""

    id: UUID
    code: str
    name: str
    prefix: str
    requires_follow_up: bool
    sla_days: int | None
    allows_document_request: bool
    allows_attachments: bool
    is_active: bool


class CaseStatusResponse(BaseModel):
    """Configurable follow-up status."""

    id: UUID
    code: str
    name: str
    color: str | None
    sort_order: int
    is_initial: bool
    is_terminal: bool
    is_active: bool


class DependencyResponse(BaseModel):
    """Internal dependency."""

    id: UUID
    code: str
    name: str
    sort_order: int
    is_active: bool


class DocumentTypeResponse(BaseModel):
    """Requestable document and its response time in business days."""

    id: UUID
    name: str
    sla_days: int
    is_active: bool


class TrackingSiteResponse(BaseModel):
    """Site (sucursal)."""

    id: UUID
    code: str
    name: str
    # Site part of the consecutive (P26AC0001).
    abbreviation: str
    is_active: bool


class TrackingCatalogResponse(BaseModel):
    """Selectors for the follow-up screens."""

    request_types: list[TrackingRequestTypeResponse]
    statuses: list[CaseStatusResponse]
    dependencies: list[DependencyResponse]
    document_types: list[DocumentTypeResponse]
    sites: list[TrackingSiteResponse]
    is_manager: bool


class PersonResponse(BaseModel):
    """A platform user shown in a case."""

    id: UUID
    display_name: str | None
    email: str | None


class CaseRequestTypeResponse(BaseModel):
    """Compact request type inside a case."""

    id: UUID
    code: str
    name: str
    prefix: str


class CaseItemResponse(BaseModel):
    """Requested document with its response times."""

    id: UUID
    document_type: DocumentTypeResponse | None
    description: str | None
    quantity: int | None
    sla_days: int | None
    due_on: date | None
    responded_on: date | None
    response_days: int | None
    difference_days: int | None
    is_late: bool
    # Unanswered, past its due date, in an open case.
    is_overdue: bool
    delay_cause: str | None
    delay_detail: str | None


class CasePermissionsResponse(BaseModel):
    """What the caller may do with the case."""

    can_work: bool
    can_assign: bool
    can_delete: bool


class CaseResponse(BaseModel):
    """A follow-up case."""

    id: UUID
    code: str
    request_type: CaseRequestTypeResponse
    status: CaseStatusResponse
    origin: str
    subject: str | None
    description: str | None
    received_on: date
    due_on: date | None
    is_overdue: bool
    site: TrackingSiteResponse | None
    dependency: DependencyResponse | None
    assignee: PersonResponse | None
    created_by: PersonResponse | None
    customer_name: str | None
    customer_nit: str | None
    supplier_name: str | None
    sales_executive_name: str | None
    requester_name: str | None
    requester_email: str | None
    request_id: UUID | None
    first_read_at: datetime | None
    closed_at: datetime | None
    legacy: dict | None
    items: list[CaseItemResponse]
    item_count: int
    responded_count: int
    permissions: CasePermissionsResponse
    created_at: datetime
    updated_at: datetime


class CaseEventResponse(BaseModel):
    """Timeline entry."""

    id: UUID
    event_type: str
    actor: PersonResponse | None
    from_status_id: UUID | None
    to_status_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime


class ActivityEventResponse(CaseEventResponse):
    """History entry of the whole module; ``case_id`` is empty once deleted."""

    case_id: UUID | None
    case_code: str | None


class ActivityPageResponse(BaseModel):
    """A page of the module history; pass ``next_before`` to get the next one."""

    items: list[ActivityEventResponse]
    next_before: datetime | None


class LastSeenResponse(BaseModel):
    """When a person last opened the PQRSF inbox."""

    user: PersonResponse
    last_seen_at: datetime


class CaseDetailResponse(BaseModel):
    """A case, its timeline and the public intake record when there is one."""

    case: CaseResponse
    events: list[CaseEventResponse]
    request: PqrsfRequestResponse | None


class CasePageResponse(BaseModel):
    """One page of the case inbox."""

    items: list[CaseResponse]
    total: int
    page: int
    size: int


class CountBucketResponse(BaseModel):
    """Labelled count."""

    id: UUID
    code: str
    name: str
    count: int
    color: str | None


class CaseSummaryResponse(BaseModel):
    """Inbox indicators."""

    total: int
    open: int
    overdue: int
    due_today: int
    unassigned: int
    by_status: list[CountBucketResponse]
    by_type: list[CountBucketResponse]


# --- Request bodies ---


class ItemRequest(BaseModel):
    """Document to request."""

    document_type_id: UUID | None = None
    description: str | None = Field(default=None, max_length=300)
    quantity: int | None = Field(default=None, ge=1, le=100_000)


class CreateCaseRequest(BaseModel):
    """Case raised by a collaborator."""

    request_type_id: UUID
    received_on: date | None = None
    subject: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    site_id: UUID | None = None
    dependency_id: UUID | None = None
    customer_name: str | None = Field(default=None, max_length=200)
    customer_nit: str | None = Field(default=None, max_length=30)
    supplier_name: str | None = Field(default=None, max_length=200)
    sales_executive_name: str | None = Field(default=None, max_length=200)
    requester_name: str | None = Field(default=None, max_length=200)
    requester_email: str | None = Field(default=None, max_length=320)
    assignee_user_id: UUID | None = None
    items: list[ItemRequest] = Field(default_factory=list, max_length=200)


class UpdateCaseRequest(BaseModel):
    """Header fields to change; omitted fields are kept, ``null`` clears."""

    subject: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    received_on: date | None = None
    site_id: UUID | None = None
    dependency_id: UUID | None = None
    customer_name: str | None = Field(default=None, max_length=200)
    customer_nit: str | None = Field(default=None, max_length=30)
    supplier_name: str | None = Field(default=None, max_length=200)
    sales_executive_name: str | None = Field(default=None, max_length=200)
    requester_name: str | None = Field(default=None, max_length=200)
    requester_email: str | None = Field(default=None, max_length=320)


class AssignRequest(BaseModel):
    """New responsible; ``null`` leaves the case unassigned."""

    assignee_user_id: UUID | None
    note: str | None = Field(default=None, max_length=2_000)


class StatusChangeRequest(BaseModel):
    """Target status."""

    status_id: UUID
    note: str | None = Field(default=None, max_length=2_000)


class DeleteCaseRequest(BaseModel):
    """Why the case is deleted; it stays in the history."""

    reason: str | None = Field(default=None, max_length=2000)


class CommentRequest(BaseModel):
    """Timeline comment."""

    body: str = Field(min_length=1, max_length=5_000)


class UpdateItemRequest(BaseModel):
    """Document fields to change; set ``responded_on`` to register the answer."""

    document_type_id: UUID | None = None
    description: str | None = Field(default=None, max_length=300)
    quantity: int | None = Field(default=None, ge=1, le=100_000)
    responded_on: date | None = None
    delay_cause: str | None = Field(default=None, max_length=300)
    delay_detail: str | None = Field(default=None, max_length=2_000)


class UpdateRequestTypeRequest(BaseModel):
    """Follow-up settings of a request type."""

    prefix: str | None = Field(default=None, max_length=5)
    requires_follow_up: bool | None = None
    sla_days: int | None = Field(default=None, ge=0, le=365)


class CreateStatusRequest(BaseModel):
    """New status."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    color: str | None = Field(default=None, max_length=7)
    sort_order: int = 0
    is_initial: bool = False
    is_terminal: bool = False


class UpdateStatusRequest(BaseModel):
    """Status fields to change."""

    name: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=7)
    sort_order: int | None = None
    is_initial: bool | None = None
    is_terminal: bool | None = None
    is_active: bool | None = None


class CreateDependencyRequest(BaseModel):
    """New dependency."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = 0


class UpdateDependencyRequest(BaseModel):
    """Dependency fields to change."""

    name: str | None = Field(default=None, max_length=120)
    sort_order: int | None = None
    is_active: bool | None = None


class CreateDocumentTypeRequest(BaseModel):
    """New document type."""

    name: str = Field(min_length=1, max_length=200)
    sla_days: int = Field(ge=0, le=365)


class UpdateDocumentTypeRequest(BaseModel):
    """Document type fields to change."""

    name: str | None = Field(default=None, max_length=200)
    sla_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None


# --- Mappers ---


def _person(user: DirectoryUser | None) -> PersonResponse | None:
    if user is None:
        return None
    return PersonResponse(id=user.id, display_name=user.display_name, email=user.email)


def _request_type(value: RequestType) -> TrackingRequestTypeResponse:
    return TrackingRequestTypeResponse.model_validate(value, from_attributes=True)


def _status(value: CaseStatus) -> CaseStatusResponse:
    return CaseStatusResponse.model_validate(value, from_attributes=True)


def _dependency(value: Dependency) -> DependencyResponse:
    return DependencyResponse.model_validate(value, from_attributes=True)


def _document_type(value: DocumentType) -> DocumentTypeResponse:
    return DocumentTypeResponse.model_validate(value, from_attributes=True)


def _site(value: Site) -> TrackingSiteResponse:
    return TrackingSiteResponse.model_validate(value, from_attributes=True)


def _item(value: CaseItem, overdue_ids: frozenset[UUID]) -> CaseItemResponse:
    return CaseItemResponse(
        is_overdue=value.id in overdue_ids,
        id=value.id,
        document_type=_document_type(value.document_type) if value.document_type else None,
        description=value.description,
        quantity=value.quantity,
        sla_days=value.sla_days,
        due_on=value.due_on,
        responded_on=value.responded_on,
        response_days=value.response_days,
        difference_days=value.difference_days,
        is_late=value.is_late,
        delay_cause=value.delay_cause,
        delay_detail=value.delay_detail,
    )


def _case(view: CaseView) -> CaseResponse:
    case = view.case
    return CaseResponse(
        id=case.id,
        code=case.code,
        request_type=CaseRequestTypeResponse(
            id=case.request_type.id,
            code=case.request_type.code,
            name=case.request_type.name,
            prefix=case.request_type.prefix,
        ),
        status=_status(case.status),
        origin=case.origin,
        subject=case.subject,
        description=case.description,
        received_on=case.received_on,
        due_on=case.due_on,
        is_overdue=view.is_overdue,
        site=_site(case.site) if case.site else None,
        dependency=_dependency(case.dependency) if case.dependency else None,
        assignee=_person(view.assignee),
        created_by=_person(view.created_by),
        customer_name=case.customer_name,
        customer_nit=case.customer_nit,
        supplier_name=case.supplier_name,
        sales_executive_name=case.sales_executive_name,
        requester_name=case.requester_name,
        requester_email=case.requester_email,
        request_id=case.request_id,
        first_read_at=case.first_read_at,
        closed_at=case.closed_at,
        legacy=case.legacy,
        items=[_item(item, view.overdue_item_ids) for item in case.items],
        item_count=len(case.items),
        responded_count=sum(1 for item in case.items if item.responded_on is not None),
        permissions=CasePermissionsResponse(
            can_work=view.access.can_work,
            can_assign=view.access.can_assign,
            can_delete=view.access.can_delete,
        ),
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _event(value: EventView) -> CaseEventResponse:
    event = value.event
    return CaseEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor=_person(value.actor),
        from_status_id=event.from_status_id,
        to_status_id=event.to_status_id,
        note=event.note,
        data=event.data,
        created_at=event.created_at,
    )


def _activity(value: EventView) -> ActivityEventResponse:
    return ActivityEventResponse(
        **_event(value).model_dump(),
        case_id=value.event.case_id,
        case_code=value.event.case_code,
    )


def _detail(value: CaseDetail) -> CaseDetailResponse:
    return CaseDetailResponse(
        case=_case(value.view),
        events=[_event(event) for event in value.events],
        request=_request(value.request) if value.request else None,
    )


def _summary(value: CaseSummary) -> CaseSummaryResponse:
    return CaseSummaryResponse(
        total=value.total,
        open=value.open,
        overdue=value.overdue,
        due_today=value.due_today,
        unassigned=value.unassigned,
        by_status=[
            CountBucketResponse.model_validate(b, from_attributes=True) for b in value.by_status
        ],
        by_type=[
            CountBucketResponse.model_validate(b, from_attributes=True) for b in value.by_type
        ],
    )


def _filters(
    status_id: list[UUID] = Query(default=[]),  # noqa: B008
    request_type_id: UUID | None = None,
    assignee_user_id: UUID | None = None,
    unassigned: bool = False,
    dependency_id: UUID | None = None,
    site_id: UUID | None = None,
    open_only: bool = False,
    overdue: bool = False,
    due_today: bool = False,
    received_from: date | None = None,
    received_to: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    sort: SortOption = "-received_on",
) -> CaseFilters:
    """Inbox filters shared by the list, summary and export endpoints."""

    return CaseFilters(
        status_ids=tuple(status_id),
        request_type_id=request_type_id,
        assignee_user_id=assignee_user_id,
        unassigned=unassigned,
        dependency_id=dependency_id,
        site_id=site_id,
        open_only=open_only,
        overdue=overdue,
        due_today=due_today,
        received_from=received_from,
        received_to=received_to,
        query=q.strip() if q and q.strip() else None,
        sort=sort,
    )


_case_filters = Depends(_filters)


def _safe_cell(value: str) -> str:
    """Neutralize spreadsheet formulas in exported text."""

    return f"'{value}" if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value


# --- Catalogs ---


@router.get(
    "/tracking/catalogs", response_model=TrackingCatalogResponse, summary="Follow-up catalogs"
)
@inject
async def tracking_catalogs(
    include_inactive: bool = False,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> TrackingCatalogResponse:
    """Return request types, statuses, dependencies, document types and sites."""

    value = await service.catalog(principal, include_inactive)
    return TrackingCatalogResponse(
        request_types=[_request_type(t) for t in value.request_types],
        statuses=[_status(s) for s in value.statuses],
        dependencies=[_dependency(d) for d in value.dependencies],
        document_types=[_document_type(d) for d in value.document_types],
        sites=[_site(s) for s in value.sites],
        is_manager=value.is_manager,
    )


@router.patch(
    "/request-types/{request_type_id}",
    response_model=TrackingRequestTypeResponse,
    summary="Update request type follow-up settings",
)
@inject
async def update_request_type(
    request_type_id: UUID,
    body: UpdateRequestTypeRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> TrackingRequestTypeResponse:
    """Change prefix, follow-up flag or default response time (managers)."""

    return _request_type(
        await service.update_request_type(
            request_type_id,
            principal,
            fields=frozenset(body.model_fields_set),
            prefix=body.prefix,
            requires_follow_up=body.requires_follow_up,
            sla_days=body.sla_days,
        )
    )


@router.post(
    "/statuses",
    response_model=CaseStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create case status",
)
@inject
async def create_status(
    body: CreateStatusRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseStatusResponse:
    """Add a follow-up status (managers)."""

    return _status(await service.create_status(principal, **body.model_dump()))


@router.patch("/statuses/{status_id}", response_model=CaseStatusResponse, summary="Update status")
@inject
async def update_status(
    status_id: UUID,
    body: UpdateStatusRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseStatusResponse:
    """Edit or deactivate a status (managers)."""

    return _status(
        await service.update_status(
            status_id, principal, fields=frozenset(body.model_fields_set), **body.model_dump()
        )
    )


@router.post(
    "/dependencies",
    response_model=DependencyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create dependency",
)
@inject
async def create_dependency(
    body: CreateDependencyRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> DependencyResponse:
    """Add an internal dependency (managers)."""

    return _dependency(await service.create_dependency(principal, **body.model_dump()))


@router.patch(
    "/dependencies/{dependency_id}", response_model=DependencyResponse, summary="Update dependency"
)
@inject
async def update_dependency(
    dependency_id: UUID,
    body: UpdateDependencyRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> DependencyResponse:
    """Edit or deactivate a dependency (managers)."""

    return _dependency(
        await service.update_dependency(
            dependency_id, principal, fields=frozenset(body.model_fields_set), **body.model_dump()
        )
    )


@router.post(
    "/document-types",
    response_model=DocumentTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create document type",
)
@inject
async def create_document_type(
    body: CreateDocumentTypeRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> DocumentTypeResponse:
    """Add a document type with its response time (managers)."""

    return _document_type(await service.create_document_type(principal, **body.model_dump()))


@router.patch(
    "/document-types/{document_type_id}",
    response_model=DocumentTypeResponse,
    summary="Update document type",
)
@inject
async def update_document_type(
    document_type_id: UUID,
    body: UpdateDocumentTypeRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> DocumentTypeResponse:
    """Edit or deactivate a document type (managers)."""

    return _document_type(
        await service.update_document_type(
            document_type_id,
            principal,
            fields=frozenset(body.model_fields_set),
            **body.model_dump(),
        )
    )


@router.get("/assignees", response_model=list[PersonResponse], summary="Search assignable users")
@inject
async def search_assignees(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> list[PersonResponse]:
    """Find active users to make responsible (managers)."""

    users = await service.search_assignees(q, principal, limit)
    return [PersonResponse(id=u.id, display_name=u.display_name, email=u.email) for u in users]


# --- Cases (static paths before /cases/{case_id}) ---


@router.get("/cases", response_model=CasePageResponse, summary="List cases")
@inject
async def list_cases(
    filters: CaseFilters = _case_filters,
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=100),
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CasePageResponse:
    """Inbox: managers see every case, others their assigned or raised cases."""

    result = await service.list_cases(filters, page, size, principal, mine)
    return CasePageResponse(
        items=[_case(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/cases/summary", response_model=CaseSummaryResponse, summary="Case indicators")
@inject
async def case_summary(
    filters: CaseFilters = _case_filters,
    mine: bool = False,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseSummaryResponse:
    """Totals by status and type, overdue, due today and unassigned."""

    return _summary(await service.summary(filters, principal, mine))


@router.get("/cases/export", summary="Export cases as CSV", response_class=Response)
@inject
async def export_cases(
    filters: CaseFilters = _case_filters,
    mine: bool = False,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> Response:
    """Download the visible cases in the P.13.F.01 layout (one row per document).

    The file uses ``;`` and a UTF-8 BOM so Excel in Spanish opens it directly.
    """

    rows = await service.export_rows(filters, principal, mine)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    for row in rows:
        writer.writerow([_safe_cell(cell) for cell in row])
    filename = f"pqrsf-seguimiento-{date.today().isoformat()}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.get("/cases/suggestions", response_model=list[str], summary="Free-text suggestions")
@inject
async def case_suggestions(
    field: Literal["customer", "supplier", "sales_executive", "requester"],
    q: str = Query(default="", max_length=100),
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> list[str]:
    """Values typed before in customer, supplier, executive or requester."""

    return await service.suggest(field, q, principal)


@router.post(
    "/cases",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create case",
)
@inject
async def create_case(
    body: CreateCaseRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Raise a case internally with its requested documents."""

    values = body.model_dump(exclude={"items"})
    command = CaseInput(
        **values,
        items=tuple(ItemInput(**item.model_dump()) for item in body.items),
    )
    return _case(await service.create_case(command, principal))


@router.get("/cases/{case_id}", response_model=CaseDetailResponse, summary="Get case")
@inject
async def get_case(
    case_id: UUID,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseDetailResponse:
    """Return the case, its timeline and the public intake record."""

    return _detail(await service.get_case(case_id, principal))


@router.patch("/cases/{case_id}", response_model=CaseResponse, summary="Update case")
@inject
async def update_case(
    case_id: UUID,
    body: UpdateCaseRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Edit header fields (responsible or managers)."""

    changes = CaseChanges(fields=frozenset(body.model_fields_set), **body.model_dump())
    return _case(await service.update_case(case_id, changes, principal))


@router.post("/cases/{case_id}/assignment", response_model=CaseResponse, summary="Assign case")
@inject
async def assign_case(
    case_id: UUID,
    body: AssignRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Set or clear the responsible (managers)."""

    return _case(await service.assign(case_id, body.assignee_user_id, body.note, principal))


@router.post("/cases/{case_id}/status", response_model=CaseResponse, summary="Change status")
@inject
async def change_case_status(
    case_id: UUID,
    body: StatusChangeRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Move the case to another status (responsible or managers)."""

    return _case(await service.change_status(case_id, body.status_id, body.note, principal))


@router.post("/cases/{case_id}/read", response_model=CaseResponse, summary="Mark case as read")
@inject
async def mark_case_read(
    case_id: UUID,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Call whenever someone opens the case.

    The responsible's first opening moves it to «Leída»; every opening is
    recorded in the history (once per person every 30 minutes).
    """

    return _case(await service.mark_read(case_id, principal))


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete case")
@inject
async def delete_case(
    case_id: UUID,
    body: DeleteCaseRequest | None = None,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> None:
    """Delete a case and its documents (managers). Its history is kept."""

    await service.delete_case(case_id, body.reason if body else None, principal)


@router.post(
    "/activity/visits", status_code=status.HTTP_204_NO_CONTENT, summary="Record inbox visit"
)
@inject
async def record_inbox_visit(
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> None:
    """Call when the inbox opens; repeats within 30 minutes are not recorded."""

    await service.record_inbox_visit(principal)


@router.get("/activity", response_model=ActivityPageResponse, summary="Module history")
@inject
async def list_activity(
    before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    event_type: list[str] = Query(default=[]),  # noqa: B008
    actor_user_id: UUID | None = None,
    case_code: str | None = Query(default=None, max_length=30),
    include_access: bool = True,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> ActivityPageResponse:
    """Every recorded action, newest first (managers)."""

    page = await service.list_activity(
        ActivityFilters(
            before=before,
            limit=limit,
            event_types=tuple(event_type),
            actor_user_id=actor_user_id,
            case_code=case_code.strip() if case_code else None,
            include_access=include_access,
        ),
        principal,
    )
    return ActivityPageResponse(
        items=[_activity(item) for item in page.items], next_before=page.next_before
    )


@router.get(
    "/activity/last-seen", response_model=list[LastSeenResponse], summary="Last inbox visits"
)
@inject
async def last_seen(
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> list[LastSeenResponse]:
    """Who opened the inbox in the last 90 days, and when (managers)."""

    return [
        LastSeenResponse(
            user=_person(row.user) or PersonResponse(id=row.user_id, display_name=None, email=None),
            last_seen_at=row.last_seen_at,
        )
        for row in await service.last_seen(principal)
    ]


@router.post(
    "/cases/{case_id}/comments",
    response_model=CaseEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on case",
)
@inject
async def comment_case(
    case_id: UUID,
    body: CommentRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseEventResponse:
    """Add a comment to the timeline (anyone who can see the case)."""

    return _event(await service.add_comment(case_id, body.body, principal))


@router.post(
    "/cases/{case_id}/items",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add document to case",
)
@inject
async def add_case_item(
    case_id: UUID,
    body: ItemRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Add a requested document (responsible or managers)."""

    return _case(await service.add_item(case_id, ItemInput(**body.model_dump()), principal))


@router.patch(
    "/cases/{case_id}/items/{item_id}", response_model=CaseResponse, summary="Update document"
)
@inject
async def update_case_item(
    case_id: UUID,
    item_id: UUID,
    body: UpdateItemRequest,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Edit a document or register its response date."""

    changes = ItemChanges(fields=frozenset(body.model_fields_set), **body.model_dump())
    return _case(await service.update_item(case_id, item_id, changes, principal))


@router.delete(
    "/cases/{case_id}/items/{item_id}", response_model=CaseResponse, summary="Remove document"
)
@inject
async def remove_case_item(
    case_id: UUID,
    item_id: UUID,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseResponse:
    """Remove a requested document (responsible or managers)."""

    return _case(await service.remove_item(case_id, item_id, principal))


@router.get(
    "/cases/{case_id}/attachments/{attachment_id}/download",
    summary="Download intake attachment",
    response_class=Response,
)
@inject
async def download_case_attachment(
    case_id: UUID,
    attachment_id: UUID,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
    pqrsf: PqrsfService = _pqrsf,
) -> Response:
    """Return an image of the public request to anyone who can see the case."""

    request_id = await service.request_for_download(case_id, principal)
    attachment, content = await pqrsf.download_attachment(request_id, attachment_id)
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(attachment.original_filename)}"
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
