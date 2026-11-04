"""Endpoints of the TI area's data warehouse monitoring (Monitoreo → Datawarehouse).

Field names are the ones of the ``monitoreo`` contract in the DWH vault, except
that intervals come in whole seconds with an ``_s`` suffix. Dates are ISO 8601 with
offset; ``time`` fields are ``HH:MM:SS`` in America/Bogota.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.dwh_monitoring.application.service import MAX_PAGE_SIZE, DwhMonitoringService
from app.modules.dwh_monitoring.domain.entities import LoadCycle
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(dependencies=[Depends(require_permission("datawarehouse"))])
_service = Depends(Provide[ApplicationContainer.v1_container.dwh_monitoring_service])
_principal = Depends(get_current_principal)

EstadoFrescura = Literal["Fresca", "Atrasada", "Nunca extraída", "Deshabilitada"]
Programacion = Literal["Secuencia", "Sin programación"]
EstadoCorrida = Literal["running", "success", "failed", "abandonada"]
EstadoRun = Literal["running", "success", "failed", "orphaned"]
Disparo = Literal["scheduled", "manual", "backfill"]
_ESTADOS = Query(default_factory=list)
_Page = Query(1, ge=1)
_Size = Query(50, ge=1, le=MAX_PAGE_SIZE)


# --- Response models ---


class SecuenciaResponse(BaseModel):
    """A sequence card (``monitoreo.secuencias``)."""

    secuencia: str
    descripcion: str | None
    estado_programacion: str | None
    calendario: str | None
    hora_inicio: time | None
    tope_s: int | None
    tablas_fase1: int | None
    tablas_fase2: int | None
    transformacion: str | None
    ultima_corrida_id: UUID | None
    ultima_estado: str | None
    ultima_inicio: datetime | None
    ultima_datos_listos: datetime | None
    ultima_fin: datetime | None
    ultima_duracion_s: int | None
    en_curso: bool | None
    eta_datos_listos_en_curso: datetime | None
    eta_fin_en_curso: datetime | None
    proxima_ejecucion: datetime | None
    eta_datos_listos_proxima: datetime | None
    eta_fin_proxima: datetime | None
    hora_tipica_datos_listos: time | None
    extraccion_mediana_min: float | None
    transformacion_mediana_min: float | None
    datos_listos_mediana_min: float | None
    datos_listos_p90_min: float | None
    duracion_mediana_min: float | None
    pct_exito: float | None
    corridas_medidas: int | None
    systemd_proximo: datetime | None
    systemd_ultimo: datetime | None
    sincronizado_en: datetime | None
    cargado_en: datetime


class SecuenciaTablaResponse(BaseModel):
    """A table a sequence extracts (``monitoreo.secuencia_tablas``)."""

    secuencia: str
    fase: int
    carril: int | None
    orden: int | None
    dominio: str | None
    alcance: str | None
    tabla: str | None
    pipeline_name: str
    estrategia: str | None
    habilitada: bool | None
    ultima_extraccion_ok: datetime | None
    ultimo_estado: str | None
    ultimo_inicio: datetime | None
    ultimo_fin: datetime | None
    ultimo_filas: int | None
    ultimo_error: str | None


class HorarioTipicoResponse(BaseModel):
    """Typical timeline of one schedule (``monitoreo.horario_tipico``)."""

    secuencia: str
    calendario: str
    dias_semana: list[int] | None
    hora_programada: time | None
    hora_inicio_real: time | None
    hora_fin_extraccion: time | None
    hora_datos_listos: time | None
    hora_datos_listos_p90: time | None
    hora_fin: time | None
    corridas_medidas: int | None


class EstadisticaResponse(BaseModel):
    """What feeds the ETAs (``monitoreo.estadisticas_secuencia``), in seconds."""

    secuencia: str
    corridas_medidas: int | None
    extraccion_mediana_s: float | None
    transformacion_mediana_s: float | None
    datos_listos_mediana_s: float | None
    datos_listos_p90_s: float | None
    duracion_mediana_s: float | None
    duracion_p90_s: float | None
    retraso_inicio_mediana_s: float | None
    extraccion_fin_desde_programada_s: float | None
    datos_listos_desde_programada_s: float | None
    datos_listos_p90_desde_programada_s: float | None
    fin_desde_programada_s: float | None
    pct_exito: float | None


class SecuenciaDetalleResponse(SecuenciaResponse):
    """A sequence with what it extracts, its typical day and its statistics."""

    tablas: list[SecuenciaTablaResponse]
    horario: list[HorarioTipicoResponse]
    estadisticas: EstadisticaResponse | None


class FrescuraResumenResponse(BaseModel):
    """The freshness KPI (``monitoreo.frescura_resumen``)."""

    calculado_en: datetime
    tablas_habilitadas: int | None
    tablas_deshabilitadas: int | None
    tablas_programadas: int | None
    frescas_programadas: int | None
    pct_fresca_programadas: float | None
    frescas_total: int | None
    pct_fresca_total: float | None
    atrasadas: int | None
    nunca_extraidas: int | None
    ultima_extraccion_ok: datetime | None


class FrescuraDominioResponse(BaseModel):
    """Freshness of one domain (``monitoreo.frescura_por_dominio``)."""

    dominio: str
    tablas: int | None
    frescas: int | None
    atrasadas: int | None
    nunca_extraidas: int | None
    programadas: int | None
    pct_fresca: float | None
    ultima_extraccion_ok: datetime | None


class FrescuraResponse(BaseModel):
    """KPI and breakdown by domain."""

    resumen: FrescuraResumenResponse | None
    dominios: list[FrescuraDominioResponse]


class FrescuraTablaResponse(BaseModel):
    """Freshness of one table (``monitoreo.frescura_tablas``)."""

    dominio: str | None
    tabla: str | None
    pipeline_name: str
    estrategia: str | None
    programacion: str | None
    secuencias: str | None
    ultima_extraccion_ok: datetime | None
    edad_s: int | None
    edad_horas: float | None
    max_staleness_s: int | None
    ultima_pasada_secuencia: datetime | None
    estado_frescura: str | None
    motivo_deshabilitada: str | None


class CorridaResponse(BaseModel):
    """A sequence run (``monitoreo.corridas_secuencia``)."""

    corrida_id: UUID
    secuencia: str
    origen: str | None
    estado: str | None
    programada: datetime | None
    fuera_de_horario: bool | None
    inicio: datetime
    extraccion_fin: datetime | None
    transformacion_inicio: datetime | None
    transformacion_fin: datetime | None
    datos_listos: datetime | None
    fase2_inicio: datetime | None
    fase2_fin: datetime | None
    fin: datetime | None
    extraccion_s: int | None
    transformacion_s: int | None
    hasta_datos_listos_s: int | None
    duracion_s: int | None
    retraso_inicio_s: int | None
    extraccion_fin_desde_programada_s: int | None
    datos_listos_desde_programada_s: int | None
    fin_desde_programada_s: int | None
    extracciones: int | None
    extracciones_fallidas: int | None
    filas_extraidas: int | None
    transformaciones_fallidas: int | None
    codigo_salida: int | None
    os_hostname: str | None
    os_pid: int | None


class ExtraccionResponse(BaseModel):
    """One extraction (``monitoreo.auditoria_extracciones``)."""

    run_id: UUID
    inicio: datetime
    fin: datetime | None
    duracion_s: int | None
    estado: str | None
    disparo: str | None
    secuencia: str | None
    corrida_id: UUID | None
    dominio: str | None
    tabla: str | None
    pipeline_name: str | None
    estrategia: str | None
    filas_extraidas: int | None
    filas_cargadas: int | None
    filas_origen: int | None
    filas_esperadas: int | None
    conciliacion: str | None
    paginas: int | None
    ventana_desde: Any
    ventana_hasta: Any
    batch_id: UUID | None
    error: str | None
    cancelada_por: str | None
    os_hostname: str | None
    os_pid: int | None


class TransformacionResponse(BaseModel):
    """One dbt run (``monitoreo.auditoria_transformaciones``)."""

    run_id: UUID
    inicio: datetime
    fin: datetime | None
    duracion_s: int | None
    estado: str | None
    disparo: str | None
    secuencia: str | None
    corrida_id: UUID | None
    selector: str | None
    version_dbt: str | None
    modelos_ok: int | None
    modelos_error: int | None
    modelos_omitidos: int | None
    tests_ok: int | None
    tests_fallidos: int | None
    tests_aviso: int | None
    tests_fallidos_nombres: str | None
    error: str | None
    os_hostname: str | None
    os_pid: int | None


class CalidadResponse(BaseModel):
    """Latest result of one test or model (``monitoreo.calidad_ultimo_resultado``)."""

    tipo: str
    nombre: str
    objeto: str | None
    capa: str | None
    estado: str | None
    severidad: str | None
    filas_fallidas: int | None
    ejecutado_en: datetime | None
    duracion_ms: int | None
    selector: str | None
    run_id: UUID | None


class VistaCargaResponse(BaseModel):
    """What one view did in a load cycle."""

    leidas: int
    escritas: int
    error: str | None


class CargaResponse(BaseModel):
    """One load cycle from the warehouse."""

    id: UUID
    iniciado_en: datetime
    terminado_en: datetime | None
    estado: Literal["success", "partial", "failed"] | None
    error: str | None
    detalle: dict[str, VistaCargaResponse]


class EstadoCargaResponse(BaseModel):
    """Background load: on or off, its interval and the last cycles."""

    habilitada: bool
    intervalo_s: int
    ultima: CargaResponse | None
    ultima_exitosa: datetime | None


class DatosListosResponse(BaseModel):
    """When the data of an active sequence was (or will be) ready today."""

    secuencia: str
    ultima_estado: str | None
    datos_listos: datetime | None
    eta_datos_listos: datetime | None
    hora_tipica_datos_listos: time | None
    corridas_medidas: int | None


class OverviewResponse(BaseModel):
    """Header of the module."""

    zona_horaria: str
    frescura: FrescuraResumenResponse | None
    secuencias_activas: int
    secuencias_en_curso: int
    datos_listos_hoy: list[DatosListosResponse]
    carga: EstadoCargaResponse


class HorarioResponse(BaseModel):
    """Typical day against the runs of one day (Gantt), with each sequence's ETAs."""

    fecha: date
    zona_horaria: str
    tipico: list[HorarioTipicoResponse]
    corridas: list[CorridaResponse]
    secuencias: list[SecuenciaResponse]


