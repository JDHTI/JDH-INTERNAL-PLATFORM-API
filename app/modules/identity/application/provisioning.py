"""Users provisioned by the employee directory before their first sign-in."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.entities import DeactivationResult, ProvisionedUser
from app.modules.identity.domain.roles import OWNER, is_platform_admin


class ProvisioningRepository(Protocol):
    """Identity persistence the provisioning needs."""

    async def upsert_user_by_identity(
        self,
        *,
        tenant_id: str,
        object_id: str,
        name: str,
        email: str | None,
        job_title: str | None,
        is_active: bool | None,
    ): ...

    async def get_user_by_identity(self, tenant_id: str, object_id: str): ...

    async def get_user(self, user_id: UUID): ...

    async def count_admins(self, *, active_only: bool = False) -> int: ...

    async def update_user(self, user_id: UUID, **changes): ...


class UserProvisioning:
    """Create and block platform users from the Entra directory.

    The directory owns who works at the company; identity keeps owning the users
    table. Role, area and photo are never changed here, and the last active
    administrator is never blocked.
    """

    def __init__(self, repository: ProvisioningRepository, tenant_id: str) -> None:
        """Create the port.

        Args:
            repository (ProvisioningRepository): Identity persistence adapter.
            tenant_id (str): Tenant of the SSO (the one sign-in uses), so a provisioned
                user and a later sign-in land on the same row.
        """

        self.repository = repository
        self.tenant_id = tenant_id

    async def upsert_user(
        self,
        object_id: str,
        *,
        name: str,
        email: str | None,
        job_title: str | None,
        is_active: bool | None,
    ) -> ProvisionedUser:
        """Create a user with the ``user`` role, or refresh name, email and job title.

        Args:
            object_id (str): Entra object id.
            name (str): Display name.
            email (str | None): Mail or user principal name.
            job_title (str | None): Job title in Entra.
            is_active (bool | None): ``False`` blocks, ``True`` unblocks, ``None`` keeps.

        Returns:
            ProvisionedUser: The user, whether it was created and its resulting state.
        """

        protected = False
        if is_active is False:
            current = await self.repository.get_user_by_identity(self.tenant_id, object_id)
            if current is not None and await self._is_last_admin(current):
                is_active, protected = None, True
        user, created = await self.repository.upsert_user_by_identity(
            tenant_id=self.tenant_id,
            object_id=object_id,
            name=name,
            email=email,
            job_title=job_title,
            is_active=is_active,
        )
        return ProvisionedUser(
            user_id=user.id,
            created=created,
            is_active=bool(getattr(user, "is_active", True)),
            last_admin_protected=protected,
        )

    async def deactivate_users(self, user_ids: Iterable[UUID]) -> DeactivationResult:
        """Block users that left the directory.

        Args:
            user_ids (Iterable[UUID]): Users of employees Entra no longer lists.

        Returns:
            DeactivationResult: How many were blocked and which admins were kept.
        """

        deactivated = 0
        protected: list[UUID] = []
        for user_id in dict.fromkeys(user_ids):
            user = await self.repository.get_user(user_id)
            if user is None or not getattr(user, "is_active", True):
                continue
            if await self._is_last_admin(user):
                protected.append(user_id)
                continue
            await self.repository.update_user(user_id, is_active=False, update_is_active=True)
            deactivated += 1
        return DeactivationResult(deactivated=deactivated, protected=tuple(protected))

    async def _is_last_admin(self, user) -> bool:
        """Whether blocking this user is forbidden: the owner, or the last active admin."""

        if user.role.code == OWNER:
            return True
        if not is_platform_admin(user.role.code) or not getattr(user, "is_active", True):
            return False
        return await self.repository.count_admins(active_only=True) <= 1
