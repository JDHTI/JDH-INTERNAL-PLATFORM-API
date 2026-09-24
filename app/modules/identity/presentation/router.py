"""Endpoints about the authenticated user."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.modules.identity.domain.entities import Principal
from app.modules.identity.presentation.dependencies import get_current_principal

router = APIRouter()


class MeResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    is_platform_admin: bool


@router.get("", response_model=MeResponse, summary="Authenticated user")
async def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:  # noqa: B008
    return MeResponse(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        is_platform_admin=principal.is_platform_admin,
    )


__all__ = ["router"]
