"""HTTP endpoints for cookie-based Entra SSO and administration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Annotated
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.core.config import Settings
from app.modules.identity.application.service import (
    AuthenticationRequired,
    IdentityService,
    ProtectedMutationError,
    user_view,
)
from app.modules.identity.infrastructure.entra import EntraConfigurationError, EntraError
from app.modules.identity.infrastructure.models import UserModel

router = APIRouter()
_identity_service_dependency = Depends(Provide[ApplicationContainer.v1_container.identity_service])


class UserResponse(BaseModel):
    """Public local user representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    entra_object_id: str
    name: str
    email: str | None
    job_title: str | None
    role: str
    area_id: UUID | None
    area_name: str | None
    photo_available: bool


class SessionResponse(BaseModel):
    """Result of completing SSO without exposing tokens."""

    user: UserResponse
    is_new_user: bool = False


class RoleResponse(BaseModel):
    """Platform role."""

    id: UUID
    code: str
    name: str


class UserPage(BaseModel):
    """Paginated administrator user response."""

    items: list[UserResponse]
    total: int
    offset: int
    limit: int


class UpdateUserRequest(BaseModel):
    """Editable local user fields."""

    job_title: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    area_id: UUID | None = None


def _cookie_kwargs(settings: Settings, max_age: int) -> dict[str, object]:
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "domain": settings.AUTH_COOKIE_DOMAIN or None,
        "path": "/",
    }


def _set_token_cookies(
    response: Response, settings: Settings, access_token: str, refresh_token: str | None
) -> None:
    response.set_cookie(
        settings.AUTH_ACCESS_COOKIE_NAME,
        access_token,
        **_cookie_kwargs(settings, settings.AUTH_ACCESS_COOKIE_MAX_AGE),
    )
    if refresh_token:
        response.set_cookie(
            settings.AUTH_REFRESH_COOKIE_NAME,
            refresh_token,
            **_cookie_kwargs(settings, settings.AUTH_REFRESH_COOKIE_MAX_AGE),
        )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.AUTH_ACCESS_COOKIE_NAME, path="/", domain=settings.AUTH_COOKIE_DOMAIN or None
    )
    response.delete_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME, path="/", domain=settings.AUTH_COOKIE_DOMAIN or None
    )
    response.delete_cookie(
        settings.AUTH_STATE_COOKIE_NAME, path="/", domain=settings.AUTH_COOKIE_DOMAIN or None
    )


def _no_store(response: Response) -> Response:
    """Prevent browsers and intermediary caches from retaining auth responses."""

    response.headers["Cache-Control"] = "no-store"
    return response


def _user_response(user: UserModel) -> UserResponse:
    return UserResponse.model_validate(user_view(user), from_attributes=True)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@inject
async def get_current_user(
    request: Request,
    response: Response,
    service: IdentityService = _identity_service_dependency,
) -> UserModel:
    """Resolve the user from HttpOnly cookies and rotate expired access tokens."""

    access_token = request.cookies.get(service.settings.AUTH_ACCESS_COOKIE_NAME)
    refresh_token = request.cookies.get(service.settings.AUTH_REFRESH_COOKIE_NAME)
    try:
        authenticated = await service.current_user(access_token, refresh_token)
    except (AuthenticationRequired, EntraError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if authenticated.refreshed_tokens:
        _set_token_cookies(
            response,
            service.settings,
            authenticated.refreshed_tokens.access_token,
            authenticated.refreshed_tokens.refresh_token or refresh_token,
        )
    return authenticated.user


_current_user_dependency = Depends(get_current_user)


async def require_admin(user: UserModel = _current_user_dependency) -> UserModel:
    """Require the local administrator role."""

    if user.role.code != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required"
        )
    return user


