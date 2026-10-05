"""FastAPI dependency that exposes the session user as a ``Principal``."""

from __future__ import annotations

from fastapi import Depends, HTTPException, WebSocket, status

from app.modules.identity.application.directory import principal_from_user
from app.modules.identity.application.service import AuthenticationRequired, IdentityService
from app.modules.identity.domain.principal import Principal
from app.modules.identity.infrastructure.entra import EntraError
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


def websocket_origin_allowed(websocket: WebSocket, allowed_origins: list[str]) -> bool:
    """Whether the handshake comes from a page the API trusts.

    Browsers attach the session cookies to cross-site WebSocket handshakes and
    CORS does not apply to them, so the ``Origin`` header is the only guard.

    Args:
        websocket (WebSocket): The pending connection.
        allowed_origins (list[str]): Configured CORS origins; ``*`` allows any.

    Returns:
        bool: ``True`` when the origin is allowed or no browser sent one.
    """

    origin = websocket.headers.get("origin")
    return origin is None or "*" in allowed_origins or origin.rstrip("/") in allowed_origins


async def websocket_principal(websocket: WebSocket, service: IdentityService) -> Principal | None:
    """Resolve the caller of a WebSocket handshake from the SSO cookies.

    An expired access token is accepted when the refresh token renews it, but
    the renewed cookies cannot be set on a WebSocket: the next HTTP call will.

    Args:
        websocket (WebSocket): The pending connection.
        service (IdentityService): Identity use cases.

    Returns:
        Principal | None: The caller, or ``None`` when not authenticated.
    """

    access_token = websocket.cookies.get(service.settings.AUTH_ACCESS_COOKIE_NAME)
    refresh_token = websocket.cookies.get(service.settings.AUTH_REFRESH_COOKIE_NAME)
    try:
        authenticated = await service.current_user(access_token, refresh_token)
    except (AuthenticationRequired, EntraError):
        return None
    if not getattr(authenticated.user, "is_active", True):
        return None
    return principal_from_user(authenticated.user)


__all__ = ["get_current_principal", "websocket_origin_allowed", "websocket_principal"]
