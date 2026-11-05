"""PostgreSQL implementation of the area repository."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.areas.domain.entities import (
    Area,
    AreaMember,
    AreaRole,
    PermissionEvent,
    RolePermission,
)
from app.modules.areas.infrastructure.models import (
    AreaMemberModel,
    AreaModel,
    AreaRoleModel,
    AreaRolePermissionModel,
    PermissionEventModel,
)


def _area(m: AreaModel) -> Area:
    return Area(
        id=m.id,
        slug=m.slug,
        name=m.name,
        remit=m.remit,
        icon=m.icon,
        sort_order=m.sort_order,
        responsible_user_id=m.responsible_user_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _role(m: AreaRoleModel) -> AreaRole:
    return AreaRole(
        id=m.id,
        area_id=m.area_id,
        name=m.name,
        created_at=m.created_at,
        updated_at=m.updated_at,
        is_leader=bool(m.is_leader),
    )


def _member(m: AreaMemberModel) -> AreaMember:
    return AreaMember(
        id=m.id,
        area_id=m.area_id,
        user_id=m.user_id,
        role_id=m.role_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


_ORDER = (AreaModel.sort_order, AreaModel.name)


class SqlAlchemyAreaRepository:
    """Persist areas, roles and members; each operation owns its transaction."""

    def __init__(self, database: Database, tenant_id: str) -> None:
        """Create the repository.

        Args:
            database (Database): Shared engine and session factory.
            tenant_id (str): Entra tenant new areas belong to, the same one the
                identity module assigns to users.
        """

        self.database = database
        self.tenant_id = tenant_id

    async def _add(self, model):
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("El registro entra en conflicto con uno existente") from exc
            await session.refresh(model)
            return model

    async def _update(self, model_cls, entity, fields: tuple[str, ...]):
        async with self.database.session_factory() as session:
            model = await session.get(model_cls, entity.id)
            for name in fields:
                setattr(model, name, getattr(entity, name))
            try:
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("El registro entra en conflicto con uno existente") from exc
            await session.refresh(model)
            return model

    async def _delete(self, statement) -> None:
        async with self.database.session_factory() as session:
            try:
                await session.execute(statement)
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("El registro está en uso y no se puede borrar") from exc

    # --- Areas ---

    async def list_areas(self) -> list[Area]:
        """List every area in display order.

        Returns:
            list[Area]: All areas, ordered by ``sort_order`` then name.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(select(AreaModel).order_by(*_ORDER))
            return [_area(m) for m in result.scalars()]

    async def list_areas_for_user(self, user_id: UUID) -> list[tuple[Area, AreaRole | None]]:
        """List the areas where a user is a member or the responsible.

        Args:
            user_id (UUID): The user.

        Returns:
            list[tuple[Area, AreaRole | None]]: Each area with the user's role
                there, or ``None`` when they are only its responsible.
        """

        query = (
            select(AreaModel, AreaRoleModel)
            .outerjoin(
                AreaMemberModel,
                (AreaMemberModel.area_id == AreaModel.id) & (AreaMemberModel.user_id == user_id),
            )
            .outerjoin(AreaRoleModel, AreaRoleModel.id == AreaMemberModel.role_id)
            .where(or_(AreaMemberModel.id.is_not(None), AreaModel.responsible_user_id == user_id))
            .order_by(*_ORDER)
        )
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [(_area(a), _role(r) if r else None) for a, r in result.all()]

    async def get_by_slug(self, slug: str) -> Area | None:
        """Find an area by its URL slug.

        Args:
            slug (str): Area slug.

        Returns:
            Area | None: The area when it exists.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(select(AreaModel).where(AreaModel.slug == slug))
            model = result.scalar_one_or_none()
            return _area(model) if model else None

    async def get_by_id(self, area_id: UUID) -> Area | None:
        """Find an area by identifier.

        Args:
            area_id (UUID): Area identifier.

        Returns:
            Area | None: The area when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(AreaModel, area_id)
            return _area(model) if model else None

    async def find_conflicting_area(
        self, slug: str | None, name: str | None, exclude_id: UUID | None = None
    ) -> Area | None:
        """Find another area that already uses the slug or the name.

        Args:
            slug (str | None): Slug to check, or ``None`` to skip.
            name (str | None): Name to check case-insensitively, or ``None``.
            exclude_id (UUID | None): Area to ignore (the one being edited).

        Returns:
            Area | None: The conflicting area, if any.
        """

        conditions = []
        if slug is not None:
            conditions.append(AreaModel.slug == slug)
        if name is not None:
            conditions.append(func.lower(AreaModel.name) == name.lower())
        if not conditions:
            return None
        query = select(AreaModel).where(or_(*conditions))
        if exclude_id is not None:
            query = query.where(AreaModel.id != exclude_id)
        async with self.database.session_factory() as session:
            model = (await session.execute(query.limit(1))).scalar_one_or_none()
            return _area(model) if model else None

    async def create_area(
        self,
        slug: str,
        name: str,
        remit: str | None,
        icon: str | None,
        sort_order: int,
        responsible_user_id: UUID | None,
    ) -> Area:
        """Persist a new area.

        Args:
            slug (str): Unique URL slug.
            name (str): Unique display name.
            remit (str | None): One-line description of the area's remit.
            icon (str | None): Icon name the UI maps to a glyph.
            sort_order (int): Position in navigation.
            responsible_user_id (UUID | None): Responsible user.

        Returns:
            Area: The stored area.
        """

        model = AreaModel(
            tenant_id=self.tenant_id,
            slug=slug,
            name=name,
            remit=remit,
            icon=icon,
            sort_order=sort_order,
            responsible_user_id=responsible_user_id,
        )
        return _area(await self._add(model))

    async def save_area(self, area: Area) -> Area:
        """Persist changes to an existing area.

        Args:
            area (Area): Area with updated fields.

        Returns:
            Area: The stored area.
        """

        fields = ("name", "remit", "icon", "sort_order", "responsible_user_id")
        return _area(await self._update(AreaModel, area, fields))

    async def delete_area(self, area_id: UUID) -> None:
        """Delete an area with its roles and members.

        Args:
            area_id (UUID): Area to delete. Fails with a conflict while the
                area still owns document folders.

        Returns:
            None
        """

        try:
            await self._delete(delete(AreaModel).where(AreaModel.id == area_id))
        except ConflictError as exc:
            raise ConflictError(
                "El área tiene carpetas de documentos o de proyectos: elimínalas antes de borrarla"
            ) from exc

    # --- Roles ---

    async def list_roles(self, area_id: UUID) -> list[AreaRole]:
        """List the role catalog of an area.

        Args:
            area_id (UUID): The area.

        Returns:
            list[AreaRole]: The leader role first, then the rest by name.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(AreaRoleModel)
                .where(AreaRoleModel.area_id == area_id)
                .order_by(AreaRoleModel.is_leader.desc(), AreaRoleModel.name)
            )
            return [_role(m) for m in result.scalars()]

    async def get_role(self, area_id: UUID, role_id: UUID) -> AreaRole | None:
        """Find a role inside a given area.

        Args:
            area_id (UUID): Area that must own the role.
            role_id (UUID): The role.

        Returns:
            AreaRole | None: The role when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(AreaRoleModel).where(
                    AreaRoleModel.area_id == area_id, AreaRoleModel.id == role_id
                )
            )
            model = result.scalar_one_or_none()
            return _role(model) if model else None

    async def role_name_taken(
        self, area_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the area already has a role with this name.

        Args:
            area_id (UUID): The area.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Role to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """

        condition = (AreaRoleModel.area_id == area_id) & (
            func.lower(AreaRoleModel.name) == name.lower()
        )
        if exclude_id is not None:
            condition = condition & (AreaRoleModel.id != exclude_id)
        async with self.database.session_factory() as session:
            return bool((await session.execute(select(exists().where(condition)))).scalar())

    async def create_role(self, area_id: UUID, name: str, is_leader: bool = False) -> AreaRole:
        """Persist a new role in an area.

        Args:
            area_id (UUID): Owning area.
            name (str): Role name.
            is_leader (bool): Whether it is the area's leader role (one per area).

        Returns:
            AreaRole: The stored role.
        """

        return _role(
            await self._add(AreaRoleModel(area_id=area_id, name=name, is_leader=is_leader))
        )

    async def save_role(self, role: AreaRole) -> AreaRole:
        """Persist changes to a role.

        Args:
            role (AreaRole): Role with updated fields.

        Returns:
            AreaRole: The stored role.
        """

        return _role(await self._update(AreaRoleModel, role, ("name",)))

    async def delete_role(self, role_id: UUID) -> None:
        """Delete a role.

        Args:
            role_id (UUID): Role to delete.

        Returns:
            None
        """

        await self._delete(delete(AreaRoleModel).where(AreaRoleModel.id == role_id))

    async def role_in_use(self, role_id: UUID) -> bool:
        """Check whether any member holds the role.

        Args:
            role_id (UUID): The role.

        Returns:
            bool: ``True`` when at least one member has it.
        """

        async with self.database.session_factory() as session:
            query = select(exists().where(AreaMemberModel.role_id == role_id))
            return bool((await session.execute(query)).scalar())

    # --- Role permissions and the permissions log ---

    async def get_role_permissions(self, role_ids: Iterable[UUID]) -> dict[UUID, dict[str, str]]:
        """The grants of several area roles.

        Args:
            role_ids (Iterable[UUID]): Roles to load.

        Returns:
            dict[UUID, dict[str, str]]: Per role, module (or ``*``) -> action.
        """

        ids = list(dict.fromkeys(role_ids))
        result: dict[UUID, dict[str, str]] = {role_id: {} for role_id in ids}
        if not ids:
            return result
        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(AreaRolePermissionModel).where(AreaRolePermissionModel.role_id.in_(ids))
            )
            for row in rows:
                result[row.role_id][row.module] = row.action
        return result

    async def replace_role_permissions(self, role_id: UUID, grants: list[RolePermission]) -> None:
        """Replace every grant of a role in one transaction.

        Args:
            role_id (UUID): The role.
            grants (list[RolePermission]): The new grants, one per module.
        """

        async with self.database.session_factory() as session:
            await session.execute(
                delete(AreaRolePermissionModel).where(AreaRolePermissionModel.role_id == role_id)
            )
            session.add_all(
                AreaRolePermissionModel(role_id=role_id, module=grant.module, action=grant.action)
                for grant in grants
            )
            await session.commit()

    async def record_event(
        self,
        action: str,
        *,
        actor_user_id: UUID | None,
        area_id: UUID | None = None,
        target_user_id: UUID | None = None,
        role_id: UUID | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append an entry to the permissions log.

        Args:
            action (str): What happened (``member_added``, ``role_permissions_set``...).
            actor_user_id (UUID | None): Who did it.
            area_id (UUID | None): Area involved.
            target_user_id (UUID | None): Person affected.
            role_id (UUID | None): Role involved.
            detail (dict | None): Before/after values.
        """

        async with self.database.session_factory() as session:
            session.add(
                PermissionEventModel(
                    action=action,
                    actor_user_id=actor_user_id,
                    area_id=area_id,
                    target_user_id=target_user_id,
                    role_id=role_id,
                    detail=detail or {},
                )
            )
            await session.commit()

    async def list_events(
        self,
        *,
        area_id: UUID | None,
        target_user_id: UUID | None,
        action: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[PermissionEvent], int]:
        """A page of the permissions log, newest first, and the total.

        Args:
            area_id (UUID | None): Only this area.
            target_user_id (UUID | None): Only this person.
            action (str | None): Only this kind of change.
            offset (int): Rows to skip.
            limit (int): Rows to return.

        Returns:
            tuple[list[PermissionEvent], int]: The page and the total.
        """

        conditions = []
        if area_id is not None:
            conditions.append(PermissionEventModel.area_id == area_id)
        if target_user_id is not None:
            conditions.append(PermissionEventModel.target_user_id == target_user_id)
        if action:
            conditions.append(PermissionEventModel.action == action)
        async with self.database.session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(PermissionEventModel).where(*conditions)
            )
            rows = await session.scalars(
                select(PermissionEventModel)
                .where(*conditions)
                .order_by(PermissionEventModel.at.desc(), PermissionEventModel.id)
                .offset(offset)
                .limit(limit)
            )
            return [
                PermissionEvent(
                    id=row.id,
                    at=row.at,
                    action=row.action,
                    actor_user_id=row.actor_user_id,
                    area_id=row.area_id,
                    target_user_id=row.target_user_id,
                    role_id=row.role_id,
                    detail=dict(row.detail or {}),
                )
                for row in rows
            ], int(total or 0)

    # --- Members ---

    async def list_members(self, area_id: UUID) -> list[AreaMember]:
        """List the members of an area.

        Args:
            area_id (UUID): The area.

        Returns:
            list[AreaMember]: Members in insertion order.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(AreaMemberModel)
                .where(AreaMemberModel.area_id == area_id)
                .order_by(AreaMemberModel.created_at)
            )
            return [_member(m) for m in result.scalars()]

    async def get_member(self, area_id: UUID, member_id: UUID) -> AreaMember | None:
        """Find a membership inside a given area.

        Args:
            area_id (UUID): Area that must own the membership.
            member_id (UUID): The membership.

        Returns:
            AreaMember | None: The membership when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(AreaMemberModel).where(
                    AreaMemberModel.area_id == area_id, AreaMemberModel.id == member_id
                )
            )
            model = result.scalar_one_or_none()
            return _member(model) if model else None

    async def get_member_by_user(self, area_id: UUID, user_id: UUID) -> AreaMember | None:
        """Find a user's membership in an area.

        Args:
            area_id (UUID): The area.
            user_id (UUID): The user.

        Returns:
            AreaMember | None: The membership when the user belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(AreaMemberModel).where(
                    AreaMemberModel.area_id == area_id, AreaMemberModel.user_id == user_id
                )
            )
            model = result.scalar_one_or_none()
            return _member(model) if model else None

    async def create_member(self, area_id: UUID, user_id: UUID, role_id: UUID) -> AreaMember:
        """Persist a membership.

        Args:
            area_id (UUID): The area.
            user_id (UUID): The user.
            role_id (UUID): A role of the same area.

        Returns:
            AreaMember: The stored membership.
        """

        model = AreaMemberModel(area_id=area_id, user_id=user_id, role_id=role_id)
        return _member(await self._add(model))

    async def save_member(self, member: AreaMember) -> AreaMember:
        """Persist changes to a membership.

        Args:
            member (AreaMember): Membership with updated fields.

        Returns:
            AreaMember: The stored membership.
        """

        return _member(await self._update(AreaMemberModel, member, ("role_id",)))

    async def delete_member(self, member_id: UUID) -> None:
        """Delete a membership.

        Args:
            member_id (UUID): Membership to delete.

        Returns:
            None
        """

        await self._delete(delete(AreaMemberModel).where(AreaMemberModel.id == member_id))
