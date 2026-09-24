"""HTTP endpoints for read-only organizational consumption data."""

from __future__ import annotations

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.consumption.application.cursor import InvalidCursor
from app.modules.consumption.application.service import ConsumptionService
from app.modules.consumption.domain.entities import Client, ClientPage
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import get_current_user

router = APIRouter()
_consumption_service_dependency = Depends(
    Provide[ApplicationContainer.v1_container.consumption_service]
)


class ClientResponse(BaseModel):
    """Public fields returned from the clients consumption view."""

    nit: str | None
    razon_social: str | None
    sucursal: str | None
    nombre_establecimiento: str | None
    zona_logistica: str | None
    departamento: str | None
    ciudad: str | None
    nombre_vendedor: str | None
    telefono: str | None
    celular: str | None
    direccion: str | None
    correo: str | None
    unidad_negocio: str | None
    canal: str | None


class ClientPageResponse(BaseModel):
    """Cursor-paginated response for the clients view."""

    items: list[ClientResponse]
    next_cursor: str | None = None
    has_more: bool = Field(description="Whether another page is available")


def _client_response(client: Client) -> ClientResponse:
    return ClientResponse.model_validate(client, from_attributes=True)


@router.get(
    "/clientes",
    response_model=ClientPageResponse,
    summary="List organization clients",
    responses={
        401: {"description": "Authentication required"},
        422: {"description": "Invalid pagination cursor"},
        503: {"description": "Data provider unavailable"},
    },
)
@inject
async def list_clients(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    _: UserModel = Depends(get_current_user),  # noqa: B008
    service: ConsumptionService = _consumption_service_dependency,
) -> ClientPageResponse:
    """Return one optimized cursor-paginated page from ``consumo.clientes``."""

    try:
        page: ClientPage = await service.list_clients(limit=limit, cursor=cursor)
    except InvalidCursor as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid pagination cursor",
        ) from exc
    except DataProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data provider unavailable",
        ) from exc
    return ClientPageResponse(
        items=[_client_response(client) for client in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


__all__ = ["router"]
