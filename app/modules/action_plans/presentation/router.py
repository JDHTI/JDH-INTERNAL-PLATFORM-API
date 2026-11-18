"""Action plans (P.04.F.02): the matrix, each action, its rows, evidence and indicators.

The routes have no module gate on purpose: a responsible from another area (a process
owner) reaches the actions where they are named without the module. The service decides:
``read`` / ``manage`` on ``planes-accion`` see and work every action; a responsible sees
theirs and marks their own rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.action_plans.application.service import (
    ActionPlanService,
    ActivityInput,
    CauseInput,
    CorrectionInput,
    PlanChanges,
    PlanDetail,
    UploadedFile,
)
from app.modules.action_plans.domain.entities import (
    ACTION_TYPES,
    CAUSE_CATEGORIES,
    EFFECTIVENESS,
    METHODS,
    ORIGINS,
    RATINGS,
    STATUSES,
    ActionPlan,
    PlanFile,
    Process,
)
from app.modules.action_plans.domain.ports import PlanFilters
from app.modules.action_plans.infrastructure.workbook import write_matrix
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(prefix="/action-plans")
_LOCAL = ZoneInfo("America/Bogota")
_service = Depends(Provide[ApplicationContainer.v1_container.action_plan_service])
_principal = Depends(get_current_principal)

Origin = Literal["AI", "AE", "AT", "IN", "OM", "PI", "SNC"]
ActionType = Literal["correctiva", "preventiva", "mejora"]
Rating = Literal["real", "potencial", "oportunidad_mejora"]
Method = Literal["5m", "espina_pescado", "lluvia_ideas", "sin_fallo"]
Category = Literal[
    "mano_obra", "metodos", "maquinaria", "materiales", "medio_ambiente", "medicion", "otra"
]
Effective = Literal["si", "no", "pendiente"]
Status = Literal["abierta", "en_proceso", "cerrada"]


# --- Responses ---


class OptionResponse(BaseModel):
    """A value of a closed list and how it reads."""

    value: str
    label: str


class ProcessResponse(BaseModel):
    """A process of the SIG (the «procedencia»)."""

    id: UUID
    code: str
    name: str
    is_active: bool


class CatalogResponse(BaseModel):
    """The lists of the form."""

    origins: list[OptionResponse]
    action_types: list[OptionResponse]
    ratings: list[OptionResponse]
    methods: list[OptionResponse]
    cause_categories: list[OptionResponse]
    effectiveness: list[OptionResponse]
    statuses: list[OptionResponse]
    processes: list[ProcessResponse]


class PermissionsResponse(BaseModel):
    """What the caller may do in the module."""

    can_read: bool
    can_manage: bool


class PersonResponse(BaseModel):
    """A responsible user with their name (``None`` when the user no longer exists)."""

    id: UUID
    display_name: str | None


class CorrectionResponse(BaseModel):
    """An immediate correction."""

    id: UUID
    description: str
    responsible_text: str | None
    responsible_user_ids: list[UUID]
    responsible_users: list[PersonResponse]
    due_on: date | None
    evidence_note: str | None
    done: bool
    done_at: datetime | None


class CauseResponse(BaseModel):
    """A cause."""

    id: UUID
    description: str
    category: str


class ActivityResponse(BaseModel):
    """A planned activity with its follow-up and effectiveness."""

    id: UUID
    description: str
    responsible_text: str | None
    responsible_user_ids: list[UUID]
    responsible_users: list[PersonResponse]
    due_on: date | None
    evidence_note: str | None
    follow_up_text: str | None
    follow_up_due_on: date | None
    done: bool
    done_at: datetime | None
    effective: str
    effectiveness_note: str | None
    overdue: bool


class PlanSummaryResponse(BaseModel):
    """A row of the matrix list."""

    id: UUID
    code: str
    number: int
    attempt: int
    origin: str
    process: ProcessResponse
    action_type: str
    rating: str
    reported_on: date
    reporter_name: str | None
    reporter_company_role: str | None
    city: str | None
    finding: str | None
    status: str
    effectiveness: str
    case_id: UUID | None
    case_code: str | None
    activities: int
    activities_done: int
    overdue: int


class PlanResponse(PlanSummaryResponse):
    """An action with everything in it."""

    reporter_nit: str | None
    registered_by_user_id: UUID | None
    registered_by_name: str | None
    registered_by_position: str | None
    registered_on: date | None
    unmet_requirement: str | None
    has_photos: bool
    has_history: bool
    analysis_method: str | None
    effectiveness_note: str | None
    cause_label: str | None
    previous_attempt_id: UUID | None
    imported: bool
    import_issues: list[str]
    corrections: list[CorrectionResponse]
    causes: list[CauseResponse]
    activities_detail: list[ActivityResponse]
    created_at: datetime | None
    updated_at: datetime | None


class FileResponse(BaseModel):
    """An evidence file."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    activity_id: UUID | None
    correction_id: UUID | None
    uploaded_by: UUID | None
    created_at: datetime | None


