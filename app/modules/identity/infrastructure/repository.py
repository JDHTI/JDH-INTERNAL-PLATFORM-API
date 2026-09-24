"""Persistence adapter for identity records."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.infrastructure.database import Database
from app.modules.identity.infrastructure.models import AreaModel, RoleModel, UserModel


class IdentityRepository:
    """Keep database operations for users, roles, and areas behind a port."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @asynccontextmanager
    async def _session(self):
        async with self.database.session_factory() as session:
            yield session

    async def get_role_by_code(self, code: str) -> RoleModel | None:
        """Return a role by its stable code."""

        async with self._session() as session:
            return await session.scalar(select(RoleModel).where(RoleModel.code == code))

    async def ensure_role(self, code: str, name: str) -> RoleModel:
        """Return a role, creating it for an un-migrated development database."""

        async with self._session() as session:
            role = await session.scalar(select(RoleModel).where(RoleModel.code == code))
            if role is None:
                role = RoleModel(code=code, name=name)
                session.add(role)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    role = await session.scalar(select(RoleModel).where(RoleModel.code == code))
                    if role is None:
                        raise
                else:
                    await session.refresh(role)
            return role

    async def get_user_by_identity(self, tenant_id: str, object_id: str) -> UserModel | None:
        """Find a user by the immutable Entra tenant/object identity."""

        async with self._session() as session:
            return await session.scalar(
                select(UserModel)
                .where(
                    UserModel.tenant_id == tenant_id,
                    UserModel.entra_object_id == object_id,
                )
                .options(selectinload(UserModel.role), selectinload(UserModel.area))
            )

    async def get_user(self, user_id: UUID) -> UserModel | None:
        """Find a user by local UUID with role and area loaded."""

        async with self._session() as session:
            return await session.scalar(
                select(UserModel)
                .where(UserModel.id == user_id)
                .options(selectinload(UserModel.role), selectinload(UserModel.area))
            )

    async def create_user(
        self,
        *,
        tenant_id: str,
        object_id: str,
        name: str,
        email: str | None,
        job_title: str | None,
        role_code: str = "user",
    ) -> UserModel:
        """Create a user with the requested role and no area assignment."""

        async with self._session() as session:
            role = await session.scalar(select(RoleModel).where(RoleModel.code == role_code))
            if role is None:
                role = RoleModel(code=role_code, name=role_code.title())
                session.add(role)
                await session.flush()
            user = UserModel(
                tenant_id=tenant_id,
                entra_object_id=object_id,
                name=name,
                email=email,
                job_title=job_title,
                role_id=role.id,
            )
            session.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(UserModel)
                    .where(
                        UserModel.tenant_id == tenant_id,
                        UserModel.entra_object_id == object_id,
                    )
                    .options(selectinload(UserModel.role), selectinload(UserModel.area))
                )
                if existing is None:
                    raise
                return existing
            await session.refresh(user, attribute_names=["role", "area"])
            return user

    async def update_photo_key(
        self, user_id: UUID, photo_key: str | None, photo_content_type: str | None = None
    ) -> UserModel | None:
        """Set or clear the object-storage key for a user's profile photo."""

        async with self._session() as session:
            user = await session.get(UserModel, user_id)
            if user is None:
                return None
            user.photo_key = photo_key
            user.photo_content_type = photo_content_type
            await session.commit()
            await session.refresh(user, attribute_names=["role", "area"])
            return user

    async def list_users(self, offset: int, limit: int) -> Sequence[UserModel]:
        """Return a page of local users."""

        async with self._session() as session:
            result = await session.scalars(
                select(UserModel)
                .options(selectinload(UserModel.role), selectinload(UserModel.area))
                .order_by(UserModel.name, UserModel.id)
                .offset(offset)
                .limit(limit)
            )
            return result.all()

    async def count_users(self) -> int:
        """Return the number of local users."""

        async with self._session() as session:
            return int(await session.scalar(select(func.count()).select_from(UserModel)) or 0)

    async def update_user(
        self,
        user_id: UUID,
        *,
        job_title: str | None | Any = None,
        role_code: str | None | Any = None,
        area_id: UUID | None | Any = None,
        update_job_title: bool = False,
        update_role: bool = False,
        update_area: bool = False,
    ) -> UserModel | None:
        """Apply explicitly supplied administrative user changes."""

        async with self._session() as session:
            user = await session.get(UserModel, user_id)
            if user is None:
                return None
            if update_job_title:
                user.job_title = job_title
            if update_role:
                role = await session.scalar(select(RoleModel).where(RoleModel.code == role_code))
                if role is None:
                    raise ValueError("role_not_found")
                user.role_id = role.id
            if update_area:
                if area_id is not None:
                    area = await session.get(AreaModel, area_id)
                    if area is None or area.tenant_id != user.tenant_id:
                        raise ValueError("area_not_found")
                user.area_id = area_id
            await session.commit()
            await session.refresh(user, attribute_names=["role", "area"])
            return user

    async def count_admins(self) -> int:
        """Count users currently holding the administrator role."""

        async with self._session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(UserModel)
                    .join(RoleModel)
                    .where(RoleModel.code == "admin")
                )
                or 0
            )

    async def list_roles(self) -> Sequence[RoleModel]:
        """Return all configured roles."""

        async with self._session() as session:
            return (await session.scalars(select(RoleModel).order_by(RoleModel.code))).all()

    async def get_area(self, area_id: UUID) -> AreaModel | None:
        """Return an area by UUID."""

        async with self._session() as session:
            return await session.get(AreaModel, area_id)

    async def get_user_by_email(self, email: str) -> UserModel | None:
        """Find a user by email, ignoring case.

        Args:
            email (str): Email address to look up.

        Returns:
            UserModel | None: The user when one has that email.
        """

        async with self._session() as session:
            return await session.scalar(
                select(UserModel)
                .where(func.lower(UserModel.email) == email.strip().lower())
                .options(selectinload(UserModel.role), selectinload(UserModel.area))
                .limit(1)
            )

    async def get_users_by_ids(self, user_ids: Iterable[UUID]) -> Sequence[UserModel]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Local user identifiers.

        Returns:
            Sequence[UserModel]: The users that exist, in no particular order.
        """

        ids = set(user_ids)
        if not ids:
            return []
        async with self._session() as session:
            return (await session.scalars(select(UserModel).where(UserModel.id.in_(ids)))).all()