class ItemsResponse[T](BaseModel):
    """A plain list."""

    items: list[T]


class PageResponse[T](BaseModel):
    """A page of a listing."""

    items: list[T]
    total: int
    page: int
    size: int


class SyncRequestedResponse(BaseModel):
    """The background load was asked to run now."""

    encolada: bool


def _carga(cycle: LoadCycle | None) -> dict[str, Any] | None:
    if cycle is None:
        return None
    return {
        "id": cycle.id,
        "iniciado_en": cycle.started_at,
        "terminado_en": cycle.finished_at,
        "estado": cycle.status.value if cycle.status else None,
        "error": cycle.error,
        "detalle": {name: report.as_dict() for name, report in cycle.views.items()},
    }


def _estado_carga(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "ultima": _carga(value["ultima"])}


# --- Endpoints ---


@router.get("/overview", response_model=OverviewResponse)
@inject
async def overview(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """KPI de frescura, secuencias activas y en curso, y datos listos de hoy."""

    result = await service.overview(principal)
    return {**result, "carga": _estado_carga(result["carga"])}


@router.get("/sequences", response_model=ItemsResponse[SecuenciaResponse])
@inject
async def list_sequences(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """Secuencias con estado, horario, última corrida, en curso, próxima ejecución y ETA."""

    return {"items": await service.sequences(principal)}


@router.get("/sequences/{secuencia}", response_model=SecuenciaDetalleResponse)
@inject
async def get_sequence(
    secuencia: str, principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """Una secuencia con sus tablas, su horario típico y sus estadísticas."""

    return await service.sequence(secuencia, principal)


@router.get("/sequence-tables", response_model=ItemsResponse[SecuenciaTablaResponse])
@inject
async def list_sequence_tables(
    secuencia: str | None = None,
    dominio: str | None = None,
    fase: int | None = Query(None, ge=1, le=2),
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Qué tablas extrae cada secuencia, con el último resultado de cada una."""

    return {"items": await service.sequence_tables(principal, secuencia, dominio, fase)}


@router.get("/statistics", response_model=ItemsResponse[EstadisticaResponse])
@inject
async def list_statistics(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """Medianas y p90 por secuencia que alimentan las ETA."""

    return {"items": await service.statistics(principal)}


@router.get("/freshness", response_model=FrescuraResponse)
@inject
async def freshness(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """KPI de data fresca (``pct_fresca_programadas``) y desglose por dominio."""

    return await service.freshness(principal)


@router.get("/freshness/tables", response_model=ItemsResponse[FrescuraTablaResponse])
@inject
async def freshness_tables(
    estado: list[EstadoFrescura] = _ESTADOS,
    dominio: str | None = None,
    programacion: Programacion | None = None,
    q: str | None = Query(None, max_length=200),
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Frescura y última modificación por tabla; atrasadas con ``estado`` repetido."""

    items = await service.freshness_tables(principal, estado, dominio, programacion, q)
    return {"items": items}


@router.get("/freshness/history", response_model=ItemsResponse[FrescuraResumenResponse])
@inject
async def freshness_history(
    desde: date | None = None,
    hasta: date | None = None,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """El KPI de frescura en el tiempo, un punto por carga (últimos 7 días por defecto)."""

    return {"items": await service.freshness_history(principal, desde, hasta)}


@router.get("/schedule", response_model=HorarioResponse)
@inject
async def schedule(
    fecha: date | None = None,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Gantt del día típico contra las corridas de un día (hoy por defecto) y las ETA."""

    return await service.schedule(principal, fecha)


@router.get("/sequence-runs", response_model=PageResponse[CorridaResponse])
@inject
async def list_runs(
    secuencia: str | None = None,
    estado: list[EstadoCorrida] = _ESTADOS,
    desde: date | None = None,
    hasta: date | None = None,
    page: int = _Page,
    size: int = _Size,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Historial de corridas de secuencia, de la más reciente a la más antigua."""

    items, total = await service.runs(principal, secuencia, estado, desde, hasta, page, size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/extractions", response_model=PageResponse[ExtraccionResponse])
@inject
async def list_extractions(
    desde: date | None = None,
    hasta: date | None = None,
    secuencia: str | None = None,
    dominio: str | None = None,
    estado: list[EstadoRun] = _ESTADOS,
    disparo: Disparo | None = None,
    q: str | None = Query(None, max_length=200),
    page: int = _Page,
    size: int = _Size,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Auditoría de extracciones por fecha, secuencia, dominio y estado (7 días por defecto)."""

    items, total = await service.extractions(
        principal, desde, hasta, secuencia, dominio, estado, disparo, q, page, size
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/transformations", response_model=PageResponse[TransformacionResponse])
@inject
async def list_transformations(
    desde: date | None = None,
    hasta: date | None = None,
    secuencia: str | None = None,
    estado: list[EstadoRun] = _ESTADOS,
    q: str | None = Query(None, max_length=200),
    page: int = _Page,
    size: int = _Size,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Auditoría de transformaciones dbt con sus modelos y tests (7 días por defecto)."""

    items, total = await service.transformations(
        principal, desde, hasta, secuencia, estado, q, page, size
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/quality", response_model=ItemsResponse[CalidadResponse])
@inject
async def list_quality(
    estado: list[str] = _ESTADOS,
    tipo: str | None = None,
    principal: Principal = _principal,
    service: DwhMonitoringService = _service,
) -> dict[str, Any]:
    """Último resultado de cada test y modelo: qué está fallando hoy."""

    return {"items": await service.quality(principal, estado, tipo)}


@router.get("/sync", response_model=EstadoCargaResponse)
@inject
async def sync_status(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, Any]:
    """Estado de la carga desde el datawarehouse y su último ciclo."""

    return _estado_carga(await service.sync_status(principal))


@router.post("/sync", response_model=SyncRequestedResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_sync(
    principal: Principal = _principal, service: DwhMonitoringService = _service
) -> dict[str, bool]:
    """Pide una carga inmediata; 409 si la carga automática está apagada."""

    await service.request_sync(principal)
    return {"encolada": True}
