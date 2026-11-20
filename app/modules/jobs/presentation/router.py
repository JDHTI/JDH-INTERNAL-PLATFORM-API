"""Background jobs (``/jobs``): what runs, when the warehouse will feed it, and history.

Administrators and moderators read; only administrators run a job by hand.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin, require_admin_or_moderator
from app.modules.jobs.application.service import MAX_HISTORY, JobService
from app.modules.jobs.domain.entities import JobRun, SequenceState

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.job_service])
_reader = Depends(require_admin_or_moderator)
_admin = Depends(require_admin)


class JobRunResponse(BaseModel):
    id: UUID
    job: str
    trigger: Literal["dwh", "scheduled", "manual", "startup"]
    status: Literal["queued", "running", "success", "partial", "failed"]
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    detail: dict[str, Any]
    error: str | None
    dwh_corrida_id: UUID | None
    dwh_datos_listos: datetime | None
    triggered_by: str | None


class SequenceStateResponse(BaseModel):
    """The warehouse sequence as last polled."""

    secuencia: str
    ultima_estado: str | None
    ultima_datos_listos: datetime | None
    en_curso: bool | None
    proxima_ejecucion: datetime | None
    eta_datos_listos_proxima: datetime | None
    eta_datos_listos_en_curso: datetime | None
    dispatched_datos_listos: datetime | None
    polled_at: datetime | None


class JobResponse(BaseModel):
    key: str
    label: str
    description: str
    sequence: str | None
    # ISO weekdays (1 = Monday) when the daily fallback runs it (or, with ``at``, it runs).
    fallback_days: list[int]
    # Local time (HH:MM) it runs every day, for a job with a fixed time; null otherwise.
    at: str | None = None
    last_run: JobRunResponse | None
    last_success_at: datetime | None
    # When the warehouse should have this job's data ready next.
    next_expected_at: datetime | None
    sequence_state: SequenceStateResponse | None


class DwhPollResponse(BaseModel):
    last_poll_at: datetime | None
    error: str | None


class JobsResponse(BaseModel):
    enabled: bool
    poll_seconds: int
    fallback_at: str
    timezone: str
    dwh: DwhPollResponse
    items: list[JobResponse]


class RunRequested(BaseModel):
    run_id: UUID


def _run(run: JobRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "job": run.job,
        "trigger": run.trigger.value,
        "status": run.status.value,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "detail": run.detail,
        "error": run.error,
        "dwh_corrida_id": run.dwh_corrida_id,
        "dwh_datos_listos": run.dwh_datos_listos,
        "triggered_by": run.triggered_by,
    }


def _state(state: SequenceState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {name: getattr(state, name) for name in SequenceStateResponse.model_fields}


@router.get("", response_model=JobsResponse, summary="Background jobs and their state")
@inject
async def list_jobs(_: UserModel = _reader, service: JobService = _service) -> dict[str, Any]:
    """Tareas en segundo plano, su última ejecución y cuándo el DWH tendrá sus datos listos."""

    settings, poll, items = await service.overview()
    return {
        "enabled": settings.enabled,
        "poll_seconds": settings.poll_seconds,
        "fallback_at": settings.fallback_at.strftime("%H:%M"),
        "timezone": settings.timezone,
        "dwh": {"last_poll_at": poll.last_poll_at, "error": poll.error},
        "items": [
            {
                "key": item.key,
                "label": item.label,
                "description": item.description,
                "sequence": item.sequence,
                "fallback_days": item.fallback_days,
                "at": item.at,
                "last_run": _run(item.last_run),
                "last_success_at": item.last_success_at,
                "next_expected_at": item.next_expected_at,
                "sequence_state": _state(item.sequence_state),
            }
            for item in items
        ],
    }


@router.get("/runs", response_model=list[JobRunResponse], summary="Job run history")
@inject
async def list_runs(
    job: str | None = Query(None, max_length=60),
    limit: int = Query(50, ge=1, le=MAX_HISTORY),
    _: UserModel = _reader,
    service: JobService = _service,
) -> list[dict[str, Any]]:
    """Ejecuciones más recientes primero, de una tarea o de todas."""

    return [_run(run) for run in await service.history(job, limit)]


@router.post(
    "/{key}/run",
    response_model=RunRequested,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a job now",
)
@inject
async def run_job(key: str, admin: UserModel = _admin, service: JobService = _service) -> dict:
    """Pone la tarea en cola ya; 404 si no existe, 409 si están apagadas o ya hay una abierta."""

    run = await service.request_run(key, admin.email or admin.name)
    return {"run_id": run.id}


__all__ = ["router"]
