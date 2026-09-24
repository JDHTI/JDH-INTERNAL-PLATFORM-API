from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import Environment, Settings
from app.modules.identity.application.service import IdentityService, ProtectedMutationError


def _user(*, role: str, is_active: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id="tenant",
        entra_object_id=str(uuid4()),
        name="Test user",
        email="test@example.test",
        job_title="Tester",
        is_active=is_active,
        role=SimpleNamespace(code=role),
        area_id=None,
        area=None,
        photo_key=None,
    )


class UpdateRepository:
    def __init__(self, user):
        self.user = user
        self.updated = False

    async def get_user(self, user_id):
        return self.user

    async def count_admins(self, *, active_only=False):
        assert active_only is True
        return 1

    async def update_user(self, *args, **kwargs):
        self.updated = True
        return self.user


@pytest.mark.asyncio
async def test_cannot_deactivate_or_degrade_the_last_active_admin() -> None:
    user = _user(role="admin", is_active=True)
    repository = UpdateRepository(user)
    service = IdentityService(repository, None, None, Settings(ENVIRONMENT=Environment.DEV))

    with pytest.raises(ProtectedMutationError):
        await service.update_user(
            user.id,
            job_title=None,
            role_code=None,
            area_id=None,
            is_active=False,
            update_job_title=False,
            update_role=False,
            update_area=False,
            update_is_active=True,
        )
    with pytest.raises(ProtectedMutationError):
        await service.update_user(
            user.id,
            job_title=None,
            role_code="user",
            area_id=None,
            is_active=None,
            update_job_title=False,
            update_role=True,
            update_area=False,
            update_is_active=False,
        )
    assert repository.updated is False


class SeedRepository:
    def __init__(self):
        self.users = {}

    async def ensure_seed_user(self, **fields):
        key = (fields["tenant_id"], fields["object_id"])
        user = self.users.get(key)
        if user is None:
            user = SimpleNamespace(
                id=uuid4(),
                email=fields["email"],
                is_active=True,
                role=SimpleNamespace(code=fields["role_code"]),
            )
            self.users[key] = user
        return user


@pytest.mark.asyncio
async def test_development_seed_is_deterministic_and_idempotent() -> None:
    settings = Settings(
        ENVIRONMENT=Environment.DEV,
        ENTRA_TENANT_ID="tenant",
        AUTH_DEV_USER_OBJECT_ID="analyst-object",
    )
    repository = SeedRepository()
    service = IdentityService(repository, None, None, settings)

    first = await service.seed_development_users()
    second = await service.seed_development_users()

    assert len(first) == len(second) == 5
    assert len(repository.users) == 5
    assert first[0].email == "analista.ti@jdh.com.co"
    assert first[0].role.code == "admin"
    assert all(user.role.code == "user" for user in first[1:])
    assert all(user.is_active for user in first)
    assert [user.id for user in first] == [user.id for user in second]
    assert [object_id for _, object_id in repository.users] == [
        "analyst-object",
        "development-mock-1",
        "development-mock-2",
        "development-mock-3",
        "development-mock-4",
    ]


@pytest.mark.asyncio
async def test_development_seed_rejects_non_dev_environment() -> None:
    settings = Settings(ENVIRONMENT=Environment.TEST)
    service = IdentityService(SeedRepository(), None, None, settings)

    with pytest.raises(RuntimeError, match="ENVIRONMENT=dev"):
        await service.seed_development_users()
