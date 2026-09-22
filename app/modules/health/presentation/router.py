"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Literal

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.core.container import ApplicationContainer
from app.modules.health.application.service import HealthService

router = APIRouter()


class ComponentResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    latency_ms: float = Field(ge=0)


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, ComponentResponse]


@router.get("/live", tags=["health"], summary="Process liveness")
async def live() -> dict[str, str]:
    """Return 200 while the application process is able to answer."""

    return {"status": "ok"}


@router.get(
    "/ready",
    tags=["health"],
    summary="Dependency readiness",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
@inject
async def ready(
    response: Response,
    health_service: HealthService = Depends(Provide[ApplicationContainer.health_service]),  # noqa: B008
) -> ReadinessResponse:
    """Return dependency status and use HTTP 503 until all are available."""

    is_ready, checks = await health_service.readiness()
    payload = ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        checks={
            name: ComponentResponse(status=result.status, latency_ms=result.latency_ms)
            for name, result in checks.items()
        },
    )
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


__all__ = ["router"]
