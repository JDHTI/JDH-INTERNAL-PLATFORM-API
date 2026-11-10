"""Endpoints of the TI area's application monitoring and the general board.

``/app-monitoring`` registers the platforms and reads their checks (Monitoreo →
Aplicativos); ``/monitoring/board`` puts them next to the data warehouse.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.app_monitoring.application.board import MonitoringBoardService
from app.modules.app_monitoring.application.service import MAX_PAGE_SIZE, AppMonitoringService
from app.modules.app_monitoring.domain.entities import Application, CheckResult, Target
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.dwh_monitoring.presentation.router import CargaResponse, _carga
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(dependencies=[Depends(require_permission("monitoreo"))])
board_router = APIRouter(dependencies=[Depends(require_permission("monitoreo"))])
_service = Depends(Provide[ApplicationContainer.v1_container.app_monitoring_service])
_board = Depends(Provide[ApplicationContainer.v1_container.monitoring_board_service])
_principal = Depends(get_current_principal)

Estado = Literal["up", "degraded", "down"]
Objetivo = Literal["api", "ui"]

# Request field -> entity attribute.
_FIELDS = {
    "nombre": "name",
    "slug": "slug",
    "descripcion": "description",
    "url_base": "base_url",
    "ruta_health": "health_path",
    "ruta_ui": "ui_path",
    "verificar_ui": "check_ui",
    "habilitado": "enabled",
    "verificar_tls": "verify_tls",
    "timeout_s": "timeout_s",
    "umbral_lento_ms": "slow_ms",
    "orden": "sort_order",
}


# --- Request models ---


class AplicativoCambios(BaseModel):
    """Fields to change; only those sent are touched."""

    nombre: str | None = Field(None, max_length=120)
    slug: str | None = Field(None, max_length=60)
    descripcion: str | None = Field(None, max_length=2000)
    url_base: str | None = Field(None, max_length=500)
    ruta_health: str | None = Field(None, max_length=300)
    ruta_ui: str | None = Field(None, max_length=300)
    verificar_ui: bool | None = None
    habilitado: bool | None = None
    verificar_tls: bool | None = None
    timeout_s: float | None = Field(None, ge=1, le=60)
    umbral_lento_ms: int | None = Field(None, ge=100, le=60000)
    orden: int | None = None

    def as_changes(self) -> dict[str, Any]:
        """Sent fields by entity attribute."""

        return {_FIELDS[name]: value for name, value in self.model_dump(exclude_unset=True).items()}


class AplicativoNuevo(AplicativoCambios):
    """A platform to monitor: ``url_base`` is the server, e.g. ``https://intranet.jdh.com.co``."""

    nombre: str = Field(max_length=120)
    url_base: str = Field(max_length=500)
    ruta_health: str = Field("/api/health", max_length=300)
    ruta_ui: str = Field("/", max_length=300)


# --- Response models ---


class ChequeoResponse(BaseModel):
    """One request against the health endpoint or the UI."""

    objetivo: Objetivo
    chequeado_en: datetime
    estado: Estado
    codigo_http: int | None
    latencia_ms: int | None
    error: str | None
    detalle: dict[str, Any] | None


class ObjetivoResponse(BaseModel):
    """The newest check of a target and its last 24 hours."""

    url: str
    ultimo: ChequeoResponse | None
    disponibilidad_24h: float | None
    chequeos_24h: int
    latencia_media_24h_ms: float | None


class AplicativoResponse(BaseModel):
    """A monitored platform with its status (``null`` when disabled or never checked)."""

    id: UUID
    slug: str
    nombre: str
    descripcion: str | None
    url_base: str
    ruta_health: str
    ruta_ui: str
    verificar_ui: bool
    habilitado: bool
    verificar_tls: bool
    timeout_s: float
    umbral_lento_ms: int
    orden: int
    creado_en: datetime | None
    actualizado_en: datetime | None
    estado: Estado | None
    api: ObjetivoResponse | None
    ui: ObjetivoResponse | None


class ChequeoAutomaticoResponse(BaseModel):
    """Background checks: on or off and their interval."""

    habilitado: bool
    intervalo_s: int


class AplicativosOverviewResponse(BaseModel):
    """Counts by status (enabled applications only) and every application."""

    total: int
    habilitados: int
    arriba: int
    degradados: int
    caidos: int
    sin_datos: int
    estado: Estado | None
    ultimo_chequeo: datetime | None
    chequeo_automatico: ChequeoAutomaticoResponse
    aplicativos: list[AplicativoResponse]


class DatawarehouseBoardResponse(BaseModel):
    """The data warehouse in the general board."""

    estado: Estado | None
    pct_fresca_programadas: float | None
    atrasadas: int | None
    nunca_extraidas: int | None
    secuencias_activas: int
    secuencias_en_curso: int
    secuencias_fallidas: int
    ultima_carga: CargaResponse | None
    ultima_carga_exitosa: datetime | None


class BoardResponse(BaseModel):
    """General board: the worst status of everything, the applications and the DWH."""

    generado_en: datetime
    estado_general: Estado | None
    aplicativos: AplicativosOverviewResponse
    datawarehouse: DatawarehouseBoardResponse


