"""Users the directory provisions, and the profile refresh at sign-in."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Environment, Settings
from app.modules.identity.application.provisioning import UserProvisioning
from app.modules.identity.application.service import IdentityService
from app.modules.identity.domain.entities import EntraProfile


def _user(oid: str, role: str = "user", is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id="tenant",
        entra_object_id=oid,
        name=oid,
        email=f"{oid}@jdh.com.co",
        job_title=None,
        is_active=is_active,
        role=SimpleNamespace(code=role),
    )


class Repository:
    def __init__(self, *users) -> None:
        self.users = {user.entra_object_id: user for user in users}
        self.upserts: list[dict] = []
        self.profile_updates: list[dict] = []

    async def get_user_by_identity(self, tenant_id, object_id):
        assert tenant_id == "tenant"
        return self.users.get(object_id)

    async def get_user(self, user_id):
        return next((u for u in self.users.values() if u.id == user_id), None)

    async def count_admins(self, *, active_only=False):
        return sum(
            1
            for u in self.users.values()
            if u.role.code == "admin" and (u.is_active or not active_only)
        )

    async def upsert_user_by_identity(self, **fields):
        self.upserts.append(fields)
        user = self.users.get(fields["object_id"])
        created = user is None
        if created:
            user = _user(fields["object_id"])
            self.users[user.entra_object_id] = user
        user.name, user.email, user.job_title = fields["name"], fields["email"], fields["job_title"]
        if fields["is_active"] is not None:
            user.is_active = fields["is_active"]
        return user, created

    async def update_user(self, user_id, **changes):
        user = await self.get_user(user_id)
        user.is_active = changes["is_active"]
        return user

    async def update_profile_fields(self, user_id, **fields):
        self.profile_updates.append(fields)
        user = await self.get_user(user_id)
        user.name, user.email, user.job_title = fields["name"], fields["email"], fields["job_title"]
        return user


@pytest.mark.asyncio
async def test_upsert_creates_and_refreshes_only_entra_fields() -> None:
    repository = Repository()
    port = UserProvisioning(repository, "tenant")

    created = await port.upsert_user(
        "ana", name="Ana", email="ana@jdh.com.co", job_title="Analista", is_active=None
    )
    updated = await port.upsert_user(
        "ana", name="Ana Pérez", email="ana@jdh.com.co", job_title=None, is_active=None
    )

    assert created.created is True and updated.created is False
    assert created.user_id == updated.user_id and updated.is_active is True
    assert repository.users["ana"].name == "Ana Pérez"
    assert repository.upserts[1]["tenant_id"] == "tenant"
    assert set(repository.upserts[1]) == {
        "tenant_id",
        "object_id",
        "name",
        "email",
        "job_title",
        "is_active",
    }


@pytest.mark.asyncio
async def test_the_last_active_admin_is_never_blocked() -> None:
    admin = _user("boss", role="admin")
    other = _user("ana")
    repository = Repository(admin, other)
    port = UserProvisioning(repository, "tenant")

    blocked = await port.upsert_user(
        "boss", name="Jefe", email=None, job_title=None, is_active=False
    )
    result = await port.deactivate_users([admin.id, other.id, other.id])

    assert blocked.last_admin_protected is True and admin.is_active is True
    assert result.deactivated == 1 and result.protected == (admin.id,)
    assert other.is_active is False


@pytest.mark.asyncio
async def test_a_second_admin_can_be_blocked() -> None:
    first, second = _user("a", role="admin"), _user("b", role="admin")
    port = UserProvisioning(Repository(first, second), "tenant")

    result = await port.deactivate_users([second.id])

    assert result.deactivated == 1 and second.is_active is False


@pytest.mark.asyncio
async def test_sign_in_refreshes_name_email_and_job_title() -> None:
    user = _user("ana")
    repository = Repository(user)
    service = IdentityService(repository, None, None, Settings(ENVIRONMENT=Environment.TEST))
    profile = EntraProfile("tenant", "ana", "Ana Pérez", "ana.perez@jdh.com.co", "Líder BI")

    refreshed, created = await service.register_profile(profile)
    again, _ = await service.register_profile(profile)

    assert created is False and refreshed.name == "Ana Pérez"
    assert refreshed.job_title == "Líder BI" and again is user
    assert len(repository.profile_updates) == 1
