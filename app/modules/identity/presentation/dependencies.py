"""FastAPI dependency that resolves the authenticated principal."""

from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Header

from app.app_container import ApplicationContainer
from app.modules.identity.application.service import IdentityService
from app.modules.identity.domain.entities import Principal


@inject
async def get_current_principal(
    authorization: str | None = Header(default=None),
    x_dev_user_email: str | None = Header(default=None),
    service: IdentityService = Depends(  # noqa: B008
        Provide[ApplicationContainer.v1_container.identity_service]
    ),
) -> Principal:
    """Authenticate the request from its bearer token (or the dev bypass)."""

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip() or None
    return await service.authenticate(token, dev_email=x_dev_user_email)


__all__ = ["get_current_principal"]
