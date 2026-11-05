"""Salespeople endpoints (``/salespeople``): my scope, the roster and the links.

``GET /me`` is for everyone signed in; the UI shows the scope in the quoter. The roster
and a person's links are for administrators and moderators; linking, unlinking and the
e-mail sync are for administrators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.app_container import ApplicationContainer
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.salespeople.application.service import SalesScopeService
from app.modules.salespeople.domain.entities import SalespersonLink

router = APIRouter()
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.sales_scope_service])

Kind = Literal["seller", "supervisor"]


class ScopeResponse(BaseModel):
    """What the caller sees of the customers: ``all``, ``team``, ``mine`` or ``none``."""

    scope: Literal["all", "team", "mine", "none"]
    seller_codes: list[str]
    supervisor_codes: list[str]
    # Seller codes whose customers the caller works on; ``null`` means every customer.
    codes: list[str] | None


class LinkResponse(BaseModel):
    id: UUID
    kind: Kind
    code: str
    source: Literal["auto", "manual", "excluded"]
    created_at: datetime
    updated_at: datetime


class UserRef(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None
    is_active: bool


class UserLinksResponse(BaseModel):
    user: UserRef
    links: list[LinkResponse]


class RosterItemResponse(BaseModel):
    kind: Kind
    code: str
    name: str | None
    email: str | None
    supervisor_code: str | None
    is_active: bool
    # Who holds the code (by hand or by e-mail); null when nobody.
    holder_email: str | None
    holder_name: str | None
    link: LinkResponse | None


class RosterPageResponse(BaseModel):
    items: list[RosterItemResponse]
    total: int
    page: int
    size: int


class LinkRequest(BaseModel):
    email: EmailStr
    kind: Kind
    code: str = Field(min_length=1, max_length=20)


class SyncResponse(BaseModel):
    created: int
    matched: int
    without_user: int


def _link(link: SalespersonLink) -> LinkResponse:
    return LinkResponse(
        id=link.id,
        kind=link.kind,  # type: ignore[arg-type]
        code=link.code,
        source=link.source,  # type: ignore[arg-type]
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


@router.get("/me", response_model=ScopeResponse, summary="The caller's sales scope")
@inject
async def my_scope(
    principal: Principal = _principal, service: SalesScopeService = _service
) -> ScopeResponse:
    """Alcance comercial de quien llama: qué clientes y cotizaciones ve."""

    scope = await service.scope_for(principal)
    return ScopeResponse(
        scope=scope.scope,  # type: ignore[arg-type]
        seller_codes=list(scope.seller_codes),
        supervisor_codes=list(scope.supervisor_codes),
        codes=None if scope.codes is None else list(scope.codes),
    )


@router.get("/roster", response_model=RosterPageResponse, summary="Salespeople or supervisors")
@inject
async def roster(
    kind: str = Query("seller", pattern="^(seller|supervisor)$"),
    q: str | None = Query(None, max_length=100),
    linked: bool | None = None,
    active_only: bool = True,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    principal: Principal = _principal,
    service: SalesScopeService = _service,
) -> RosterPageResponse:
    """Vendedores o supervisores de SIESA con quién tiene cada código (admin o moderador)."""

    result = await service.roster_page(
        principal, kind=kind, q=q, linked=linked, active_only=active_only, page=page, size=size
    )
    return RosterPageResponse(
        items=[
            RosterItemResponse(
                kind=row.entry.kind,  # type: ignore[arg-type]
                code=row.entry.code,
                name=row.entry.name,
                email=row.entry.email,
                supervisor_code=row.entry.supervisor_code,
                is_active=row.entry.is_active,
                holder_email=row.holder_email,
                holder_name=row.holder_name,
                link=_link(row.link) if row.link else None,
            )
            for row in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/links", response_model=UserLinksResponse, summary="A person's codes")
@inject
async def user_links(
    email: str = Query(..., min_length=3, max_length=254),
    principal: Principal = _principal,
    service: SalesScopeService = _service,
) -> UserLinksResponse:
    """Códigos de vendedor y supervisor de una persona (admin o moderador)."""

    user, links = await service.user_links(principal, email)
    return UserLinksResponse(
        user=UserRef(
            id=user.id, email=user.email, display_name=user.display_name, is_active=user.is_active
        ),
        links=[_link(link) for link in links],
    )


@router.post(
    "/links",
    response_model=LinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a code to a person",
)
@inject
async def link(
    payload: LinkRequest, principal: Principal = _principal, service: SalesScopeService = _service
) -> LinkResponse:
    """Vincula un código a una persona; gana sobre el vínculo por correo (admin)."""

    return _link(await service.link(principal, payload.email, payload.kind, payload.code))


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink a code")
@inject
async def unlink(
    link_id: UUID, principal: Principal = _principal, service: SalesScopeService = _service
) -> None:
    """Desvincula el código; el correo no lo vuelve a vincular solo (admin)."""

    await service.unlink(principal, link_id)


@router.post("/sync", response_model=SyncResponse, summary="Link salespeople by e-mail")
@inject
async def sync(
    principal: Principal = _principal, service: SalesScopeService = _service
) -> SyncResponse:
    """Guarda el vínculo de cada vendedor activo cuyo correo es de un usuario activo (admin)."""

    result = await service.sync_by_email(principal)
    return SyncResponse(
        created=result.created, matched=result.matched, without_user=result.without_user
    )
