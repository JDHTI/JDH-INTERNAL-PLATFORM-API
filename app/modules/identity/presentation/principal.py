"""FastAPI dependency that exposes the session user as a ``Principal``."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.modules.identity.application.directory import principal_from_user
from app.modules.identity.domain.principal import Principal
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import get_current_user


async def get_current_principal(
    user: UserModel = Depends(get_current_user),  # noqa: B008
) -> Principal:
    """Resolve the caller from the SSO cookies (or the dev bypass).

    Args:
        user (UserModel): User resolved by the identity session dependency.

    Returns:
        Principal: The caller as other modules see it.
    """

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return principal_from_user(user)


__all__ = ["get_current_principal"]
