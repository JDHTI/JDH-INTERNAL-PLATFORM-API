"""Notifications endpoints (``/notifications``), with the intranet's payloads.

- The bell and push are for anyone signed in (``router``).
- Strapi's ingestion has no session: a shared secret in ``X-Jdh-Ingest-Secret`` guards
  the whole ``ingest_router``, compared in constant time; empty secret, ingestion off.
"""

from __future__ import annotations

import hmac
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.notifications.application.service import MAX_PAGE_SIZE, NotificationService
from app.modules.notifications.domain.entities import AudienceType, IngestCommand, Source

_service = Depends(Provide[ApplicationContainer.v1_container.notification_service])
_principal = Depends(get_current_principal)


async def require_ingest_secret(
    request: Request,
    secret: str | None = Header(None, alias="X-Jdh-Ingest-Secret"),
) -> None:
    """Let Strapi in with the shared secret; nobody else.

    Raises:
        HTTPException: 503 when the secret is not configured, 401 when it does not match.
    """

    expected = request.app.state.settings.NOTIFICATIONS_INGEST_SECRET.get_secret_value()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "La ingesta está desactivada")
    if not secret or not hmac.compare_digest(secret.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Secreto de ingesta inválido")


router = APIRouter()
ingest_router = APIRouter(dependencies=[Depends(require_ingest_secret)])


# --- Schemas (the intranet's names) ---


class NotificationResponse(BaseModel):
    id: UUID
    titulo: str
    resumen: str | None
    href: str
    fuente: Literal["NOTICIA", "EVENTO", "CERTIFICADO", "PQRSF"]
    leida: bool
    creada_en: datetime


class FeedResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    page: int
    pageSize: int  # noqa: N815 - the intranet's contract
    no_leidas: int


class CountResponse(BaseModel):
    no_leidas: int


class PublicKeyResponse(BaseModel):
    clave: str | None


class SubscriptionRequest(BaseModel):
    endpoint: str = Field(max_length=2000)
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)
    expirationTime: datetime | None = None  # noqa: N815


class AudienceRequest(BaseModel):
    tipo: Literal["TODOS", "SEDE", "AREA", "EMPLEADOS"]
    valores: list[str] = Field(default_factory=list, max_length=200)


class IngestRequest(BaseModel):
    """What Strapi sends on publish (the intranet's ``IngestNotificacionDto``)."""

    fuente: Literal["NOTICIA", "EVENTO", "CERTIFICADO"]
    fuenteId: str = Field(min_length=1, max_length=255)  # noqa: N815
    slug: str | None = Field(None, max_length=255)
    titulo: str = Field(min_length=1, max_length=200)
    resumen: str | None = Field(None, max_length=500)
    idempotencyKey: str = Field(min_length=8, max_length=255)  # noqa: N815
    audiencia: AudienceRequest
    programadaPara: datetime | None = None  # noqa: N815
    recordatorioDiasAntes: int | None = Field(None, ge=1, le=60)  # noqa: N815
    fechaEvento: date | None = None  # noqa: N815
    publicadoPor: str | None = Field(None, max_length=200)  # noqa: N815


class IngestResponse(BaseModel):
    id: UUID
    estado: str
    duplicada: bool
    destinatarios: int | None
    recordatorioId: UUID | None  # noqa: N815


# --- The bell ---


@router.get("", response_model=FeedResponse)
@inject
async def feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_PAGE_SIZE, alias="pageSize"),
    unread_only: bool = Query(False, alias="soloNoLeidas"),
    principal: Principal = _principal,
    service: NotificationService = _service,
) -> dict[str, Any]:
    """Notificaciones de quien llama, de la más reciente a la más antigua."""

    result = await service.feed(principal, page, page_size, unread_only)
    return {
        "items": [
            {
                "id": item.notification.id,
                "titulo": item.notification.title,
                "resumen": item.notification.summary,
                "href": item.notification.href,
                "fuente": item.notification.source.value,
                "leida": item.read,
                "creada_en": item.received_at,
            }
            for item in result.items
        ],
        "total": result.total,
        "page": result.page,
        "pageSize": result.page_size,
        "no_leidas": result.unread,
    }


@router.get("/count", response_model=CountResponse)
@inject
async def count(
    principal: Principal = _principal, service: NotificationService = _service
) -> dict[str, int]:
    """Cuántas notificaciones no ha leído quien llama (el número de la campana)."""

    return {"no_leidas": await service.unread_count(principal)}


@router.patch("/read", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def read_all(
    principal: Principal = _principal, service: NotificationService = _service
) -> Response:
    """Marca todas como leídas."""

    await service.mark_read(principal, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def read_one(
    notification_id: UUID,
    principal: Principal = _principal,
    service: NotificationService = _service,
) -> Response:
    """Marca una como leída (sin efecto si no es de quien llama)."""

    await service.mark_read(principal, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Push ---


@router.get("/push/public-key", response_model=PublicKeyResponse)
@inject
async def public_key(
    _: Principal = _principal, service: NotificationService = _service
) -> dict[str, str | None]:
    """Clave VAPID para suscribir el navegador; ``null`` si push está apagado."""

    return {"clave": service.public_key()}


@router.post("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def subscribe(
    body: SubscriptionRequest,
    user_agent: str | None = Header(None),
    principal: Principal = _principal,
    service: NotificationService = _service,
) -> Response:
    """Registra el navegador de quien llama para recibir push."""

    await service.subscribe(
        principal, body.endpoint, body.p256dh, body.auth, body.expirationTime, user_agent
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def unsubscribe(
    endpoint: str = Query(max_length=2000),
    principal: Principal = _principal,
    service: NotificationService = _service,
) -> Response:
    """Da de baja un navegador de quien llama."""

    await service.unsubscribe(principal, endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Strapi ---


@ingest_router.post("", response_model=IngestResponse)
@inject
async def ingest(body: IngestRequest, service: NotificationService = _service) -> dict[str, Any]:
    """Ingesta desde Strapi al publicar; repetir con la misma clave no notifica dos veces."""

    result = await service.ingest(
        IngestCommand(
            source=Source(body.fuente),
            source_id=body.fuenteId,
            slug=body.slug,
            title=body.titulo,
            summary=body.resumen,
            idempotency_key=body.idempotencyKey,
            audience_type=AudienceType(body.audiencia.tipo),
            audience_values=body.audiencia.valores,
            scheduled_for=body.programadaPara,
            reminder_days_before=body.recordatorioDiasAntes,
            event_date=body.fechaEvento,
            published_by=body.publicadoPor,
        )
    )
    return {
        "id": result.id,
        "estado": result.status.value,
        "duplicada": result.duplicate,
        "destinatarios": result.recipients,
        "recordatorioId": result.reminder_id,
    }


@ingest_router.get("/audiences")
@inject
async def audiences(service: NotificationService = _service) -> dict[str, Any]:
    """Sedes y áreas reales, para que el editor de Strapi elija la audiencia."""

    return await service.audiences()