class EventResponse(BaseModel):
    """A step in the history of an action."""

    id: UUID
    event_type: str
    actor_user_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime


class AccessResponse(BaseModel):
    """What the caller may do with this action."""

    can_manage: bool
    is_responsible: bool


class PlanDetailResponse(BaseModel):
    """An action, its evidence, history, access and earlier actions of the same customer."""

    plan: PlanResponse
    access: AccessResponse
    files: list[FileResponse]
    events: list[EventResponse]
    similar: int


class PlanPageResponse(BaseModel):
    """A page of the matrix."""

    items: list[PlanSummaryResponse]
    total: int
    page: int
    size: int


class IndicatorsResponse(BaseModel):
    """The «Indicador» sheet."""

    actions: int
    activities: int
    activities_done: int
    overdue_activities: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_effectiveness: dict[str, int]
    activities_effective: dict[str, int]
    by_origin: dict[str, int]
    by_process: dict[str, int]
    by_month: dict[str, int]
    by_city: dict[str, int]


class UploadResponse(BaseModel):
    """The evidence stored and the files refused."""

    files: list[FileResponse]
    rejected: list[dict]


# --- Requests ---


class PlanRequest(BaseModel):
    """Header of an action (create: the first four are required; update: what is sent)."""

    origin: Origin | None = None
    process_id: UUID | None = None
    action_type: ActionType | None = None
    reported_on: date | None = None
    rating: Rating | None = None
    reporter_nit: str | None = Field(default=None, max_length=30)
    reporter_name: str | None = Field(default=None, max_length=200)
    reporter_company_role: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=80)
    registered_on: date | None = None
    unmet_requirement: str | None = Field(default=None, max_length=5_000)
    finding: str | None = Field(default=None, max_length=5_000)
    has_photos: bool | None = None
    has_history: bool | None = None
    analysis_method: Method | None = None
    effectiveness_note: str | None = Field(default=None, max_length=5_000)


class CorrectionRequest(BaseModel):
    """An immediate correction; ``id`` keeps an existing row."""

    id: UUID | None = None
    description: str = Field(min_length=1, max_length=2_000)
    responsible_text: str | None = Field(default=None, max_length=1_000)
    responsible_user_ids: list[UUID] = Field(default_factory=list, max_length=10)
    due_on: date | None = None
    evidence_note: str | None = Field(default=None, max_length=1_000)


class CauseRequest(BaseModel):
    """A cause; ``id`` keeps an existing row."""

    id: UUID | None = None
    description: str = Field(min_length=1, max_length=2_000)
    category: Category = "otra"


class ActivityRequest(BaseModel):
    """A planned activity; ``id`` keeps an existing row and its progress."""

    id: UUID | None = None
    description: str = Field(min_length=1, max_length=2_000)
    responsible_text: str | None = Field(default=None, max_length=1_000)
    responsible_user_ids: list[UUID] = Field(default_factory=list, max_length=10)
    due_on: date | None = None
    evidence_note: str | None = Field(default=None, max_length=1_000)
    follow_up_text: str | None = Field(default=None, max_length=1_000)
    follow_up_due_on: date | None = None


class DoneRequest(BaseModel):
    """Mark a row as done (or not)."""

    done: bool
    evidence_note: str | None = Field(default=None, max_length=1_000)


class RateRequest(BaseModel):
    """How effective a done activity was."""

    effective: Effective
    note: str | None = Field(default=None, max_length=2_000)


class ProcessRequest(BaseModel):
    """A process: the code only on creation."""

    code: str | None = Field(default=None, min_length=2, max_length=8)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


# --- Mapping ---


def _options(values: dict[str, str]) -> list[OptionResponse]:
    return [OptionResponse(value=k, label=v) for k, v in values.items()]


def _process(value: Process) -> ProcessResponse:
    return ProcessResponse(id=value.id, code=value.code, name=value.name, is_active=value.is_active)


