"""Authenticated endpoints for the follow-up of PQRSF cases."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.case_attachments import CaseAttachmentService
from app.modules.pqrsf.application.comments import CaseCommentService, UploadedFile
from app.modules.pqrsf.application.dto import (
    CaseChanges,
    CaseDetail,
    CaseView,
    EventView,
    ItemChanges,
)
from app.modules.pqrsf.application.forwards import CustomerForwardService
from app.modules.pqrsf.application.notifications import NotificationService
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.domain.entities import MAX_TYPE_DESCRIPTION, RequestType
from app.modules.pqrsf.domain.notifications import (
    JobSchedule,
    Notification,
    NotificationSettings,
    parse_time,
)
from app.modules.pqrsf.domain.tracking import (
    ActivityFilters,
    CaseFilters,
    CaseInput,
    CaseItem,
    CaseStatus,
    CaseSummary,
    CatalogRef,
    Dependency,
    DocumentType,
    ItemInput,
    TrackingCase,
)
from app.modules.pqrsf.presentation.router import PqrsfRequestResponse, _request
from app.modules.sites.domain.entities import Site

# Interactive inbox behind a session: the limit is per IP and offices share one
# behind NAT, so it uses the relaxed profile.
router = APIRouter(prefix="/pqrsf")
_READ_GATE = Depends(require_permission("pqrsf"))
_tracking = Depends(Provide[ApplicationContainer.v1_container.pqrsf_tracking_service])
_pqrsf = Depends(Provide[ApplicationContainer.v1_container.pqrsf_service])
_case_files = Depends(Provide[ApplicationContainer.v1_container.pqrsf_case_attachment_service])
_comments = Depends(Provide[ApplicationContainer.v1_container.pqrsf_comment_service])
_forwards = Depends(Provide[ApplicationContainer.v1_container.pqrsf_forward_service])
_notifications = Depends(Provide[ApplicationContainer.v1_container.pqrsf_notification_service])
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
    handling: Literal["case", "acknowledge"]
    allows_document_request: bool
    allows_attachments: bool
    is_active: bool
    # One short line shown under the type on the forms.
    description: str | None = None
    # Whether the public form asks the requester for the area.
    asks_area: bool = False


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
    # The answer can be uploaded straight to the case, without the document manager.
    allows_direct_upload: bool = False


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


class SalesExecutiveResponse(BaseModel):
    """Who sold the invoice of the case, or who filed it, or who was typed."""

    name: str | None
    code: str | None
    email: str | None
    # Platform user matched by email: they can see and comment on the case.
    user_id: UUID | None
    # ``invoice`` (sales data), ``internal`` (the comercial who filed it) or ``manual``.
    source: Literal["invoice", "internal", "manual"] | None


class CatalogRefResponse(BaseModel):
    """An area, subject or cause as the case shows it."""

    id: UUID
    code: str
    name: str


class CaseClassificationResponse(BaseModel):
    """What the case is about, copied from the request when it opened."""

    # Area the requester picked on the form.
    requested_area: CatalogRefResponse | None
    subject: CatalogRefResponse | None
    cause: CatalogRefResponse | None
    # ``supplier`` (may go to the manufacturer), ``internal`` (a JDH process) or ``none``.
    route: Literal["supplier", "internal", "none"] | None
    pickup_order_requested: Literal["yes", "no", "unknown"] | None
    pickup_order_number: str | None
    # When the order with the products was delivered.
    delivery_date: date | None


class CaseProductResponse(BaseModel):
    """A product line of the case, as the requester sent it."""

    id: UUID
    # ``request`` (public form), ``internal`` or ``email``.
    source: Literal["request", "internal", "email"]
    invoice_number: str
    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    lot: str
    expiry_date: date
    affected_quantity: int


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
    sales_executive: SalesExecutiveResponse | None = None
    requester_name: str | None
    requester_email: str | None
    request_id: UUID | None
    first_read_at: datetime | None
    # An email from the client or a supplier nobody who works the case has opened yet.
    news_at: datetime | None = None
    # How the case ended (set when finishing it) and whether a JDH process failed.
    resolution: Literal["procede", "no_procede"] | None = None
    internal_failure: bool = False
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    # When an improvement opportunity (OM) went to the supplier.
    om_sent_at: datetime | None = None
    closed_at: datetime | None
    legacy: dict | None
    items: list[CaseItemResponse]
    item_count: int
    responded_count: int
    permissions: CasePermissionsResponse
    created_at: datetime
    updated_at: datetime
    classification: CaseClassificationResponse | None = None
    # With products the supplier of the case comes from them and is not edited by hand.
    products: list[CaseProductResponse] = []


class StatusChangeResponse(BaseModel):
    """Status names before and after an event."""

    from_name: str | None = Field(default=None, serialization_alias="from")
    to_name: str | None = Field(default=None, serialization_alias="to")


class CaseEventResponse(BaseModel):
    """Timeline entry, with a ready-to-show sentence."""

    id: UUID
    event_type: str
    actor: PersonResponse | None
    from_status_id: UUID | None
    to_status_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime
    # ``system``, ``user``, ``client``, ``supplier`` or ``external`` (an unknown sender).
    actor_kind: str = "system"
    actor_name: str = "Sistema"
    # One short sentence: «Abrió el caso por primera vez», «Asignó el caso a Juan».
    title: str = ""
    # ``lifecycle``, ``access``, ``assignment``, ``status``, ``communication``, ``documents``.
    category: str = "lifecycle"
    status_change: StatusChangeResponse | None = None
    # Only on ``email_received`` of a case detail: who wrote it (``supplier``, ``customer``
    # or ``external``), whether the caller can send it to the customer now, and when and
    # by whom it was sent.
    sender_role: Literal["supplier", "customer", "external"] | None = None
    forwardable: bool = False
    forwarded_at: datetime | None = None
    forwarded_by: str | None = None
    forwarded_to: str | None = None


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
    with_news: int = 0
    om_sent: int = 0
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
    # The number of the pickup order goes only with ``yes``.
    pickup_order_requested: Literal["yes", "no", "unknown"] | None = None
    pickup_order_number: str | None = Field(default=None, max_length=40)
    delivery_date: date | None = None


class AssignRequest(BaseModel):
    """New responsible; ``null`` leaves the case unassigned."""

    assignee_user_id: UUID | None
    note: str | None = Field(default=None, max_length=2_000)


class StatusChangeRequest(BaseModel):
    """Target status; closing a complaint or a claim also says how it ended."""

    status_id: UUID
    note: str | None = Field(default=None, max_length=2_000)
    # Only when finishing: required for complaints and claims.
    resolution: Literal["procede", "no_procede"] | None = None
    internal_failure: bool = False
    resolution_note: str | None = Field(default=None, max_length=2_000)


class DeleteCaseRequest(BaseModel):
    """Why the case is deleted; it stays in the history."""

    reason: str | None = Field(default=None, max_length=2000)


class CommentRequest(BaseModel):
    """Timeline comment, optionally also emailed to the client (JSON, no files)."""

    body: str = Field(min_length=1, max_length=5_000)
    reply_to_client: bool = False
    cc: list[str] = Field(default_factory=list, max_length=5)


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
    handling: Literal["case", "acknowledge"] | None = None
    # One visible line under the type on the form; ``null`` clears it.
    description: str | None = Field(default=None, max_length=MAX_TYPE_DESCRIPTION)
    allows_attachments: bool | None = None
    asks_area: bool | None = None


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
    allows_direct_upload: bool | None = None


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
        sales_executive=(
            SalesExecutiveResponse(
                name=case.sales_executive_name,
                code=case.sales_executive_code,
                email=case.sales_executive_email,
                user_id=case.sales_executive_user_id,
                source=case.sales_executive_source,
            )
            if case.sales_executive_name or case.sales_executive_code
            else None
        ),
        requester_name=case.requester_name,
        requester_email=case.requester_email,
        request_id=case.request_id,
        first_read_at=case.first_read_at,
        news_at=case.news_at,
        resolution=case.resolution,
        internal_failure=case.internal_failure,
        resolution_note=case.resolution_note,
        resolved_at=case.resolved_at,
        om_sent_at=case.om_sent_at,
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
        classification=_classification(case),
        products=[
            CaseProductResponse.model_validate(product, from_attributes=True)
            for product in case.products
        ],
    )


def _catalog_ref(value: CatalogRef | None) -> CatalogRefResponse | None:
    if value is None:
        return None
    return CatalogRefResponse(id=value.id, code=value.code, name=value.name)


def _classification(case: TrackingCase) -> CaseClassificationResponse:
    return CaseClassificationResponse(
        requested_area=_catalog_ref(case.requested_area),
        subject=_catalog_ref(case.subject_ref),
        cause=_catalog_ref(case.cause_ref),
        route=case.route,
        pickup_order_requested=case.pickup_order_requested,
        pickup_order_number=case.pickup_order_number,
        delivery_date=case.delivery_date,
    )


def _event(value: EventView) -> CaseEventResponse:
    event, summary = value.event, value.summary
    told = {}
    if summary is not None:
        told = {
            "actor_kind": summary.actor_kind,
            "actor_name": summary.actor_name,
            "title": summary.title,
            "category": summary.category,
            "status_change": (
                StatusChangeResponse(from_name=summary.status_from, to_name=summary.status_to)
                if summary.status_from or summary.status_to
                else None
            ),
        }
    if value.forward is not None:
        told |= {
            "sender_role": value.forward.sender_role,
            "forwardable": value.forward.forwardable,
            "forwarded_at": value.forward.forwarded_at,
            "forwarded_by": value.forward.forwarded_by,
            "forwarded_to": value.forward.forwarded_to,
        }
    return CaseEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor=_person(value.actor),
        from_status_id=event.from_status_id,
        to_status_id=event.to_status_id,
        note=event.note,
        data=event.data,
        created_at=event.created_at,
        **told,
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
        with_news=value.with_news,
        om_sent=value.om_sent,
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
    with_news: bool = False,
    resolution: Literal["procede", "no_procede"] | None = None,
    internal_failure: bool | None = None,
    om_sent: bool | None = None,
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
        with_news=with_news,
        resolution=resolution,
        internal_failure=internal_failure,
        om_sent=om_sent,
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


@router.get(
    "/request-types",
    response_model=list[TrackingRequestTypeResponse],
    summary="List every request type with its settings",
)
@inject
async def list_request_types(
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> list[TrackingRequestTypeResponse]:
    """All five types, suggestions and compliments included, to configure them (managers)."""

    return [_request_type(value) for value in await service.list_request_types(principal)]


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
    """Change prefix, follow-up flag, response time, form description or evidence (managers)."""

    return _request_type(
        await service.update_request_type(
            request_type_id,
            principal,
            fields=frozenset(body.model_fields_set),
            prefix=body.prefix,
            requires_follow_up=body.requires_follow_up,
            sla_days=body.sla_days,
            handling=body.handling,
            description=body.description,
            allows_attachments=body.allows_attachments,
            asks_area=body.asks_area,
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


@router.get("/people", response_model=list[PersonResponse], summary="Search people to copy (CC)")
@inject
async def search_people(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> list[PersonResponse]:
    """Find active people of the organization to copy on a reply (anyone who sees PQRSF)."""

    users = await service.search_people(q, principal, limit)
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


@router.get(
    "/cases/suggestions",
    dependencies=[_READ_GATE],
    response_model=list[str],
    summary="Free-text suggestions",
)
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
    """Return the case, its timeline and the public intake record.

    The timeline leaves out who opened the case (``read``, ``viewed``): see
    ``GET /cases/{case_id}/openings``.
    """

    return _detail(await service.get_case(case_id, principal))


class PersonOpeningsResponse(BaseModel):
    """How one person opened the case."""

    user: PersonResponse | None
    user_id: UUID
    first_opened_at: datetime
    last_opened_at: datetime
    # Visits recorded; a repeat within 30 minutes counts once.
    visits: int


class CaseOpeningsResponse(BaseModel):
    """Who opened the case (newest first) and each opening; kept apart from the timeline."""

    people: list[PersonOpeningsResponse]
    entries: list[CaseEventResponse]


@router.get(
    "/cases/{case_id}/openings",
    response_model=CaseOpeningsResponse,
    summary="Who opened the case",
)
@inject
async def case_openings(
    case_id: UUID,
    principal: Principal = _principal,
    service: CaseTrackingService = _tracking,
) -> CaseOpeningsResponse:
    """Who opened the case and when, for anyone who can see it.

    Each person's first and last opening and visit count, then every opening newest first.
    The first opening by the responsible (``read``) carries the move to «Leída».
    """

    value = await service.case_openings(case_id, principal)
    return CaseOpeningsResponse(
        people=[
            PersonOpeningsResponse(
                user=_person(person.user),
                user_id=person.user_id,
                first_opened_at=person.first_opened_at,
                last_opened_at=person.last_opened_at,
                visits=person.visits,
            )
            for person in value.people
        ],
        entries=[_event(entry) for entry in value.entries],
    )


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
    """Move the case to another status (responsible or managers).

    Finishing a complaint or a claim requires ``resolution`` (``procede`` or ``no_procede``)
    and takes ``internal_failure`` (a JDH process failed) and ``resolution_note``.
    """

    return _case(
        await service.change_status(
            case_id,
            body.status_id,
            body.note,
            principal,
            resolution=body.resolution,
            internal_failure=body.internal_failure,
            resolution_note=body.resolution_note,
        )
    )


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


class CaseFileResponse(BaseModel):
    """A file that arrived by email, stored under the case."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    # The timeline comment the file was attached to; null for files that came by email.
    comment_event_id: UUID | None = None


