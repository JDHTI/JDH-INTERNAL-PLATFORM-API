"""Use cases for browser SSO and local identity management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.config import Environment, Settings
from app.infrastructure.s3 import S3Storage
from app.modules.identity.application.directory import principal_from_user as principal_of
from app.modules.identity.domain.entities import EntraProfile, IdentityToken, UserView
from app.modules.identity.domain.principal import Principal
from app.modules.identity.domain.roles import (
    ASSIGNABLE_ROLES,
    GLOBAL_ROLES,
    OWNER,
    ROLE_NAMES,
    is_platform_admin,
)
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


class OwnerProtectedError(Exception):
    """Raised when an operation would deactivate the owner or change its role."""


class RoleChangeForbidden(Exception):
    """Raised when someone other than the owner tries to change a global role."""


class PermissionAudit(Protocol):
    """Where changes to who may do what are logged."""

    async def record_global_event(
        self,
        action: str,
        principal: Principal | None,
        *,
        target_user_id: UUID | None,
        detail: dict | None = None,
    ) -> None: ...


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
        audit: PermissionAudit | None = None,
    ) -> None:
        self.repository = repository
        self.entra = entra
        self.storage = storage
        self.settings = settings
        # Logs global role changes in the permissions log (the areas module implements it).
        self.audit = audit

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
        profile = await self.entra.profile(identity, tokens.graph_token)
        user, is_new = await self.register_profile(profile)
        if not getattr(user, "is_active", True):
            raise AuthenticationRequired
        return user, tokens, is_new

    async def register_profile(self, profile: EntraProfile) -> tuple[UserModel, bool]:
        """Create a default user once and attach an optional profile photo.

        An existing user gets the fields Entra owns (name, email, job title) refreshed
        when they changed; role, area, state and photo stay as they are.
        """

        existing = await self.repository.get_user_by_identity(profile.tenant_id, profile.object_id)
        if existing is not None:
            fields = (profile.name, profile.email, profile.job_title)
            if fields != (existing.name, existing.email, existing.job_title):
                refreshed = await self.repository.update_profile_fields(
                    existing.id,
                    name=profile.name,
                    email=profile.email,
                    job_title=profile.job_title,
                )
                existing = refreshed or existing
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
        if not getattr(user, "is_active", True):
            raise AuthenticationRequired
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

    async def list_users(
        self, offset: int, limit: int, is_active: bool | None = None
    ) -> tuple[list[UserModel], int]:
        """Return a paginated user list."""

        users = await self.repository.list_users(offset, limit, is_active)
        return list(users), await self.repository.count_users(is_active)

    async def update_user(
        self,
        user_id: UUID,
        *,
        job_title: str | None,
        role_code: str | None,
        area_id: UUID | None,
        is_active: bool | None,
        update_job_title: bool,
        update_role: bool,
        update_area: bool,
        update_is_active: bool,
        actor: UserModel | None = None,
    ) -> UserModel | None:
        """Apply admin changes while keeping the owner and at least one administrator.

        Only the owner changes global roles (``actor`` must be the owner when
        ``update_role``); ``owner`` itself is never assigned here. The owner cannot be
        deactivated nor change role.
        """

        current = await self.repository.get_user(user_id)
        if current is None:
            return None
        if update_is_active and is_active is None:
            raise ValueError("is_active_required")
        current_is_active = getattr(current, "is_active", True)
        previous_role = current.role.code
        if update_role and role_code == OWNER:
            raise ValueError("owner_not_assignable")
        if update_role and role_code not in ASSIGNABLE_ROLES:
            raise ValueError("role_not_found")
        if current.role.code == OWNER and (
            (update_role and role_code != OWNER) or (update_is_active and is_active is False)
        ):
            raise OwnerProtectedError
        if update_role and role_code != current.role.code:
            if actor is None or actor.role.code != OWNER:
                raise RoleChangeForbidden
        if (
            update_role
            and not is_platform_admin(role_code)
            and is_platform_admin(current.role.code)
        ):
            if current_is_active and await self.repository.count_admins(active_only=True) <= 1:
                raise ProtectedMutationError
        if update_is_active and is_active is False and current_is_active:
            if (
                is_platform_admin(current.role.code)
                and await self.repository.count_admins(active_only=True) <= 1
            ):
                raise ProtectedMutationError
        if update_area and area_id is not None and not current_is_active:
            raise ValueError("inactive_user")
        updated = await self.repository.update_user(
            user_id,
            job_title=job_title,
            role_code=role_code,
            area_id=area_id,
            is_active=is_active,
            update_job_title=update_job_title,
            update_role=update_role,
            update_area=update_area,
            update_is_active=update_is_active,
        )
        if self.audit is not None and updated is not None:
            actor_principal = principal_of(actor) if actor is not None else None
            if update_role and role_code != previous_role:
                await self.audit.record_global_event(
                    "global_role_changed",
                    actor_principal,
                    target_user_id=user_id,
                    detail={"before": previous_role, "after": role_code},
                )
            if update_is_active and is_active != current_is_active:
                await self.audit.record_global_event(
                    "user_activated" if is_active else "user_deactivated",
                    actor_principal,
                    target_user_id=user_id,
                )
        return updated

    async def seed_development_users(self) -> list[UserModel]:
        """Create or refresh deterministic users for a local development database."""

        if self.settings.ENVIRONMENT is not Environment.DEV:
            raise RuntimeError("Development seed is only allowed in ENVIRONMENT=dev")

        tenant_id = self.settings.AUTH_DEV_SEED_TENANT_ID or self.tenant_id
        seed_users = [
            {
                "tenant_id": tenant_id,
                "object_id": self.settings.AUTH_DEV_USER_OBJECT_ID,
                "name": "Analista TI",
                "email": self.settings.AUTH_DEV_SEED_ANALYST_EMAIL,
                "job_title": "Analista TI",
                "role_code": OWNER
                if self.settings.AUTH_DEV_SEED_ANALYST_EMAIL.lower()
                == self.settings.PLATFORM_OWNER_EMAIL.lower()
                else "admin",
            },
            *[
                {
                    "tenant_id": tenant_id,
                    "object_id": f"{self.settings.AUTH_DEV_SEED_MOCK_PREFIX}-{index}",
                    "name": f"Usuario Mock {index}",
                    "email": f"mock.usuario{index}@jdh.com.co",
                    "job_title": "Usuario de pruebas",
                    "role_code": "user",
                }
                for index in range(1, 5)
            ],
        ]
        return [await self.repository.ensure_seed_user(**seed_user) for seed_user in seed_users]

    async def list_roles(self) -> list[RoleModel]:
        """Return configured platform roles."""

        for code in GLOBAL_ROLES:
            await self.repository.ensure_role(code, ROLE_NAMES[code])
        return list(await self.repository.list_roles())

    async def set_owner(self, email: str) -> UserModel | None:
        """Make the user with this email the owner; the previous owner becomes admin.

        Args:
            email (str): Email of an existing, active user.

        Returns:
            UserModel | None: The new owner, or ``None`` when nobody has that email.
        """

        user = await self.repository.get_user_by_email(email)
        if user is None or not getattr(user, "is_active", True):
            return None
        return await self.repository.transfer_owner(user.id)

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
        is_active=getattr(user, "is_active", True),
        role=user.role.code,
        area_id=user.area_id,
        area_name=user.area.name if user.area is not None else None,
        photo_available=user.photo_key is not None,
    )
