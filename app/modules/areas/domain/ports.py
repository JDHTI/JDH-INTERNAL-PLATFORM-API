"""Contracts required by the area use cases."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from app.modules.areas.domain.catalog import AreaModule
from app.modules.areas.domain.entities import (
    Area,
    AreaMember,
    AreaRole,
    PermissionEvent,
    RolePermission,
)


class DirectoryUser(Protocol):
    """The part of a platform user this module reads."""

    id: UUID
    email: str | None
    display_name: str | None
    is_active: bool


class UserDirectory(Protocol):
    """Look users up without reading the identity module's tables."""

    async def require_user(self, email: str) -> DirectoryUser:
        """Return the user with this email.

        Args:
            email (str): Email address of the person.

        Returns:
            DirectoryUser: The existing user.

        Raises:
            NotFoundError: When nobody has that email in the directory or the platform.
        """
        ...

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, DirectoryUser]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Identifiers to load.

        Returns:
            dict[UUID, DirectoryUser]: Found users keyed by identifier.
        """
        ...


class AreaRepository(Protocol):
    """Define persistence operations for areas, roles and members."""

    async def list_module_overrides(self) -> dict[str, dict[str, AreaModule]]:
        """Every stored module choice (``area_modules``).

        Returns:
            dict[str, dict[str, AreaModule]]: Area slug -> module key -> choice.
        """
        ...

    async def set_area_module(
        self, area_id: UUID, module: str, is_enabled: bool, config: dict
    ) -> None:
        """Store whether a module is on in an area, and its settings.

        Args:
            area_id (UUID): The area.
            module (str): Module key of the catalog.
            is_enabled (bool): On or off.
            config (dict): The module's settings in the area.
        """
        ...

    async def delete_area_module(self, area_id: UUID, module: str) -> None:
        """Forget the choice so the module follows the catalog default again.

        Args:
            area_id (UUID): The area.
            module (str): Module key of the catalog.
        """
        ...

    async def get_role_permissions(self, role_ids: Iterable[UUID]) -> dict[UUID, dict[str, str]]:
        """The grants of several area roles.

        Args:
            role_ids (Iterable[UUID]): Roles to load.

        Returns:
            dict[UUID, dict[str, str]]: Per role, module (or ``*``) -> action.
        """
        ...

    async def replace_role_permissions(self, role_id: UUID, grants: list[RolePermission]) -> None:
        """Replace every grant of a role.

        Args:
            role_id (UUID): The role.
            grants (list[RolePermission]): The new grants, one per module.
        """
        ...

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
        """Append an entry to the permissions log."""
        ...

    async def list_events(
        self,
        *,
        area_id: UUID | None,
        target_user_id: UUID | None,
        action: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[PermissionEvent], int]:
        """A page of the permissions log, newest first, and the total."""
        ...

    async def list_areas(self) -> list[Area]:
        """List every area in display order.

        Returns:
            list[Area]: All areas, ordered by ``sort_order`` then name.
        """
        ...

    async def list_areas_for_user(self, user_id: UUID) -> list[tuple[Area, AreaRole | None]]:
        """List the areas where a user is a member or the responsible.

        Args:
            user_id (UUID): The user.

        Returns:
            list[tuple[Area, AreaRole | None]]: Each area with the user's role
                there, or ``None`` when they are only its responsible.
        """
        ...

    async def get_by_slug(self, slug: str) -> Area | None:
        """Find an area by its URL slug.

        Args:
            slug (str): Area slug.

        Returns:
            Area | None: The area when it exists.
        """
        ...

    async def get_by_id(self, area_id: UUID) -> Area | None:
        """Find an area by identifier.

        Args:
            area_id (UUID): Area identifier.

        Returns:
            Area | None: The area when it exists.
        """
        ...

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
        ...

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
        ...

    async def save_area(self, area: Area) -> Area:
        """Persist changes to an existing area.

        Args:
            area (Area): Area with updated fields.

        Returns:
            Area: The stored area.
        """
        ...

    async def delete_area(self, area_id: UUID) -> None:
        """Delete an area with its roles and members.

        Args:
            area_id (UUID): Area to delete. Fails with a conflict while the
                area still owns document folders.

        Returns:
            None
        """
        ...

    async def list_roles(self, area_id: UUID) -> list[AreaRole]:
        """List the role catalog of an area.

        Args:
            area_id (UUID): The area.

        Returns:
            list[AreaRole]: Roles ordered by name.
        """
        ...

    async def get_role(self, area_id: UUID, role_id: UUID) -> AreaRole | None:
        """Find a role inside a given area.

        Args:
            area_id (UUID): Area that must own the role.
            role_id (UUID): The role.

        Returns:
            AreaRole | None: The role when it belongs to the area.
        """
        ...

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
        ...

    async def create_role(self, area_id: UUID, name: str, is_leader: bool = False) -> AreaRole:
        """Persist a new role in an area.

        Args:
            area_id (UUID): Owning area.
            name (str): Role name.
            is_leader (bool): Whether it is the area's leader role (one per area).

        Returns:
            AreaRole: The stored role.
        """
        ...

    async def save_role(self, role: AreaRole) -> AreaRole:
        """Persist changes to a role.

        Args:
            role (AreaRole): Role with updated fields.

        Returns:
            AreaRole: The stored role.
        """
        ...

    async def delete_role(self, role_id: UUID) -> None:
        """Delete a role.

        Args:
            role_id (UUID): Role to delete.

        Returns:
            None
        """
        ...

    async def role_in_use(self, role_id: UUID) -> bool:
        """Check whether any member holds the role.

        Args:
            role_id (UUID): The role.

        Returns:
            bool: ``True`` when at least one member has it.
        """
        ...

    async def list_members(self, area_id: UUID) -> list[AreaMember]:
        """List the members of an area.

        Args:
            area_id (UUID): The area.

        Returns:
            list[AreaMember]: Members in insertion order.
        """
        ...

    async def get_member(self, area_id: UUID, member_id: UUID) -> AreaMember | None:
        """Find a membership inside a given area.

        Args:
            area_id (UUID): Area that must own the membership.
            member_id (UUID): The membership.

        Returns:
            AreaMember | None: The membership when it belongs to the area.
        """
        ...

    async def get_member_by_user(self, area_id: UUID, user_id: UUID) -> AreaMember | None:
        """Find a user's membership in an area.

        Args:
            area_id (UUID): The area.
            user_id (UUID): The user.

        Returns:
            AreaMember | None: The membership when the user belongs to the area.
        """
        ...

    async def create_member(self, area_id: UUID, user_id: UUID, role_id: UUID) -> AreaMember:
        """Persist a membership.

        Args:
            area_id (UUID): The area.
            user_id (UUID): The user.
            role_id (UUID): A role of the same area.

        Returns:
            AreaMember: The stored membership.
        """
        ...

    async def save_member(self, member: AreaMember) -> AreaMember:
        """Persist changes to a membership.

        Args:
            member (AreaMember): Membership with updated fields.

        Returns:
            AreaMember: The stored membership.
        """
        ...

    async def delete_member(self, member_id: UUID) -> None:
        """Delete a membership.

        Args:
            member_id (UUID): Membership to delete.

        Returns:
            None
        """
        ...