@router.get("/auth/login", include_in_schema=True)
@inject
async def login(
    request: Request,
    service: IdentityService = _identity_service_dependency,
) -> Response:
    """Start the Entra authorization-code SSO flow."""

    if service.settings.AUTH_DEV_BYPASS:
        access_token = request.cookies.get(service.settings.AUTH_ACCESS_COOKIE_NAME)
        refresh_token = request.cookies.get(service.settings.AUTH_REFRESH_COOKIE_NAME)
        try:
            user = await service.current_user(access_token, refresh_token)
        except (AuthenticationRequired, EntraError) as exc:
            raise HTTPException(status_code=401, detail="Authentication required") from exc
        return _no_store(
            JSONResponse(SessionResponse(user=_user_response(user.user)).model_dump(mode="json"))
        )
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    try:
        location = service.entra.authorization_url(state, challenge)
    except EntraConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="Entra authentication is not configured"
        ) from exc
    response = RedirectResponse(url=location, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    state_payload = f"{state}.{verifier}"
    response.set_cookie(
        service.settings.AUTH_STATE_COOKIE_NAME,
        state_payload,
        **_cookie_kwargs(service.settings, service.settings.AUTH_STATE_COOKIE_MAX_AGE),
    )
    return response


@router.get("/auth/callback", response_model=SessionResponse)
@inject
async def callback(
    request: Request,
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    service: IdentityService = _identity_service_dependency,
) -> Response:
    """Complete SSO, register the user once, and set HttpOnly token cookies."""

    state_cookie = request.cookies.get(service.settings.AUTH_STATE_COOKIE_NAME)
    if not state_cookie or "." not in state_cookie:
        raise HTTPException(status_code=400, detail="Invalid SSO state")
    expected_state, verifier = state_cookie.split(".", 1)
    if not hmac.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="Invalid SSO state")
    try:
        user, tokens, is_new = await service.complete_code_login(code, verifier)
    except EntraConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="Entra authentication is not configured"
        ) from exc
    except EntraError as exc:
        raise HTTPException(status_code=401, detail="Unable to complete SSO") from exc

    if service.settings.AUTH_FRONTEND_REDIRECT_URI:
        response: Response = RedirectResponse(
            url=service.settings.AUTH_FRONTEND_REDIRECT_URI,
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response = JSONResponse(
            SessionResponse(user=_user_response(user), is_new_user=is_new).model_dump(mode="json")
        )
    _set_token_cookies(response, service.settings, tokens.access_token, tokens.refresh_token)
    response.delete_cookie(
        service.settings.AUTH_STATE_COOKIE_NAME,
        path="/",
        domain=service.settings.AUTH_COOKIE_DOMAIN or None,
    )
    return _no_store(response)


@router.post("/auth/refresh", response_model=SessionResponse)
@inject
async def refresh(
    request: Request,
    service: IdentityService = _identity_service_dependency,
) -> Response:
    """Rotate an access cookie using the refresh cookie."""

    refresh_token = request.cookies.get(service.settings.AUTH_REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh session required")
    try:
        authenticated = await service.current_user(None, refresh_token)
    except (AuthenticationRequired, EntraError) as exc:
        raise HTTPException(status_code=401, detail="Refresh session expired") from exc
    response = JSONResponse(
        SessionResponse(user=_user_response(authenticated.user)).model_dump(mode="json")
    )
    if authenticated.refreshed_tokens:
        _set_token_cookies(
            response,
            service.settings,
            authenticated.refreshed_tokens.access_token,
            authenticated.refreshed_tokens.refresh_token or refresh_token,
        )
    return _no_store(response)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    """Clear the browser's local SSO cookies."""

    settings = request.app.state.settings
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_auth_cookies(response, settings)
    return _no_store(response)


@router.get("/users/me", response_model=UserResponse)
@router.get("/auth/me", response_model=UserResponse)
async def me(user: UserModel = _current_user_dependency) -> UserResponse:
    """Return the authenticated local profile."""

    return _user_response(user)


@router.get("/users", response_model=UserPage)
@inject
async def list_users(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: IdentityService = _identity_service_dependency,
) -> UserPage:
    """List local users for administrators."""

    users, total = await service.list_users(offset, limit)
    return UserPage(
        items=[_user_response(user) for user in users], total=total, offset=offset, limit=limit
    )


@router.get("/users/{user_id}", response_model=UserResponse)
@inject
async def get_user(
    user_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: IdentityService = _identity_service_dependency,
) -> UserResponse:
    """Get one local user for administrators."""

    user = await service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
@inject
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: IdentityService = _identity_service_dependency,
) -> UserResponse:
    """Change local cargo, role, or area."""

    fields = body.model_fields_set
    try:
        user = await service.update_user(
            user_id,
            job_title=body.job_title,
            role_code=body.role,
            area_id=body.area_id,
            update_job_title="job_title" in fields,
            update_role="role" in fields,
            update_area="area_id" in fields,
        )
    except ProtectedMutationError as exc:
        raise HTTPException(
            status_code=409, detail="At least one administrator must remain"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(user)


@router.get("/roles", response_model=list[RoleResponse])
@inject
async def list_roles(
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: IdentityService = _identity_service_dependency,
) -> list[RoleResponse]:
    """List platform roles."""

    return [
        RoleResponse.model_validate(role, from_attributes=True)
        for role in await service.list_roles()
    ]


# Areas are served by the areas module under /v1/areas (by slug), which also
# owns their members, role catalog and document space.


@router.get("/auth/me/photo")
@inject
async def profile_photo(
    user: UserModel = _current_user_dependency,
    service: IdentityService = _identity_service_dependency,
) -> Response:
    """Return the private profile photo for the authenticated user."""

    body = await service.photo(user)
    if body is None:
        raise HTTPException(status_code=404, detail="Profile photo not available")
    return Response(
        content=body,
        media_type=getattr(user, "photo_content_type", None) or "image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/auth/me/photo/refresh", response_model=UserResponse)
@inject
async def refresh_profile_photo(
    request: Request,
    user: UserModel = _current_user_dependency,
    service: IdentityService = _identity_service_dependency,
) -> UserResponse:
    """Retry downloading the authenticated user's optional Graph photo."""

    access_token = request.cookies.get(service.settings.AUTH_ACCESS_COOKIE_NAME)
    if not access_token:
        raise HTTPException(status_code=401, detail="Access session required")
    try:
        updated = await service.refresh_photo(user, access_token)
    except EntraError as exc:
        raise HTTPException(status_code=401, detail="Unable to refresh profile photo") from exc
    return _user_response(updated)