class ItemsResponse[T](BaseModel):
    """A plain list."""

    items: list[T]


class PageResponse[T](BaseModel):
    """A page of a listing."""

    items: list[T]
    total: int
    page: int
    size: int


class CheckRequestedResponse(BaseModel):
    """The background checks were asked to run now."""

    encolado: bool


def _chequeo(result: CheckResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "objetivo": result.target.value,
        "chequeado_en": result.checked_at,
        "estado": result.status.value,
        "codigo_http": result.http_status,
        "latencia_ms": result.latency_ms,
        "error": result.error,
        "detalle": result.detail,
    }


def _objetivo(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if value is None else {**value, "ultimo": _chequeo(value["ultimo"])}


def _aplicativo(view: dict[str, Any]) -> dict[str, Any]:
    application: Application = view["aplicativo"]
    return {
        "id": application.id,
        **{name: getattr(application, attribute) for name, attribute in _FIELDS.items()},
        "creado_en": application.created_at,
        "actualizado_en": application.updated_at,
        "estado": view["estado"].value if view["estado"] else None,
        Target.API.value: _objetivo(view[Target.API.value]),
        Target.UI.value: _objetivo(view[Target.UI.value]),
    }


def _overview(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "estado": value["estado"].value if value["estado"] else None,
        "aplicativos": [_aplicativo(item) for item in value["aplicativos"]],
    }


# --- Endpoints ---


@router.get("/overview", response_model=AplicativosOverviewResponse)
@inject
async def overview(
    principal: Principal = _principal, service: AppMonitoringService = _service
) -> dict[str, Any]:
    """Cuántos aplicativos están arriba, degradados o caídos, con cada uno."""

    return _overview(await service.overview(principal))


@router.get("/applications", response_model=ItemsResponse[AplicativoResponse])
@inject
async def list_applications(
    principal: Principal = _principal, service: AppMonitoringService = _service
) -> dict[str, Any]:
    """Aplicativos registrados con el último chequeo de su API y su UI."""

    return {"items": [_aplicativo(item) for item in await service.applications(principal)]}


@router.post(
    "/applications", response_model=AplicativoResponse, status_code=status.HTTP_201_CREATED
)
@inject
async def create_application(
    body: AplicativoNuevo,
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> dict[str, Any]:
    """Registra un aplicativo; el slug sale del nombre si no se envía. 409 si el slug existe."""

    return _aplicativo(await service.create(principal, body.as_changes()))


@router.get("/applications/{application_id}", response_model=AplicativoResponse)
@inject
async def get_application(
    application_id: UUID,
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> dict[str, Any]:
    """Un aplicativo con su estado."""

    return _aplicativo(await service.application(application_id, principal))


@router.patch("/applications/{application_id}", response_model=AplicativoResponse)
@inject
async def update_application(
    application_id: UUID,
    body: AplicativoCambios,
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> dict[str, Any]:
    """Cambia solo los campos enviados (p. ej. ``habilitado`` o ``verificar_tls``)."""

    return _aplicativo(await service.update(application_id, principal, body.as_changes()))


@router.delete("/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_application(
    application_id: UUID,
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> Response:
    """Elimina un aplicativo y su historial de chequeos."""

    await service.delete(application_id, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/applications/{application_id}/check", response_model=AplicativoResponse)
@inject
async def check_application(
    application_id: UUID,
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> dict[str, Any]:
    """Chequea el aplicativo ahora (aunque esté deshabilitado) y devuelve el resultado."""

    return _aplicativo(await service.check_now(application_id, principal))


@router.get("/applications/{application_id}/checks", response_model=PageResponse[ChequeoResponse])
@inject
async def list_checks(
    application_id: UUID,
    objetivo: Objetivo | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: AppMonitoringService = _service,
) -> dict[str, Any]:
    """Historial de chequeos, del más reciente al más antiguo (hoy por defecto)."""

    target = Target(objetivo) if objetivo else None
    items, total = await service.history(
        application_id, principal, target, desde, hasta, page, size
    )
    return {"items": [_chequeo(item) for item in items], "total": total, "page": page, "size": size}


@router.post("/check", response_model=CheckRequestedResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_check(
    principal: Principal = _principal, service: AppMonitoringService = _service
) -> dict[str, bool]:
    """Pide chequear todos los aplicativos ya; 409 si los chequeos automáticos están apagados."""

    await service.request_check(principal)
    return {"encolado": True}


@board_router.get("/board", response_model=BoardResponse)
@inject
async def board(
    principal: Principal = _principal, service: MonitoringBoardService = _board
) -> dict[str, Any]:
    """Tablero general de TI: aplicativos y datawarehouse, con el peor estado de todos."""

    result = await service.board(principal)
    dwh = result["datawarehouse"]
    return {
        **result,
        "estado_general": result["estado_general"].value if result["estado_general"] else None,
        "aplicativos": _overview(result["aplicativos"]),
        "datawarehouse": {
            **dwh,
            "estado": dwh["estado"].value if dwh["estado"] else None,
            "ultima_carga": _carga(dwh["ultima_carga"]),
        },
    }