@router.get(
    "/cases/{case_id}/files",
    response_model=list[CaseFileResponse],
    summary="List files received by email",
)
@inject
async def list_case_files(
    case_id: UUID,
    principal: Principal = _principal,
    service: CaseAttachmentService = _case_files,
) -> list[CaseFileResponse]:
    """Files that came with the case's emails, for anyone who can see the case."""

    return [
        CaseFileResponse(
            id=item.id,
            filename=item.original_filename,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            created_at=item.created_at,
            comment_event_id=item.comment_event_id,
        )
        for item in await service.list_attachments(case_id, principal)
    ]


@router.get(
    "/cases/{case_id}/files/{file_id}/download",
    summary="Download a file received by email",
    response_class=Response,
)
@inject
async def download_case_file(
    case_id: UUID,
    file_id: UUID,
    principal: Principal = _principal,
    service: CaseAttachmentService = _case_files,
) -> Response:
    """Return one stored file; it is always sent as a download, never rendered."""

    attachment, content = await service.download(case_id, file_id, principal)
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


class RejectedFileResponse(BaseModel):
    """A file that was not stored, and why."""

    name: str
    reason: str


class ClientReplyResponse(BaseModel):
    """A partial reply emailed to the client and where its delivery stands."""

    id: UUID
    event_id: UUID | None
    to: str
    cc: list[str] = []
    status: Literal["pending", "sent", "failed"]
    attempts: int
    error: str | None
    sent_at: datetime | None
    created_at: datetime | None


