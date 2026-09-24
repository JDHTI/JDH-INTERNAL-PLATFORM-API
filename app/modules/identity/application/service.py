"""Use cases for browser SSO and local identity management."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.config import Settings
from app.infrastructure.s3 import S3Storage
from app.modules.identity.domain.entities import EntraProfile, IdentityToken, UserView
from app.modules.identity.infrastructure.entra import (
    EntraClient,
    EntraTokenError,
    EntraTokenExpired,
    TokenSet,
)
from app.modules.identity.infrastructure.models import RoleModel, UserModel
from app.modules.identity.infrastructure.repository import IdentityRepository


class AuthenticationRequired(Exception):
    """Raised when a request has no valid authenticated identity."""


class AuthorizationRequired(Exception):
    """Raised when a user lacks the administrator role."""


class ProtectedMutationError(Exception):
    """Raised when an operation would remove the last administrator."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Current user plus tokens that should be rotated in cookies."""

    user: UserModel
    refreshed_tokens: TokenSet | None = None


class IdentityService:
    """Coordinate Entra, local persistence, and private profile photo storage."""

    def __init__(
        self,
        repository: IdentityRepository,
        entra: EntraClient,
        storage: S3Storage,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.entra = entra
        self.storage = storage
        self.settings = settings

    @property
    def tenant_id(self) -> str:
        """Return the configured tenant or a stable development tenant."""

        return self.settings.ENTRA_TENANT_ID or "development"

    async def complete_code_login(
        self, code: str, code_verifier: str
    ) -> tuple[UserModel, TokenSet, bool]:
        """Exchange the callback code and register or load the local user."""

        tokens = await self.entra.exchange_code(code, code_verifier)
        identity = await self.entra.validate_access_token(tokens.access_token)
        profile = await self.entra.profile(identity)
        user, is_new = await self.register_profile(profile)
        return user, tokens, is_new

    async def register_profile(self, profile: EntraProfile) -> tuple[UserModel, bool]:
        """Create a default user once and attach an optional profile photo."""

        existing = await self.repository.get_user_by_identity(profile.tenant_id, profile.object_id)
        if existing is not None:
            return existing, False
        user = await self.repository.create_user(
            tenant_id=profile.tenant_id,
            object_id=profile.object_id,
            name=profile.name,
            email=profile.email,
            job_title=profile.job_title,
            role_code="user",
        )
        if profile.photo:
            photo_key = f"profile-photos/{profile.tenant_id}/{profile.object_id}"
            try:
                await self.storage.put_object(
                    photo_key, profile.photo, content_type=profile.photo_content_type
                )
            except Exception:
                # A missing photo is not a failed identity registration.
                pass
            else:
                updated = await self.repository.update_photo_key(
                    user.id, photo_key, profile.photo_content_type
                )
                if updated is not None:
                    user = updated
        return user, True

    async def current_user(
        self, access_token: str | None, refresh_token: str | None
    ) -> AuthenticatedUser:
        """Validate cookies, rotate an expired access token, and load the local user."""

        if self.settings.AUTH_DEV_BYPASS and not access_token and not refresh_token:
            return AuthenticatedUser(await self._development_user())
        if not access_token and not refresh_token:
            raise AuthenticationRequired

        refreshed: TokenSet | None = None
        identity: IdentityToken
        if access_token:
            try:
                identity = await self.entra.validate_access_token(access_token)
            except EntraTokenExpired:
                if not refresh_token:
                    raise AuthenticationRequired from None
                refreshed = await self.entra.refresh(refresh_token)
                identity = await self.entra.validate_access_token(refreshed.access_token)
            except EntraTokenError:
                # An invalid token must never silently activate the development bypass.
                raise AuthenticationRequired from None
        else:
            if not refresh_token:
                raise AuthenticationRequired
            refreshed = await self.entra.refresh(refresh_token)
            identity = await self.entra.validate_access_token(refreshed.access_token)

        user = await self.repository.get_user_by_identity(identity.tenant_id, identity.object_id)
        if user is None:
            profile = await self.entra.profile(identity)
            user, _ = await self.register_profile(profile)
        return AuthenticatedUser(user=user, refreshed_tokens=refreshed)

    async def _development_user(self) -> UserModel:
        """Create the deterministic development administrator when first requested."""

        profile = EntraProfile(
            tenant_id=self.tenant_id,
            object_id=self.settings.AUTH_DEV_USER_OBJECT_ID,
            name=self.settings.AUTH_DEV_USER_NAME,
            email=self.settings.AUTH_DEV_USER_EMAIL,
        )
        existing = await self.repository.get_user_by_identity(profile.tenant_id, profile.object_id)
        if existing is not None:
            return existing
        user = await self.repository.create_user(
            tenant_id=profile.tenant_id,
            object_id=profile.object_id,
            name=profile.name,
            email=profile.email,
            job_title="Development administrator",
            role_code="admin",
        )
        return user

    async def get_user(self, user_id: UUID) -> UserModel | None:
        """Return a local user for an administrative endpoint."""

        return await self.repository.get_user(user_id)

    async def promote_user(self, tenant_id: str, object_id: str) -> UserModel | None:
        """Promote an existing Entra user to administrator for bootstrap."""

        user = await self.repository.get_user_by_identity(tenant_id, object_id)
        if user is None:
            return None
        return await self.repository.update_user(
            user.id,
            role_code="admin",
            update_role=True,
        )

    async def list_users(self, offset: int, limit: int) -> tuple[list[UserModel], int]:
        """Return a paginated user list."""

        users = await self.repository.list_users(offset, limit)
        return list(users), await self.repository.count_users()

    async def update_user(
        self,
        user_id: UUID,
        *,
        job_title: str | None,
        role_code: str | None,
        area_id: UUID | None,
        update_job_title: bool,
        update_role: bool,
        update_area: bool,
    ) -> UserModel | None:
        """Apply admin changes while preserving at least one administrator."""

        current = await self.repository.get_user(user_id)
        if current is None:
            return None
        if update_role and role_code != "admin" and current.role.code == "admin":
            if await self.repository.count_admins() <= 1:
                raise ProtectedMutationError
        return await self.repository.update_user(
            user_id,
            job_title=job_title,
            role_code=role_code,
            area_id=area_id,
            update_job_title=update_job_title,
            update_role=update_role,
            update_area=update_area,
        )

    async def list_roles(self) -> list[RoleModel]:
        """Return configured platform roles."""

        await self.repository.ensure_role("admin", "Administrator")
        await self.repository.ensure_role("user", "User")
        return list(await self.repository.list_roles())

    async def photo(self, user: UserModel) -> bytes | None:
        """Read a private profile photo from object storage."""

        if not user.photo_key:
            return None
        try:
            return await self.storage.get_object(user.photo_key)
        except Exception:
            return None

    async def refresh_photo(self, user: UserModel, access_token: str) -> UserModel:
        """Capture the current Graph photo without changing local profile fields."""

        identity = await self.entra.validate_access_token(access_token)
        profile = await self.entra.profile(identity)
        if not profile.photo:
            return user
        key = f"profile-photos/{profile.tenant_id}/{profile.object_id}"
        await self.storage.put_object(key, profile.photo, content_type=profile.photo_content_type)
        return (
            await self.repository.update_photo_key(user.id, key, profile.photo_content_type) or user
        )


def user_view(user: UserModel) -> UserView:
    """Convert a detached persistence entity to a stable response value."""

    return UserView(
        id=user.id,
        tenant_id=user.tenant_id,
        entra_object_id=user.entra_object_id,
        name=user.name,
        email=user.email,
        job_title=user.job_title,
        role=user.role.code,
        area_id=user.area_id,
        area_name=user.area.name if user.area is not None else None,
        photo_available=user.photo_key is not None,
    )
