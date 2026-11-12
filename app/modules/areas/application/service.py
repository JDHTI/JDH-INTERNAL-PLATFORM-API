"""Area use cases: catalog, roles, members and access to an area's space.

Rules:
- Platform administrators manage areas, roles and members, and see every area.
- Everyone else sees only the areas where they are a member or the responsible.
- A member's role must come from the catalog of the same area.
- An area that still has document folders cannot be deleted; neither can a
  role that members hold.
- Every area has one leader role ("Líder de área"): it holds every permission of the
  area, is never deleted and its permissions never change; it can be renamed. The
  other roles are free: any module, at read or manage, or the whole area.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.areas.application.dto import (
    AreaGrantsView,
    AreaModulesView,
    AreaView,
    CreateAreaCommand,
    MemberView,
    ModuleStateView,
    RoleRef,
    UpdateAreaCommand,
    UserRef,
)
from app.modules.areas.domain.catalog import (
    MANAGE,
    MODULES,
    READ,
    WILDCARD,
    Module,
    clean_module_config,
    get_module,
    is_enabled,
    module_config,
    modules_for,
    validate_grant,
)
from app.modules.areas.domain.entities import (
    AccessProfile,
    Area,
    AreaGrants,
    AreaRole,
    PermissionEvent,
    RolePermission,
    clean_name,
    validate_slug,
)
from app.modules.areas.domain.ports import AreaRepository, DirectoryUser, UserDirectory
from app.modules.identity.domain.principal import Principal

LEADER_ROLE = "Líder de área"
LEADER_GRANTS = (RolePermission(WILDCARD, MANAGE),)


def _sorted_grants(grants: dict[str, str]) -> list[RolePermission]:
    """The grants to show, wildcard first.

    A stored grant on a module that left the catalog is skipped: it grants nothing, and
    the panel would send it back on save, where ``validate_grant`` rejects it. Saving the
    role drops its row.
    """

    return [
        RolePermission(module, action)
        for module, action in sorted(grants.items(), key=lambda item: (item[0] != WILDCARD, item))
        if module == WILDCARD or get_module(module) is not None
    ]


def _role_ref(role: AreaRole) -> RoleRef:
    return RoleRef(id=role.id, name=role.name, is_leader=role.is_leader)


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

    @staticmethod
    def _require_admin_or_moderator(principal: Principal) -> None:
        if not (principal.is_platform_admin or principal.is_moderator):
            raise ForbiddenError("Solo un administrador o un moderador puede ver esto")

    @staticmethod
    def _require_active_user(user: DirectoryUser) -> DirectoryUser:
        if not user.is_active:
            raise ConflictError("No se puede asignar un usuario inactivo")
        return user

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
        profile: AccessProfile | None = None,
    ) -> list[AreaView]:
        rows = list(rows)
        responsibles = await self.users.get_users(
            area.responsible_user_id for area, _ in rows if area.responsible_user_id
        )
        profile = profile or await self.profile(principal)
        return [
            AreaView(
                id=area.id,
                slug=area.slug,
                name=area.name,
                remit=area.remit,
                icon=area.icon,
                sort_order=area.sort_order,
                responsible=_user_ref(responsibles.get(area.responsible_user_id)),
                my_role=_role_ref(role) if role else None,
                is_responsible=area.responsible_user_id == principal.user_id,
                created_at=area.created_at,
                updated_at=area.updated_at,
                my_modules=profile.modules(area.slug),
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

    # --- Access (also used by the documents module and every permission policy) ---

    async def profile(self, principal: Principal) -> AccessProfile:
        """Everything the principal may do in the workspace.

        Args:
            principal (Principal): The caller.

        Returns:
            AccessProfile: Global role plus, per area, responsibility and role grants.
        """

        rows = await self.repository.list_areas_for_user(principal.user_id)
        grants = await self.repository.get_role_permissions(role.id for _, role in rows if role)
        return AccessProfile(
            role=principal.role,
            overrides=await self.repository.list_module_overrides(),
            areas={
                area.slug: AreaGrants(
                    area_id=area.id,
                    slug=area.slug,
                    is_responsible=area.responsible_user_id == principal.user_id,
                    role_id=role.id if role else None,
                    grants=grants.get(role.id, {}) if role else {},
                    is_leader=bool(role and role.is_leader),
                )
                for area, role in rows
            },
        )

    async def allows(
        self, principal: Principal, area_slug: str | None, module: str, action: str = READ
    ) -> bool:
        """Whether the principal may do ``action`` on ``module``.

        Args:
            principal (Principal): The caller.
            area_slug (str | None): The area; ``None`` means any area of the module.
            module (str): Module key of the catalog.
            action (str): ``read`` or ``manage``.

        Returns:
            bool: ``True`` when allowed.
        """

        if principal.is_platform_admin:
            return True
        if principal.is_moderator:
            return action == READ
        return (await self.profile(principal)).allows(area_slug, module, action)

    async def require_permission(
        self, principal: Principal, area_slug: str | None, module: str, action: str = READ
    ) -> None:
        """Raise unless the principal may do ``action`` on ``module``.

        Raises:
            ForbiddenError: When not allowed.
        """

        if not await self.allows(principal, area_slug, module, action):
            verb = "gestionar" if action == MANAGE else "consultar"
            where = f" en {area_slug}" if area_slug else ""
            raise ForbiddenError(f"No tienes permiso para {verb} {module}{where}")

    async def authorize(
        self, slug: str, principal: Principal, module: str | None = None, action: str = READ
    ) -> Area:
        """Return the area when the principal may work in its space.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.
            module (str | None): Module of the area being used; ``None`` only asks
                whether the caller belongs to the area.
            action (str): ``read`` or ``manage`` on that module.

        Returns:
            Area: The area.
        """

        return await self._authorize(await self._get_area(slug), principal, module, action)

    async def authorize_by_id(
        self, area_id: UUID, principal: Principal, module: str | None = None, action: str = READ
    ) -> Area:
        """Return the area by identifier when the principal may work in its space.

        Args:
            area_id (UUID): Area identifier.
            principal (Principal): The caller.
            module (str | None): Module of the area being used.
            action (str): ``read`` or ``manage`` on that module.

        Returns:
            Area: The area.
        """

        area = await self.repository.get_by_id(area_id)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return await self._authorize(area, principal, module, action)

    async def _authorize(
        self, area: Area, principal: Principal, module: str | None, action: str
    ) -> Area:
        if module is not None:
            await self.require_permission(principal, area.slug, module, action)
            return area
        if principal.is_platform_admin or area.responsible_user_id == principal.user_id:
            return area
        if principal.is_moderator and action == READ:
            return area
        if await self.repository.get_member_by_user(area.id, principal.user_id) is None:
            raise ForbiddenError("No perteneces a esta área")
        return area

    async def accessible_area_ids(
        self, principal: Principal, module: str | None = None, action: str = READ
    ) -> list[UUID] | None:
        """List the areas whose space (or one module of it) the principal may use.

        Args:
            principal (Principal): The caller.
            module (str | None): Only areas where the caller may use this module.
            action (str): ``read`` or ``manage`` on that module.

        Returns:
            list[UUID] | None: Area identifiers, or ``None`` meaning every area
                (platform administrators, and moderators to read).
        """

        if principal.is_platform_admin or (principal.is_moderator and action == READ):
            return None
        profile = await self.profile(principal)
        if module is None:
            return [grants.area_id for grants in profile.areas.values()]
        return [
            grants.area_id
            for slug, grants in profile.areas.items()
            if profile.allows(slug, module, action)
        ]

    async def module_configs(
        self, principal: Principal, module: str, action: str = READ
    ) -> dict[str, dict[str, Any]] | None:
        """The settings of a module in each area where the principal may use it.

        Args:
            principal (Principal): The caller.
            module (str): Module key.
            action (str): ``read`` or ``manage``.

        Returns:
            dict[str, dict[str, Any]] | None: Area slug -> settings; ``None`` means
                every area (platform administrators, and moderators to read).
        """

        if principal.is_platform_admin or (principal.is_moderator and action == READ):
            return None
        return (await self.profile(principal)).configs(module, action)

    async def area_module_config(self, slug: str, module: str) -> dict[str, Any] | None:
        """The settings of a module in one area, or ``None`` when it is off there.

        Args:
            slug (str): Area slug.
            module (str): Module key.

        Returns:
            dict[str, Any] | None: The settings, or ``None``.
        """

        area = await self._get_area(slug)
        overrides = await self.repository.list_module_overrides()
        if not is_enabled(area.slug, module, overrides):
            return None
        return dict(module_config(area.slug, module, overrides))

    # --- Modules per area ---

    async def grantable_modules(self) -> list[tuple[Area, list[Module]]]:
        """Every area with the modules switched on in it (what its roles can grant)."""

        overrides = await self.repository.list_module_overrides()
        return [
            (area, modules_for(area.slug, overrides)) for area in await self.repository.list_areas()
        ]

    async def area_modules(self, principal: Principal) -> list[AreaModulesView]:
        """Every area with the state of every module, for the administration panel.

        Args:
            principal (Principal): An administrator or a moderator.

        Returns:
            list[AreaModulesView]: Areas in navigation order.
        """

        self._require_admin_or_moderator(principal)
        overrides = await self.repository.list_module_overrides()
        return [
            AreaModulesView(
                area_id=area.id,
                slug=area.slug,
                name=area.name,
                modules={
                    module.key: ModuleStateView(
                        is_enabled=is_enabled(area.slug, module.key, overrides),
                        config=dict(module_config(area.slug, module.key, overrides)),
                        is_default=module.key not in overrides.get(area.slug, {}),
                    )
                    for module in MODULES
                },
            )
            for area in await self.repository.list_areas()
        ]

    async def set_area_module(
        self,
        slug: str,
        module: str,
        is_enabled_: bool,
        config: dict[str, Any] | None,
        principal: Principal,
    ) -> ModuleStateView:
        """Switch a module on or off in an area and store its settings.

        Turning a module off keeps the role grants that mention it: they stop counting
        and come back when it is switched on again.

        Args:
            slug (str): Area slug.
            module (str): Module key of the catalog.
            is_enabled_ (bool): On or off.
            config (dict[str, Any] | None): Settings (``maestros``: which masters).
            principal (Principal): A platform administrator.

        Returns:
            ModuleStateView: The new state.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        found = get_module(module)
        if found is None:
            raise NotFoundError(f"El módulo {module} no existe")
        if is_enabled_ and not found.allowed_in(area.slug):
            raise InvalidInputError(
                f"El módulo {found.label} está bloqueado: solo se activa en "
                f"{', '.join(found.area_slugs)}"
            )
        cleaned = clean_module_config(module, config)
        overrides = await self.repository.list_module_overrides()
        before = {
            "enabled": is_enabled(area.slug, module, overrides),
            "config": dict(module_config(area.slug, module, overrides)),
        }
        await self.repository.set_area_module(area.id, module, is_enabled_, cleaned)
        await self._event(
            "module_toggled",
            principal,
            area_id=area.id,
            detail={
                "module": module,
                "before": before,
                "after": {"enabled": is_enabled_, "config": cleaned},
            },
        )
        return ModuleStateView(is_enabled=is_enabled_, config=cleaned, is_default=False)

    async def reset_area_module(
        self, slug: str, module: str, principal: Principal
    ) -> ModuleStateView:
        """Forget an area's choice so the module follows the catalog default again.

        Args:
            slug (str): Area slug.
            module (str): Module key of the catalog.
            principal (Principal): A platform administrator.

        Returns:
            ModuleStateView: The default state now in force.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        if get_module(module) is None:
            raise NotFoundError(f"El módulo {module} no existe")
        overrides = await self.repository.list_module_overrides()
        default = ModuleStateView(
            is_enabled=is_enabled(area.slug, module), config={}, is_default=True
        )
        if module not in overrides.get(area.slug, {}):
            return default
        before = {
            "enabled": is_enabled(area.slug, module, overrides),
            "config": dict(module_config(area.slug, module, overrides)),
        }
        await self.repository.delete_area_module(area.id, module)
        await self._event(
            "module_toggled",
            principal,
            area_id=area.id,
            detail={
                "module": module,
                "before": before,
                "after": {"enabled": default.is_enabled, "config": {}},
                "reset": True,
            },
        )
        return default

    # --- Role permissions and the log ---

    async def all_areas(self) -> list[Area]:
        """Every area in navigation order (the catalog of the permissions panel)."""

        return await self.repository.list_areas()

    async def get_role_permissions(
        self, slug: str, role_id: UUID, principal: Principal
    ) -> tuple[AreaRole, list[RolePermission]]:
        """The grants of an area role.

        Args:
            slug (str): Area slug.
            role_id (UUID): The role.
            principal (Principal): An administrator or a moderator.

        Returns:
            tuple[AreaRole, list[RolePermission]]: The role and its grants.
        """

        self._require_admin_or_moderator(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        grants = (await self.repository.get_role_permissions([role.id])).get(role.id, {})
        return role, _sorted_grants(grants)

    async def set_role_permissions(
        self,
        slug: str,
        role_id: UUID,
        grants: Iterable[tuple[str, str]],
        principal: Principal,
    ) -> tuple[AreaRole, list[RolePermission]]:
        """Replace the grants of an area role.

        Args:
            slug (str): Area slug.
            role_id (UUID): The role.
            grants (Iterable[tuple[str, str]]): ``(module, action)`` pairs; ``*`` is the
                whole area. A module listed twice keeps its strongest action.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            tuple[AreaRole, list[RolePermission]]: The role and its new grants.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        if role.is_leader:
            raise ConflictError(
                "El rol líder tiene todos los permisos del área: no se pueden modificar"
            )
        cleaned: dict[str, str] = {}
        overrides = await self.repository.list_module_overrides()
        for module, action in grants:
            module, action = validate_grant(area.slug, module, action, overrides)
            if cleaned.get(module) != MANAGE:
                cleaned[module] = action
        before = (await self.repository.get_role_permissions([role.id])).get(role.id, {})
        await self.repository.replace_role_permissions(
            role.id, [RolePermission(module, action) for module, action in cleaned.items()]
        )
        await self._event(
            "role_permissions_set",
            principal,
            area_id=area.id,
            role_id=role.id,
            detail={"role": role.name, "before": before, "after": cleaned},
        )
        return role, _sorted_grants(cleaned)

    async def user_permissions(self, user_id: UUID, principal: Principal) -> list[AreaGrantsView]:
        """Areas, roles and grants of one person, for the permissions panel.

        Args:
            user_id (UUID): The person.
            principal (Principal): An administrator or a moderator.

        Returns:
            list[AreaGrantsView]: One entry per area where the person is a member or
                the responsible.
        """

        self._require_admin_or_moderator(principal)
        rows = await self.repository.list_areas_for_user(user_id)
        grants = await self.repository.get_role_permissions(role.id for _, role in rows if role)
        return [
            AreaGrantsView(
                area_id=area.id,
                slug=area.slug,
                name=area.name,
                is_responsible=area.responsible_user_id == user_id,
                role=_role_ref(role) if role else None,
                grants=_sorted_grants(grants.get(role.id, {}) if role else {}),
            )
            for area, role in rows
        ]

    async def list_events(
        self,
        principal: Principal,
        *,
        area_slug: str | None = None,
        target_user_id: UUID | None = None,
        action: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PermissionEvent], int]:
        """The permissions log, newest first.

        Args:
            principal (Principal): An administrator or a moderator.
            area_slug (str | None): Only this area.
            target_user_id (UUID | None): Only this person.
            action (str | None): Only this kind of change.
            offset (int): Rows to skip.
            limit (int): 1 to 100 rows.

        Returns:
            tuple[list[PermissionEvent], int]: The page and the total.
        """

        self._require_admin_or_moderator(principal)
        if not 1 <= limit <= 100:
            raise InvalidInputError("El límite debe estar entre 1 y 100")
        area_id = (await self._get_area(area_slug)).id if area_slug else None
        return await self.repository.list_events(
            area_id=area_id,
            target_user_id=target_user_id,
            action=action,
            offset=offset,
            limit=limit,
        )

    async def record_global_event(
        self,
        action: str,
        principal: Principal | None,
        *,
        target_user_id: UUID | None,
        detail: dict | None = None,
    ) -> None:
        """Log a change made outside the areas (a global role, a deactivation).

        Args:
            action (str): What happened.
            principal (Principal | None): Who did it (``None`` for a system job).
            target_user_id (UUID | None): Person affected.
            detail (dict | None): Before/after values.
        """

        await self._event(action, principal, target_user_id=target_user_id, detail=detail)

    async def _event(self, action: str, principal: Principal | None, **fields) -> None:
        await self.repository.record_event(
            action, actor_user_id=principal.user_id if principal else None, **fields
        )

    # --- Areas ---

    async def list_areas(self, principal: Principal) -> list[AreaView]:
        """List the areas visible to the principal, in navigation order.

        Args:
            principal (Principal): The caller.

        Returns:
            list[AreaView]: Every area for administrators and moderators; otherwise
                those where the caller is a member or the responsible.
        """

        mine = await self.repository.list_areas_for_user(principal.user_id)
        profile = await self.profile(principal)
        if not (principal.is_platform_admin or principal.is_moderator):
            return await self._views(mine, principal, profile)
        roles = {area.id: role for area, role in mine}
        areas = await self.repository.list_areas()
        return await self._views(((a, roles.get(a.id)) for a in areas), principal, profile)

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
                email and must be in the directory (no sign-in needed).
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            AreaView: The created area.
        """

        self._require_admin(principal)
        slug = validate_slug(command.slug.strip().lower())
        name = clean_name(command.name)
        await self._check_unique(slug, name)
        responsible = (
            self._require_active_user(await self.users.require_user(command.responsible_email))
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
        # Every area starts with its leader role, which grants it whole.
        role = await self.repository.create_role(area.id, LEADER_ROLE, is_leader=True)
        await self.repository.replace_role_permissions(role.id, list(LEADER_GRANTS))
        await self._event("area_created", principal, area_id=area.id, detail={"slug": slug})
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
                area.responsible_user_id = self._require_active_user(
                    await self.users.require_user(command.responsible_email)
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
        role = await self.repository.create_role(area.id, name)
        await self._event(
            "role_created", principal, area_id=area.id, role_id=role.id, detail={"name": name}
        )
        return role

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
        before = role.name
        role.name = clean_name(name, max_length=100)
        if await self.repository.role_name_taken(area.id, role.name, exclude_id=role.id):
            raise ConflictError("El área ya tiene un rol con ese nombre")
        saved = await self.repository.save_role(role)
        if saved.name != before:
            await self._event(
                "role_renamed",
                principal,
                area_id=area.id,
                role_id=role.id,
                detail={"before": before, "after": saved.name},
            )
        return saved

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
        if role.is_leader:
            raise ConflictError("El rol líder del área no se puede eliminar")
        if await self.repository.role_in_use(role.id):
            raise ConflictError("El rol tiene miembros asignados: reasígnalos antes de borrarlo")
        await self.repository.delete_role(role.id)
        await self._event(
            "role_deleted", principal, area_id=area.id, role_id=role.id, detail={"name": role.name}
        )

    # --- Members ---

    async def member_user_ids(self, slug: str) -> list[UUID]:
        """Identifiers of an area's members, for system jobs that have no caller.

        Args:
            slug (str): Area slug.

        Returns:
            list[UUID]: Member user identifiers (not filtered by active status).
        """

        area = await self._get_area(slug)
        return [member.user_id for member in await self.repository.list_members(area.id)]

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
                role=_role_ref(roles[m.role_id]),
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in members
            if m.user_id in users and m.role_id in roles
        ]
        return sorted(views, key=lambda v: (v.role.name, v.user.display_name or v.user.email or ""))

    async def add_member(
        self, slug: str, email: str, role_id: UUID, principal: Principal
    ) -> MemberView:
        """Assign a person to an area with one of its roles.

        Args:
            slug (str): Area slug.
            email (str): Person's email. The directory sync creates every Entra
                account as a user, so someone who never signed in can be assigned;
                their first sign-in reuses that user and keeps the membership.
            role_id (UUID): A role of the same area.
            principal (Principal): The caller; must be a platform administrator.

        Returns:
            MemberView: The created membership.
        """

        self._require_admin(principal)
        area = await self._get_area(slug)
        role = await self._get_role(area.id, role_id)
        user = self._require_active_user(await self.users.require_user(email))
        if await self.repository.get_member_by_user(area.id, user.id):
            raise ConflictError("La persona ya es miembro del área")
        member = await self.repository.create_member(area.id, user.id, role.id)
        await self._event(
            "member_added",
            principal,
            area_id=area.id,
            target_user_id=user.id,
            role_id=role.id,
            detail={"role": role.name},
        )
        return MemberView(
            id=member.id,
            user=_user_ref(user),
            role=_role_ref(role),
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
        previous_role_id = member.role_id
        member.role_id = role.id
        member = await self.repository.save_member(member)
        await self._event(
            "member_role_changed",
            principal,
            area_id=area.id,
            target_user_id=member.user_id,
            role_id=role.id,
            detail={"role": role.name, "previous_role_id": str(previous_role_id)},
        )
        users = await self.users.get_users([member.user_id])
        return MemberView(
            id=member.id,
            user=_user_ref(users[member.user_id]),
            role=_role_ref(role),
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
        await self._event(
            "member_removed",
            principal,
            area_id=area.id,
            target_user_id=member.user_id,
            role_id=member.role_id,
        )