class CommentResponse(CaseEventResponse):
    """The new timeline entry, its files and the client reply it queued."""

    files: list[CaseFileResponse] = []
    rejected_files: list[RejectedFileResponse] = []
    client_reply: ClientReplyResponse | None = None


def _client_reply(item: Notification) -> ClientReplyResponse:
    event_id = item.payload.get("event_id")
    return ClientReplyResponse(
        id=item.id,
        event_id=UUID(event_id) if event_id else None,
        to=item.recipient_email,
        cc=[str(address) for address in item.payload.get("cc") or []],
        status=item.status,
        attempts=item.attempts,
        error=item.error,
        sent_at=item.sent_at,
        created_at=item.created_at,
    )


_COMMENT_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["body"],
                "properties": {
                    "body": {"type": "string", "maxLength": 5000},
                    "reply_to_client": {"type": "boolean", "default": False},
                    "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                },
            }
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["body"],
                "properties": {
                    "body": {"type": "string", "maxLength": 5000},
                    "reply_to_client": {"type": "boolean", "default": False},
                    "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                },
            }
        },
    },
}
_MAX_COMMENT_FILES = 20
_TRUE = {"1", "true", "yes", "on"}


async def _read_comment(
    request: Request, max_file_bytes: int
) -> tuple[str, bool, list[UploadedFile], list[str]]:
    """Read a comment sent as JSON (text only) or as a multipart form with files.

    Returns:
        tuple[str, bool, list[UploadedFile], list[str]]: Text, whether it also goes to the
        client, its files and the people to copy on that email.
    """

    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=_MAX_COMMENT_FILES, max_fields=30)
        files = [
            UploadedFile(upload.filename or "archivo", await upload.read(max_file_bytes + 1))
            for upload in form.getlist("files")
            if hasattr(upload, "read")
        ]
        return (
            str(form.get("body") or ""),
            str(form.get("reply_to_client") or "").lower() in _TRUE,
            files,
            [str(value) for value in form.getlist("cc")],
        )
    try:
        payload = CommentRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        raise InvalidInputError(
            "El comentario no es válido: escribe un texto de hasta 5000 caracteres"
        ) from exc
    return payload.body, payload.reply_to_client, [], payload.cc


