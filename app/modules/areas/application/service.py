"""Area use cases: catalog, roles, members and access to an area's space.

Rules:
- Platform administrators manage areas, roles and members, and see every area.
- Everyone else sees only the areas where they are a member or the responsible.
- A member's role must come from the catalog of the same area.
- An area that still has document folders cannot be deleted; neither can a
  role that members hold.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.areas.application.dto import (
    AreaView,
    CreateAreaCommand,
    MemberView,
    RoleRef,
    UpdateAreaCommand,
    UserRef,
)
from app.modules.areas.domain.entities import Area, AreaRole, clean_name, validate_slug
from app.modules.areas.domain.ports import AreaRepository, DirectoryUser, UserDirectory
from app.modules.identity.domain.entities import Principal


def _user_ref(user: DirectoryUser | None) -> UserRef | None:
    if user is None:
        return None
    return UserRef(id=user.id, email=user.email, display_name=user.display_name)


def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    return clean_name(value, field, max_length)


class AreaService:
    """Coordinate area use cases and answer access questions for other modules."""

    def __init__(self, repository: AreaRepository, users: UserDirectory) -> None:
        """Create the service with its ports.

        Args:
            repository (AreaRepository): Port that stores areas, roles and members.
            users (UserDirectory): Port that resolves platform users.
        """

        self.repository = repository
        self.users = users

    # --- Helpers ---

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        if not principal.is_platform_admin:
            raise ForbiddenError("Solo un administrador de la plataforma puede hacer esto")

    async def _get_area(self, slug: str) -> Area:
        area = await self.repository.get_by_slug(slug)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return area

    async def _get_role(self, area_id: UUID, role_id: UUID) -> AreaRole:
        role = await self.repository.get_role(area_id, role_id)
        if role is None:
            raise NotFoundError("Rol no encontrado en esta área")
        return role

    async def _views(
        self,
        rows: Iterable[tuple[Area, AreaRole | None]],
        principal: Principal,
    ) -> list[AreaView]:
        rows = list(rows)
        responsibles = await self.users.get_users(
            area.responsible_user_id for area, _ in rows if area.responsible_user_id
        )
        return [
            AreaView(
                id=area.id,
                slug=area.slug,
                name=area.name,
                remit=area.remit,
                icon=area.icon,
                sort_order=area.sort_order,
                responsible=_user_ref(responsibles.get(area.responsible_user_id)),
                my_role=RoleRef(id=role.id, name=role.name) if role else None,
                is_responsible=area.responsible_user_id == principal.user_id,
                created_at=area.created_at,
                updated_at=area.updated_at,
            )
            for area, role in rows
        ]

    async def _view(self, area: Area, principal: Principal) -> AreaView:
        member = await self.repository.get_member_by_user(area.id, principal.user_id)
        role = await self.repository.get_role(area.id, member.role_id) if member else None
        return (await self._views([(area, role)], principal))[0]

    async def _check_unique(
        self, slug: str | None, name: str | None, exclude_id: UUID | None = None
    ) -> None:
        conflict = await self.repository.find_conflicting_area(slug, name, exclude_id)
        if conflict is None:
            return
        if slug is not None and conflict.slug == slug:
            raise ConflictError("Ya existe un área con ese identificador")
        raise ConflictError("Ya existe un área con ese nombre")

    # --- Access (also used by the documents module) ---

    async def authorize(self, slug: str, principal: Principal) -> Area:
        """Return the area when the principal may work in its space.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            Area: The area, for administrators, its responsible or its members.
        """

        return await self._authorize(await self._get_area(slug), principal)

    async def authorize_by_id(self, area_id: UUID, principal: Principal) -> Area:
        """Return the area by identifier when the principal may work in its space.

        Args:
            area_id (UUID): Area identifier.
            principal (Principal): The caller.

        Returns:
            Area: The area, for administrators, its responsible or its members.
        """

        area = await self.repository.get_by_id(area_id)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return await self._authorize(area, principal)

    async def _authorize(self, area: Area, principal: Principal) -> Area:
        if principal.is_platform_admin or area.responsible_user_id == principal.user_id:
            return area
        if await self.repository.get_member_by_user(area.id, principal.user_id) is None:
            raise ForbiddenError("No perteneces a esta área")
        return area

    async def accessible_area_ids(self, principal: Principal) -> list[UUID] | None:
        """List the areas whose space the principal may use.

        Args:
            principal (Principal): The caller.

        Returns:
            list[UUID] | None: Area identifiers, or ``None`` meaning every area
                (platform administrators).
        """

        if principal.is_platform_admin:
            return None
        rows = await self.repository.list_areas_for_user(principal.user_id)
        return [area.id for area, _ in rows]

    # --- Areas ---

    async def list_areas(self, principal: Principal) -> list[AreaView]:
        """List the areas visible to the principal, in navigation order.

        Args:
            principal (Principal): The caller.

        Returns:
            list[AreaView]: Every area for administrators; otherwise those
                where the caller is a member or the responsible.
        """

        mine = await self.repository.list_areas_for_user(principal.user_id)
        if not principal.is_platform_admin:
            return await self._views(mine, principal)
        roles = {area.id: role for area, role in mine}
        areas = await self.repository.list_areas()
        return await self._views(((a, roles.get(a.id)) for a in areas), principal)

    async def get_area(self, slug: str, principal: Principal) -> AreaView:
        """Return one area the principal can access.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            AreaView: The area with the caller's role in it.
        """

        return await self._view(await self.authorize(slug, principal), principal)

    async def create_area(self, command: CreateAreaCommand, principal: Principal) -> AreaView:
        """Create an area.

        Args:
            command (CreateAreaCommand): Area data; the responsible is given by
                email and provisioned as a stub if they never signed in.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            AreaView: The created area.
        """

        self._require_admin(principal)
        slug = validate_slug(command.slug.strip().lower())
        name = clean_name(command.name)
        await self._check_unique(slug, name)
        responsible = (
            await self.users.ensure_user(command.responsible_email)
            if command.responsible_email
            else None
        )
        area = await self.repository.create_area(
            slug=slug,
            name=name,
            remit=_optional_text(command.remit, "alcance", 300),
            icon=_optional_text(command.icon, "ícono", 64),
            sort_order=command.sort_order,
            responsible_user_id=responsible.id if responsible else None,
        )
        return await self._view(area, principal)

    async def update_area(
        self, slug: str, command: UpdateAreaCommand, principal: Principal
    ) -> AreaView:
        """Apply a partial update to an area. The slug is immutable.

        Args:
            slug (str): Area slug.
            command (UpdateAreaCommand): Fields to change; a ``None``
                ``responsible_email`` in ``fields`` removes the responsible.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            AreaView: The updated area.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        if "name" in command.fields and command.name is not None:
            area.name = clean_name(command.name)
            await self._check_unique(None, area.name, exclude_id=area.id)
        if "remit" in command.fields:
            area.remit = _optional_text(command.remit, "alcance", 300)
        if "icon" in command.fields:
            area.icon = _optional_text(command.icon, "ícono", 64)
        if "sort_order" in command.fields and command.sort_order is not None:
            area.sort_order = command.sort_order
        if "responsible_email" in command.fields:
            if command.responsible_email:
                area.responsible_user_id = (
                    await self.users.ensure_user(command.responsible_email)
                ).id
            else:
                area.responsible_user_id = None
        return await self._view(await self.repository.save_area(area), principal)

    async def delete_area(self, slug: str, principal: Principal) -> None:
        """Delete an area with its roles and members.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            None
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        await self.repository.delete_area(area.id)

    # --- Roles ---

    async def list_roles(self, slug: str, principal: Principal) -> list[AreaRole]:
        """List the role catalog of an area the principal can access.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            list[AreaRole]: Roles ordered by name.
        """

        area = await self.authorize(slug, principal)
        return await self.repository.list_roles(area.id)

    async def create_role(self, slug: str, name: str, principal: Principal) -> AreaRole:
        """Add a role to an area's catalog.

        Args:
            slug (str): Area slug.
            name (str): Role name, unique inside the area.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            AreaRole: The created role.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        name = clean_name(name, max_length=100)
        if await self.repository.role_name_taken(area.id, name):
            raise ConflictError("El área ya tiene un rol con ese nombre")
        return await self.repository.create_role(area.id, name)

    async def rename_role(
        self, slug: str, role_id: UUID, name: str, principal: Principal
    ) -> AreaRole:
        """Rename a role of an area.

        Args:
            slug (str): Area slug.
            role_id (UUID): The role.
            name (str): New name, unique inside the area.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            AreaRole: The renamed role.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        role.name = clean_name(name, max_length=100)
        if await self.repository.role_name_taken(area.id, role.name, exclude_id=role.id):
            raise ConflictError("El área ya tiene un rol con ese nombre")
        return await self.repository.save_role(role)

    async def delete_role(self, slug: str, role_id: UUID, principal: Principal) -> None:
        """Delete a role nobody holds.

        Args:
            slug (str): Area slug.
            role_id (UUID): The role.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            None
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        if await self.repository.role_in_use(role.id):
            raise ConflictError("El rol tiene miembros asignados: reasígnalos antes de borrarlo")
        await self.repository.delete_role(role.id)

    # --- Members ---

    async def list_members(self, slug: str, principal: Principal) -> list[MemberView]:
        """List the members of an area the principal can access.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            list[MemberView]: Members with their user and role.
        """

        area = await self.authorize(slug, principal)
        members = await self.repository.list_members(area.id)
        roles = {role.id: role for role in await self.repository.list_roles(area.id)}
        users = await self.users.get_users(m.user_id for m in members)
        views = [
            MemberView(
                id=m.id,
                user=_user_ref(users[m.user_id]),
                role=RoleRef(id=m.role_id, name=roles[m.role_id].name),
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in members
            if m.user_id in users and m.role_id in roles
        ]
        return sorted(views, key=lambda v: (v.role.name, v.user.display_name or v.user.email))

    async def add_member(
        self, slug: str, email: str, role_id: UUID, principal: Principal
    ) -> MemberView:
        """Assign a person to an area with one of its roles.

        Args:
            slug (str): Area slug.
            email (str): Person's email; provisioned as a stub if they never
                signed in, and linked on their first sign-in.
            role_id (UUID): A role of the same area.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            MemberView: The created membership.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        user = await self.users.ensure_user(email)
        if await self.repository.get_member_by_user(area.id, user.id):
            raise ConflictError("La persona ya es miembro del área")
        member = await self.repository.create_member(area.id, user.id, role.id)
        return MemberView(
            id=member.id,
            user=_user_ref(user),
            role=RoleRef(id=role.id, name=role.name),
            created_at=member.created_at,
            updated_at=member.updated_at,
        )

    async def change_member_role(
        self, slug: str, member_id: UUID, role_id: UUID, principal: Principal
    ) -> MemberView:
        """Change the role of a member.

        Args:
            slug (str): Area slug.
            member_id (UUID): The membership.
            role_id (UUID): A role of the same area.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            MemberView: The updated membership.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        member = await self.repository.get_member(area.id, member_id)
        if member is None:
            raise NotFoundError("Miembro no encontrado")
        role = await self._get_role(area.id, role_id)
        member.role_id = role.id
        member = await self.repository.save_member(member)
        users = await self.users.get_users([member.user_id])
        return MemberView(
            id=member.id,
            user=_user_ref(users[member.user_id]),
            role=RoleRef(id=role.id, name=role.name),
            created_at=member.created_at,
            updated_at=member.updated_at,
        )

    async def remove_member(self, slug: str, member_id: UUID, principal: Principal) -> None:
        """Remove a person from an area.

        Args:
            slug (str): Area slug.
            member_id (UUID): The membership.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            None
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        member = await self.repository.get_member(area.id, member_id)
        if member is None:
            raise NotFoundError("Miembro no encontrado")
        await self.repository.delete_member(member.id)