def _summary_fields(plan: ActionPlan, today: date) -> dict:
    return {
        "id": plan.id,
        "code": plan.code,
        "number": plan.number,
        "attempt": plan.attempt,
        "origin": plan.origin,
        "process": _process(plan.process),
        "action_type": plan.action_type,
        "rating": plan.rating,
        "reported_on": plan.reported_on,
        "reporter_name": plan.reporter_name,
        "reporter_company_role": plan.reporter_company_role,
        "city": plan.city,
        "finding": plan.finding,
        "status": plan.status,
        "effectiveness": plan.effectiveness,
        "case_id": plan.case_id,
        "case_code": plan.case_code,
        "activities": len(plan.activities),
        "activities_done": sum(1 for a in plan.activities if a.done),
        "overdue": sum(1 for a in plan.activities if a.is_overdue(today)),
    }


def _row(model: type[BaseModel], row, people: dict[UUID, str], **extra) -> BaseModel:
    """A correction or activity with its responsibles named."""

    named = {"responsible_users", *extra}
    return model(
        **{k: getattr(row, k) for k in model.model_fields if k not in named},
        responsible_users=[
            PersonResponse(id=uid, display_name=people.get(uid)) for uid in row.responsible_user_ids
        ],
        **extra,
    )


def _plan(plan: ActionPlan, today: date, people: dict[UUID, str]) -> PlanResponse:
    legacy = plan.legacy or {}
    return PlanResponse(
        **_summary_fields(plan, today),
        reporter_nit=plan.reporter_nit,
        registered_by_user_id=plan.registered_by_user_id,
        registered_by_name=plan.registered_by_name,
        registered_by_position=plan.registered_by_position,
        registered_on=plan.registered_on,
        unmet_requirement=plan.unmet_requirement,
        has_photos=plan.has_photos,
        has_history=plan.has_history,
        analysis_method=plan.analysis_method,
        effectiveness_note=plan.effectiveness_note,
        cause_label=plan.cause_label,
        previous_attempt_id=plan.previous_attempt_id,
        imported=bool(legacy.get("source")),
        import_issues=list(legacy.get("issues") or []),
        corrections=[_row(CorrectionResponse, c, people) for c in plan.corrections],
        causes=[CauseResponse.model_validate(c, from_attributes=True) for c in plan.causes],
        activities_detail=[
            _row(ActivityResponse, a, people, overdue=a.is_overdue(today)) for a in plan.activities
        ],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _file(value: PlanFile) -> FileResponse:
    return FileResponse(
        id=value.id,
        filename=value.original_filename,
        content_type=value.content_type,
        size_bytes=value.size_bytes,
        activity_id=value.activity_id,
        correction_id=value.correction_id,
        uploaded_by=value.uploaded_by,
        created_at=value.created_at,
    )


def _detail(value: PlanDetail, today: date) -> PlanDetailResponse:
    return PlanDetailResponse(
        plan=_plan(value.plan, today, value.people),
        access=AccessResponse(
            can_manage=value.access.can_manage, is_responsible=value.access.is_responsible
        ),
        files=[_file(f) for f in value.files],
        events=[EventResponse.model_validate(e, from_attributes=True) for e in value.events],
        similar=value.similar,
    )


def _filters(
    origin: Origin | None = None,
    process_id: UUID | None = None,
    action_type: ActionType | None = None,
    status: Status | None = None,
    effectiveness: Effective | None = None,
    city: str | None = Query(default=None, max_length=80),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    case_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    mine: bool = False,
    overdue: bool = False,
    principal: Principal = _principal,
) -> PlanFilters:
    return PlanFilters(
        origin=origin,
        process_id=process_id,
        action_type=action_type,
        status=status,
        effectiveness=effectiveness,
        city=city.strip() if city and city.strip() else None,
        year=year,
        month=month,
        case_id=case_id,
        query=q.strip() if q and q.strip() else None,
        visible_to=principal.user_id if mine else None,
        overdue_on=datetime.now(_LOCAL).date() if overdue else None,
    )


_plan_filters = Depends(_filters)


def _changes(payload: PlanRequest) -> PlanChanges:
    fields = frozenset(payload.model_fields_set)
    return PlanChanges(fields, {name: getattr(payload, name) for name in fields})


# --- Routes ---


@router.get("/permissions", response_model=PermissionsResponse, summary="Module permissions")
@inject
async def permissions(
    principal: Principal = _principal, service: ActionPlanService = _service
) -> PermissionsResponse:
    """What the caller may do: ``false`` (never 403) without the module."""

    can_read, can_manage = await service.permissions(principal)
    return PermissionsResponse(can_read=can_read, can_manage=can_manage)


@router.get("/catalog", response_model=CatalogResponse, summary="Lists of the form")
@inject
async def catalog(
    include_inactive: bool = False,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> CatalogResponse:
    """Origins, types, ratings, methods, cause categories, statuses and processes."""

    return CatalogResponse(
        origins=_options(ORIGINS),
        action_types=_options(ACTION_TYPES),
        ratings=_options(RATINGS),
        methods=_options(METHODS),
        cause_categories=_options(CAUSE_CATEGORIES),
        effectiveness=_options(EFFECTIVENESS),
        statuses=_options(STATUSES),
        processes=[_process(p) for p in await service.processes(include_inactive)],
    )


@router.post(
    "/processes",
    response_model=ProcessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a process",
)
@inject
async def create_process(
    payload: ProcessRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> ProcessResponse:
    """A new process of the SIG (Calidad)."""

    return _process(
        await service.save_process(None, payload.code, payload.name, payload.is_active, principal)
    )


@router.patch("/processes/{process_id}", response_model=ProcessResponse, summary="Edit a process")
@inject
async def update_process(
    process_id: UUID,
    payload: ProcessRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> ProcessResponse:
    """Rename or deactivate a process (Calidad); its code does not change."""

    return _process(
        await service.save_process(process_id, None, payload.name, payload.is_active, principal)
    )


@router.get("", response_model=PlanPageResponse, summary="List actions")
@inject
async def list_plans(
    filters: PlanFilters = _plan_filters,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=100),
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanPageResponse:
    """The matrix, newest first; a responsible without the module sees only their actions."""

    result = await service.list_plans(filters, page, size, principal)
    today = service.today()
    return PlanPageResponse(
        items=[PlanSummaryResponse(**_summary_fields(p, today)) for p in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/indicators", response_model=IndicatorsResponse, summary="Indicators")
@inject
async def indicators(
    filters: PlanFilters = _plan_filters,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> IndicatorsResponse:
    """By type, status, effectiveness, origin, process, month and city; overdue activities."""

    value = await service.indicators(filters, principal)
    return IndicatorsResponse.model_validate(value, from_attributes=True)


@router.get("/export", summary="Export the matrix (P.04.F.02)")
@inject
async def export(
    filters: PlanFilters = _plan_filters,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> Response:
    """The matrix in the layout of the P.04.F.02 Excel, for audits."""

    content = write_matrix(await service.export_plans(filters, principal))
    filename = f"P.04.F.02 Seguimiento plan de acción {service.today():%Y-%m-%d}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an action",
)
@inject
async def create_plan(
    payload: PlanRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """A new action (Calidad): ``origin``, ``process_id`` and ``action_type`` are required."""

    plan = await service.create_plan(_changes(payload), principal)
    return _detail(await service.get_plan(plan.id, principal), service.today())


@router.post(
    "/from-case/{case_id}",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a corrective action from a PQRSF case",
)
@inject
async def create_from_case(
    case_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """A corrective action already filled from the case (Calidad); the case records it."""

    plan = await service.create_from_case(case_id, principal)
    return _detail(await service.get_plan(plan.id, principal), service.today())


@router.get("/{plan_id}", response_model=PlanDetailResponse, summary="Get an action")
@inject
async def get_plan(
    plan_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """The action with its rows, evidence, history and earlier actions of the customer."""

    return _detail(await service.get_plan(plan_id, principal), service.today())


@router.patch("/{plan_id}", response_model=PlanDetailResponse, summary="Edit an action")
@inject
async def update_plan(
    plan_id: UUID,
    payload: PlanRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """Change the fields sent (Calidad); ``null`` clears an optional one."""

    await service.update_plan(plan_id, _changes(payload), principal)
    return _detail(await service.get_plan(plan_id, principal), service.today())


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an action")
@inject
async def delete_plan(
    plan_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> None:
    """Delete an action and its evidence (Calidad)."""

    await service.delete_plan(plan_id, principal)


async def _rows_response(service: ActionPlanService, plan_id: UUID, principal: Principal):
    return _detail(await service.get_plan(plan_id, principal), service.today())


@router.put(
    "/{plan_id}/corrections", response_model=PlanDetailResponse, summary="Set the corrections"
)
@inject
async def put_corrections(
    plan_id: UUID,
    payload: list[CorrectionRequest],
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """The immediate corrections, as the form leaves them (Calidad)."""

    await service.replace_corrections(
        plan_id,
        [
            CorrectionInput(
                **item.model_dump(exclude={"responsible_user_ids"}),
                responsible_user_ids=tuple(item.responsible_user_ids),
            )
            for item in payload
        ],
        principal,
    )
    return await _rows_response(service, plan_id, principal)


@router.put("/{plan_id}/causes", response_model=PlanDetailResponse, summary="Set the causes")
@inject
async def put_causes(
    plan_id: UUID,
    payload: list[CauseRequest],
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """The causes found with the analysis method (Calidad)."""

    await service.replace_causes(
        plan_id, [CauseInput(**item.model_dump()) for item in payload], principal
    )
    return await _rows_response(service, plan_id, principal)


@router.put(
    "/{plan_id}/activities", response_model=PlanDetailResponse, summary="Set the activities"
)
@inject
async def put_activities(
    plan_id: UUID,
    payload: list[ActivityRequest],
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """The plan of activities (Calidad); progress and rating are kept by ``id``."""

    await service.replace_activities(
        plan_id,
        [
            ActivityInput(
                **item.model_dump(exclude={"responsible_user_ids"}),
                responsible_user_ids=tuple(item.responsible_user_ids),
            )
            for item in payload
        ],
        principal,
    )
    return await _rows_response(service, plan_id, principal)


@router.post(
    "/{plan_id}/rows/{row_id}/done", response_model=PlanDetailResponse, summary="Mark a row done"
)
@inject
async def mark_done(
    plan_id: UUID,
    row_id: UUID,
    payload: DoneRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """A correction or an activity done (or not): its responsible or Calidad."""

    await service.mark_done(plan_id, row_id, payload.done, payload.evidence_note, principal)
    return await _rows_response(service, plan_id, principal)


@router.post(
    "/{plan_id}/activities/{activity_id}/rating",
    response_model=PlanDetailResponse,
    summary="Rate how effective an activity was",
)
@inject
async def rate_activity(
    plan_id: UUID,
    activity_id: UUID,
    payload: RateRequest,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """``si``, ``no`` or ``pendiente`` with a description (Calidad); only done activities."""

    await service.rate_activity(plan_id, activity_id, payload.effective, payload.note, principal)
    return await _rows_response(service, plan_id, principal)


@router.post(
    "/{plan_id}/next-attempt",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new attempt",
)
@inject
async def next_attempt(
    plan_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> PlanDetailResponse:
    """A new attempt of a not effective action (Calidad): same number, attempt + 1."""

    plan = await service.next_attempt(plan_id, principal)
    return _detail(await service.get_plan(plan.id, principal), service.today())


@router.post(
    "/{plan_id}/files",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload evidence",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["files"],
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                            "row_id": {"type": "string", "format": "uuid"},
                        },
                    }
                }
            }
        }
    },
)
@inject
async def upload_files(
    plan_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> UploadResponse:
    """Evidence for the action or one of its rows (``row_id``): Calidad or a responsible."""

    form = await request.form(max_files=10, max_fields=5)
    limit = service.guard.max_attachment_bytes
    uploads = [
        UploadedFile(upload.filename or "archivo", await upload.read(limit + 1))
        for upload in form.getlist("files")
        if hasattr(upload, "read")
    ]
    row = str(form.get("row_id") or "").strip()
    try:
        row_id = UUID(row) if row else None
    except ValueError as exc:
        raise InvalidInputError("La fila indicada no es válida") from exc
    stored, rejected = await service.add_files(plan_id, uploads, principal, row_id)
    return UploadResponse(files=[_file(f) for f in stored], rejected=rejected)


@router.get("/{plan_id}/files/{file_id}/download", summary="Download evidence")
@inject
async def download_file(
    plan_id: UUID,
    file_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> Response:
    """An evidence file, for whoever sees the action."""

    found, content = await service.file_content(plan_id, file_id, principal)
    return Response(
        content=content,
        media_type=found.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(found.original_filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/{plan_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove evidence"
)
@inject
async def delete_file(
    plan_id: UUID,
    file_id: UUID,
    principal: Principal = _principal,
    service: ActionPlanService = _service,
) -> None:
    """Remove an evidence file (Calidad, or who uploaded it)."""

    await service.delete_file(plan_id, file_id, principal)


__all__ = ["router"]