@router.post(
    "/cases/{case_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on case",
    openapi_extra={"requestBody": _COMMENT_BODY},
)
@inject
async def comment_case(
    case_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: CaseCommentService = _comments,
) -> CommentResponse:
    """Comment on the timeline, optionally with files and as a partial reply to the client.

    Anyone who can see the case may comment. With ``reply_to_client`` the comment (and
    its files) is also emailed to the requester from the PQRSF mailbox; that needs the
    right to work the case, an open case and a requester email, and it never closes the
    case. The email goes through a queue: ``client_reply.status`` says where it stands.
    """

    text, reply, uploads, copied = await _read_comment(request, service.guard.max_attachment_bytes)
    result = await service.comment(case_id, text, principal, uploads, reply, copied)
    return CommentResponse(
        **_event(result.event).model_dump(),
        files=[
            CaseFileResponse(
                id=item.id,
                filename=item.original_filename,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                created_at=item.created_at,
                comment_event_id=item.comment_event_id,
            )
            for item in result.files
        ],
        rejected_files=[RejectedFileResponse(**item) for item in result.rejected],
        client_reply=_client_reply(result.client_reply) if result.client_reply else None,
    )


@router.get(
    "/cases/{case_id}/client-replies",
    response_model=list[ClientReplyResponse],
    summary="List replies sent to the client",
)
@inject
async def list_client_replies(
    case_id: UUID,
    principal: Principal = _principal,
    service: CaseCommentService = _comments,
) -> list[ClientReplyResponse]:
    """The partial replies emailed to the client, with their delivery state."""

    return [_client_reply(item) for item in await service.list_client_replies(case_id, principal)]


