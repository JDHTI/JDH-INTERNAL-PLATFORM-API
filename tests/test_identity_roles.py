"""Global roles: the owner rules, who changes roles, and the log of changes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Environment, Settings
from app.modules.identity.application.directory import principal_from_user
from app.modules.identity.application.provisioning import UserProvisioning
from app.modules.identity.application.service import (
    IdentityService,
    OwnerProtectedError,
    ProtectedMutationError,
    RoleChangeForbidden,
)


def _user(role: str, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id="tenant",
        entra_object_id=str(uuid4()),
        name=role,
        email=f"{role}@jdh.com.co",
        job_title=None,
        is_active=is_active,
        role=SimpleNamespace(code=role),
        area_id=None,
        area=None,
        photo_key=None,
    )


class Repository:
    def __init__(self, *users) -> None:
        self.users = {user.id: user for user in users}

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def get_user_by_identity(self, tenant_id, object_id):
        return next((u for u in self.users.values() if u.entra_object_id == object_id), None)

    async def count_admins(self, *, active_only=False):
        return sum(
            1
            for u in self.users.values()
            if u.role.code in ("owner", "admin") and (u.is_active or not active_only)
        )

    async def update_user(self, user_id, **changes):
        user = self.users[user_id]
        if changes.get("update_role"):
            user.role = SimpleNamespace(code=changes["role_code"])
        if changes.get("update_is_active"):
            user.is_active = changes["is_active"]
        return user


class Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def record_global_event(self, action, principal, *, target_user_id, detail=None):
        self.events.append((action, principal.user_id if principal else None, target_user_id))


def _service(*users, audit=None) -> tuple[IdentityService, Repository]:
    repository = Repository(*users)
    service = IdentityService(
        repository, None, None, Settings(ENVIRONMENT=Environment.TEST), audit=audit
    )
    return service, repository


def _change(service, user, actor, **changes):
    return service.update_user(
        user.id,
        actor=actor,
        job_title=None,
        role_code=changes.get("role"),
        area_id=None,
        is_active=changes.get("is_active"),
        update_job_title=False,
        update_role="role" in changes,
        update_area=False,
        update_is_active="is_active" in changes,
    )


def test_principals_carry_the_global_role() -> None:
    owner = principal_from_user(_user("owner"))
    moderator = principal_from_user(_user("moderator"))

    assert owner.is_platform_admin and owner.is_owner
    assert moderator.is_moderator and not moderator.is_platform_admin


@pytest.mark.asyncio
async def test_only_the_owner_changes_global_roles() -> None:
    owner, admin, user = _user("owner"), _user("admin"), _user("user")
    audit = Audit()
    service, _ = _service(owner, admin, user, audit=audit)

    with pytest.raises(RoleChangeForbidden):
        await _change(service, user, admin, role="moderator")
    await _change(service, user, owner, role="moderator")

    assert user.role.code == "moderator"
    assert audit.events == [("global_role_changed", owner.id, user.id)]


@pytest.mark.asyncio
async def test_the_owner_is_never_demoted_nor_deactivated() -> None:
    owner, admin = _user("owner"), _user("admin")
    service, _ = _service(owner, admin)

    with pytest.raises(OwnerProtectedError):
        await _change(service, owner, owner, role="admin")
    with pytest.raises(OwnerProtectedError):
        await _change(service, owner, admin, is_active=False)
    with pytest.raises(ValueError, match="owner_not_assignable"):
        await _change(service, admin, owner, role="owner")


@pytest.mark.asyncio
async def test_admins_still_block_users_and_the_last_admin_is_kept() -> None:
    owner, admin, user = _user("owner"), _user("admin"), _user("user")
    audit = Audit()
    service, _ = _service(owner, admin, user, audit=audit)

    await _change(service, user, admin, is_active=False)
    assert user.is_active is False
    assert audit.events == [("user_deactivated", admin.id, user.id)]

    lonely_admin = _user("admin")
    service, _ = _service(lonely_admin)
    with pytest.raises(ProtectedMutationError):
        await _change(service, lonely_admin, lonely_admin, is_active=False)


@pytest.mark.asyncio
async def test_the_directory_sync_never_blocks_the_owner() -> None:
    owner, admin = _user("owner"), _user("admin")
    port = UserProvisioning(Repository(owner, admin), "tenant")

    result = await port.deactivate_users([owner.id, admin.id])

    assert result.protected == (owner.id,) and owner.is_active is True
    assert result.deactivated == 1 and admin.is_active is False