@router.post(
    "/cases/{case_id}/client-replies/{notification_id}/retry",
    response_model=ClientReplyResponse,
    summary="Retry a failed reply to the client",
)
@inject
async def retry_client_reply(
    case_id: UUID,
    notification_id: UUID,
    principal: Principal = _principal,
    service: CaseCommentService = _comments,
) -> ClientReplyResponse:
    """Send a reply again after its delivery gave up (the responsible or a manager)."""

    return _client_reply(await service.retry_client_reply(case_id, notification_id, principal))


class ForwardRequest(BaseModel):
    """What the team adds for the customer when passing a supplier's answer on."""

    note: str | None = Field(default=None, max_length=2_000)


class ForwardPreviewResponse(BaseModel):
    """The email the customer would get, without sending it."""

    to: str
    sender: str
    subject: str
    html: str
    text: str
    attachments: list[str]


class ForwardResponse(CaseEventResponse):
    """The ``forwarded_to_customer`` entry and the email it queued."""

    delivery: ClientReplyResponse


_FORWARD_PATH = "/cases/{case_id}/events/{event_id}/forward-to-customer"


@router.post(
    f"{_FORWARD_PATH}/preview",
    response_model=ForwardPreviewResponse,
    summary="Preview a supplier's answer forwarded to the customer",
)
@inject
async def preview_forward_to_customer(
    case_id: UUID,
    event_id: UUID,
    payload: ForwardRequest | None = None,
    principal: Principal = _principal,
    service: CustomerForwardService = _forwards,
) -> ForwardPreviewResponse:
    """The email exactly as the customer would get it (subject, HTML, text, files).

    Sends nothing. Same checks as the send, so a preview that works means the send will.
    """

    preview = await service.preview(case_id, event_id, payload.note if payload else None, principal)
    return ForwardPreviewResponse(
        to=preview.to,
        sender=preview.sender,
        subject=preview.subject,
        html=preview.html,
        text=preview.text,
        attachments=preview.attachments,
    )


@router.post(
    _FORWARD_PATH,
    response_model=ForwardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Forward a supplier's answer to the customer",
)
@inject
async def forward_to_customer(
    case_id: UUID,
    event_id: UUID,
    payload: ForwardRequest | None = None,
    principal: Principal = _principal,
    service: CustomerForwardService = _forwards,
) -> ForwardResponse:
    """Email a supplier's answer (an ``email_received`` event) to the customer, by hand.

    Only the responsible of the case or a manager; only a supplier's answer, once; the case
    must be open and have the requester's email. It leaves from the no-reply mailbox and
    says that answers to it are not read. The timeline gets a ``forwarded_to_customer``
    entry and the original email shows ``forwarded_at`` and ``forwarded_by``.
    """

    result = await service.forward(case_id, event_id, payload.note if payload else None, principal)
    return ForwardResponse(
        **_event(result.event).model_dump(), delivery=_client_reply(result.notification)
    )


class JobScheduleModel(BaseModel):
    """One timer: whether it runs and at what local time (24 hours, HH:MM)."""

    enabled: bool
    time: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["17:30"])


class NotificationSettingsModel(BaseModel):
    """The organization-wide timers."""

    daily_summary: JobScheduleModel
    overdue_reminder: JobScheduleModel


class NotificationSettingsResponse(NotificationSettingsModel):
    """The timers plus the rules the UI should show next to them."""

    timezone: str
    business_days_only: bool = True


def _settings_response(
    settings: NotificationSettings, service: NotificationService
) -> NotificationSettingsResponse:
    stored = settings.to_json()
    return NotificationSettingsResponse(
        daily_summary=JobScheduleModel(**stored["daily_summary"]),
        overdue_reminder=JobScheduleModel(**stored["overdue_reminder"]),
        timezone=service.zone.key,
    )


@router.get(
    "/settings/notifications",
    response_model=NotificationSettingsResponse,
    summary="Get the notification timers",
)
@inject
async def get_notification_settings(
    principal: Principal = _principal,
    service: NotificationService = _notifications,
) -> NotificationSettingsResponse:
    """The organization timers: end-of-day summary and overdue reminder."""

    return _settings_response(await service.get_settings(principal), service)


@router.put(
    "/settings/notifications",
    response_model=NotificationSettingsResponse,
    summary="Change the notification timers",
)
@inject
async def update_notification_settings(
    body: NotificationSettingsModel,
    principal: Principal = _principal,
    service: NotificationService = _notifications,
) -> NotificationSettingsResponse:
    """Replace the timers for the whole organization (whoever works PQRSF)."""

    settings = NotificationSettings(
        daily_summary=JobSchedule(body.daily_summary.enabled, parse_time(body.daily_summary.time)),
        overdue_reminder=JobSchedule(
            body.overdue_reminder.enabled, parse_time(body.overdue_reminder.time)
        ),
    )
    return _settings_response(await service.update_settings(settings, principal), service)


__all__ = ["router"]
