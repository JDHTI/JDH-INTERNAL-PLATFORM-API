"""Project management use cases, segmented by area like the document manager.

Rules:
- The module is switched on per area from the administration panel (``area_modules``;
  by default only TI). Inside an enabled area, the role grants decide who may enter.
- Every member can create, edit, move, archive and restore. Permanent deletes
  are for the creator, the area responsible and platform administrators.
- Concurrent edits are last-write-wins. Each write raises the project's
  ``version`` so clients can discard events older than what they already show.
- After every committed change an event goes to the area's live channel. A
  failed publish is logged and never undoes the change: clients resync on
  reconnect.
- A folder can only be deleted when it holds no projects, archived included.
- Inside a project, epics only group tasks (an epic has no status and a task
  may have none). Tasks move between statuses: ``backlog`` is a separate board,
  and ``in_progress``, ``pending_validation``, ``blocked`` and ``done`` are the
  main board columns. Leaving the backlog is the same task changing status.
  ``blocked_reason`` is kept only while the task is blocked. An epic can only
  be deleted when empty; deleting a project takes its epics and tasks with it.
  Any member may delete a task.
- Projects and epics show task counts. When a task write changes them, the
  project's and epics' versions rise too and ``project.updated`` /
  ``epic.updated`` go out, so clients keep the counts current.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.core.logging import logger
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.projects.application.dto import (
    CreateEpicCommand,
    CreateProjectCommand,
    CreateTaskCommand,
    EpicView,
    FolderCommand,
    FolderView,
    ProjectView,
    TaskView,
    UpdateEpicCommand,
    UpdateProjectCommand,
    UpdateTaskCommand,
    actor_payload,
    epic_payload,
    folder_payload,
    iso,
    project_payload,
    task_payload,
)
from app.modules.projects.domain.entities import (
    BLOCKED_TASK_STATUS,
    DEFAULT_COLOR,
    DEFAULT_PRIORITY,
    DEFAULT_STATUS,
    DEFAULT_TASK_STATUS,
    DONE_TASK_STATUS,
    Epic,
    Project,
    ProjectFolder,
    Task,
    TaskCounts,
    check_dates,
    check_position,
    clean_blocked_reason,
    clean_text,
    normalize_color,
    normalize_priority,
    normalize_status,
    normalize_task_status,
    optional_text,
)
from app.modules.projects.domain.ports import (
    AreaAccess,
    AreaRef,
    People,
    ProjectEventPublisher,
    ProjectFilters,
    ProjectRepository,
)

MODULE = "proyectos"
_DUPLICATE_FOLDER = "Ya existe una carpeta con ese nombre en el área"
_REQUIRED_PROJECT_FIELDS = ("name", "status", "priority", "color", "start_date", "folder_id")
_REQUIRED_EPIC_FIELDS = ("name", "color", "position")
_REQUIRED_TASK_FIELDS = ("name", "status", "priority", "position")
_COUNT_FIELDS = ("done_count", "task_count")


class ProjectService:
    """Coordinate project folders and projects inside the enabled areas."""

    def __init__(
        self,
        repository: ProjectRepository,
        areas: AreaAccess,
        people: People,
        events: ProjectEventPublisher,
    ) -> None:
        """Create the service with its ports.

        Args:
            repository (ProjectRepository): Port that stores folders and projects.
            areas (AreaAccess): Port that authorizes access to an area and says where
                the module is switched on.
            people (People): Port that resolves project owners.
            events (ProjectEventPublisher): Port that fans changes out live.
        """

        self.repository = repository
        self.areas = areas
        self.people = people
        self.events = events

    # --- Helpers ---

    async def authorize(
        self, area_slug: str, principal: Principal, action: str = "read"
    ) -> AreaRef:
        """Return the area when the module is on there and the caller holds ``action``.

        Args:
            area_slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            AreaRef: The authorized area.
        """

        slug = area_slug.strip().lower()
        if await self.areas.area_module_config(slug, MODULE) is None:
            raise NotFoundError("Proyectos no está habilitado para esta área")
        return await self.areas.authorize(slug, principal, MODULE, action)

    async def can_delete_any(self, area_slug: str, principal: Principal) -> bool:
        """Whether the caller may delete folders and projects created by others.

        Everyone may delete what they created; the UI combines this flag with
        ``created_by`` to decide per item.

        Args:
            area_slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            bool: ``True`` for the area responsible and platform administrators.
        """

        area = await self.authorize(area_slug, principal)
        return principal.is_platform_admin or area.responsible_user_id == principal.user_id

    async def search_people(
        self, area_slug: str, query: str, principal: Principal, limit: int = 20
    ) -> list[DirectoryUser]:
        """Find active users who can be made responsible for a project.

        Args:
            area_slug (str): Area slug; the caller must be able to enter it.
            query (str): Text in the name or email; blank lists the first ones.
            principal (Principal): The caller.
            limit (int): Maximum number of results.

        Returns:
            list[DirectoryUser]: Matching active users ordered by name.
        """

        await self.authorize(area_slug, principal)
        return await self.people.search_users(query.strip(), limit)

    async def _folder(self, area_id: UUID, folder_id: UUID) -> ProjectFolder:
        folder = await self.repository.get_folder(area_id, folder_id)
        if folder is None:
            raise NotFoundError("Carpeta no encontrada en esta área")
        return folder

    async def _project(self, area_id: UUID, project_id: UUID) -> Project:
        project = await self.repository.get_project(area_id, project_id)
        if project is None:
            raise NotFoundError("Proyecto no encontrado en esta área")
        return project

    @staticmethod
    def _require_manager(area: AreaRef, created_by: str, principal: Principal) -> None:
        if principal.is_platform_admin or area.responsible_user_id == principal.user_id:
            return
        if created_by.lower() == principal.email.lower():
            return
        raise ForbiddenError("Solo quien lo creó, el responsable del área o un administrador")

    async def _owner(self, owner_user_id: UUID | None) -> DirectoryUser | None:
        if owner_user_id is None:
            return None
        user = (await self.people.get_users([owner_user_id])).get(owner_user_id)
        if user is None or not user.is_active:
            raise InvalidInputError("El responsable debe ser un usuario activo de la plataforma")
        return user

    async def _views(self, projects: list[Project]) -> list[ProjectView]:
        owners = await self._owners(p.owner_user_id for p in projects)
        counts = (
            await self.repository.project_task_counts([p.id for p in projects]) if projects else {}
        )
        return [
            ProjectView(p, owners.get(p.owner_user_id), counts.get(p.id, TaskCounts()))
            for p in projects
        ]

    async def _owners(self, ids: Iterable[UUID | None]) -> dict[UUID, DirectoryUser]:
        wanted = {i for i in ids if i}
        return await self.people.get_users(wanted) if wanted else {}

    async def _view(self, project: Project) -> ProjectView:
        return (await self._views([project]))[0]

    async def _publish(
        self,
        area_id: UUID,
        event_type: str,
        data: dict[str, Any],
        principal: Principal,
        changed: Iterable[str] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": event_type,
            "area_id": str(area_id),
            "data": data,
            "actor": actor_payload(principal),
            "at": iso(datetime.now(UTC)),
        }
        if changed is not None:
            event["changed"] = sorted(changed)
        try:
            await self.events.publish(area_id, event)
        except Exception as exc:  # noqa: BLE001 - the change is committed; clients resync
            logger.error("Could not publish %s for area %s: %s", event_type, area_id, exc)

    # --- Folders ---

    async def list_folders(self, area_slug: str, principal: Principal) -> list[FolderView]:
        """List the folders of an area with their active project counts.

        Args:
            area_slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            list[FolderView]: Folders ordered by name.
        """

        area = await self.authorize(area_slug, principal)
        return [FolderView(f, n) for f, n in await self.repository.list_folders(area.id)]

    async def create_folder(
        self, area_slug: str, command: FolderCommand, principal: Principal
    ) -> FolderView:
        """Create a folder in an area.

        Args:
            area_slug (str): Area slug.
            command (FolderCommand): Name (required), description and color.
            principal (Principal): The caller.

        Returns:
            FolderView: The created, empty folder.
        """

        area = await self.authorize(area_slug, principal, "manage")
        name = clean_text(command.name or "", "nombre", 150)
        if await self.repository.folder_name_taken(area.id, name):
            raise ConflictError(_DUPLICATE_FOLDER)
        folder = await self.repository.create_folder(
            area.id,
            name,
            optional_text(command.description, "descripción", 1000),
            normalize_color(command.color) if command.color else DEFAULT_COLOR,
            principal.email,
        )
        view = FolderView(folder, 0)
        await self._publish(area.id, "folder.created", folder_payload(view), principal)
        return view

    async def update_folder(
        self, area_slug: str, folder_id: UUID, command: FolderCommand, principal: Principal
    ) -> FolderView:
        """Rename a folder or change its description or color.

        Args:
            area_slug (str): Area slug.
            folder_id (UUID): The folder.
            command (FolderCommand): Fields to change, listed in ``fields``.
            principal (Principal): The caller.

        Returns:
            FolderView: The updated folder.
        """

        area = await self.authorize(area_slug, principal, "manage")
        folder = await self._folder(area.id, folder_id)
        if "name" in command.fields:
            folder.name = clean_text(command.name or "", "nombre", 150)
            if await self.repository.folder_name_taken(area.id, folder.name, folder.id):
                raise ConflictError(_DUPLICATE_FOLDER)
        if "description" in command.fields:
            folder.description = optional_text(command.description, "descripción", 1000)
        if "color" in command.fields:
            folder.color = normalize_color(command.color or DEFAULT_COLOR)
        folder = await self.repository.save_folder(folder)
        count = await self.repository.count_folder_projects(folder.id, include_archived=False)
        view = FolderView(folder, count)
        await self._publish(
            area.id, "folder.updated", folder_payload(view), principal, changed=command.fields
        )
        return view

    async def delete_folder(self, area_slug: str, folder_id: UUID, principal: Principal) -> None:
        """Delete an empty folder.

        Args:
            area_slug (str): Area slug.
            folder_id (UUID): The folder.
            principal (Principal): The caller; creator, area responsible or admin.

        Returns:
            None
        """

        area = await self.authorize(area_slug, principal, "manage")
        folder = await self._folder(area.id, folder_id)
        self._require_manager(area, folder.created_by, principal)
        if await self.repository.count_folder_projects(folder.id, include_archived=True):
            raise ConflictError(
                "La carpeta tiene proyectos (también archivados): muévelos o elimínalos antes"
            )
        await self.repository.delete_folder(folder.id)
        await self._publish(area.id, "folder.deleted", {"id": str(folder.id)}, principal)

    # --- Projects ---

    async def list_projects(
        self, area_slug: str, filters: ProjectFilters, principal: Principal
    ) -> list[ProjectView]:
        """List the projects of an area.

        Args:
            area_slug (str): Area slug.
            filters (ProjectFilters): Optional folder, statuses and archived flag.
            principal (Principal): The caller.

        Returns:
            list[ProjectView]: Projects ordered by folder and position.
        """

        area = await self.authorize(area_slug, principal)
        if filters.folder_id is not None:
            await self._folder(area.id, filters.folder_id)
        filters = ProjectFilters(
            folder_id=filters.folder_id,
            statuses=tuple(normalize_status(s) for s in filters.statuses),
            include_archived=filters.include_archived,
        )
        return await self._views(await self.repository.list_projects(area.id, filters))

    async def get_project(
        self, area_slug: str, project_id: UUID, principal: Principal
    ) -> ProjectView:
        """Return one project of the area.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            principal (Principal): The caller.

        Returns:
            ProjectView: The project with its owner.
        """

        area = await self.authorize(area_slug, principal)
        return await self._view(await self._project(area.id, project_id))

    async def create_project(
        self, area_slug: str, command: CreateProjectCommand, principal: Principal
    ) -> ProjectView:
        """Create a project at the end of a folder.

        Args:
            area_slug (str): Area slug.
            command (CreateProjectCommand): Folder, name, start date and the
                optional attributes; status starts as ``created``.
            principal (Principal): The caller.

        Returns:
            ProjectView: The created project.
        """

        area = await self.authorize(area_slug, principal, "manage")
        folder = await self._folder(area.id, command.folder_id)
        check_dates(command.start_date, command.end_date)
        owner = await self._owner(command.owner_user_id)
        now = datetime.now(UTC)
        project = Project(
            id=uuid.uuid4(),
            area_id=area.id,
            folder_id=folder.id,
            name=clean_text(command.name, "nombre", 200),
            description=optional_text(command.description, "descripción", 5000),
            color=normalize_color(command.color) if command.color else DEFAULT_COLOR,
            status=normalize_status(command.status) if command.status else DEFAULT_STATUS,
            priority=(
                normalize_priority(command.priority) if command.priority else DEFAULT_PRIORITY
            ),
            start_date=command.start_date,
            end_date=command.end_date,
            owner_user_id=owner.id if owner else None,
            position=await self.repository.next_position(folder.id),
            is_archived=False,
            archived_at=None,
            version=1,
            created_by=principal.email,
            updated_by=principal.email,
            created_at=now,
            updated_at=now,
        )
        view = ProjectView(await self.repository.add_project(project), owner)
        await self._publish(area.id, "project.created", project_payload(view), principal)
        return view

    async def update_project(
        self,
        area_slug: str,
        project_id: UUID,
        command: UpdateProjectCommand,
        principal: Principal,
    ) -> ProjectView:
        """Change attributes of a project, move it or reorder it.

        Moving to another folder without a ``position`` places it last there.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            command (UpdateProjectCommand): Attributes listed in ``fields``.
            principal (Principal): The caller.

        Returns:
            ProjectView: The updated project.
        """

        area = await self.authorize(area_slug, principal, "manage")
        project = await self._project(area.id, project_id)
        fields = command.fields
        for name in _REQUIRED_PROJECT_FIELDS:
            if name in fields and getattr(command, name) is None:
                raise InvalidInputError(f"El campo {name} no puede quedar vacío")

        if "folder_id" in fields and command.folder_id != project.folder_id:
            project.folder_id = (await self._folder(area.id, command.folder_id)).id
            if "position" not in fields:
                project.position = await self.repository.next_position(project.folder_id)
        if "position" in fields:
            project.position = check_position(command.position or 0.0)
        if "name" in fields:
            project.name = clean_text(command.name, "nombre", 200)
        if "description" in fields:
            project.description = optional_text(command.description, "descripción", 5000)
        if "color" in fields:
            project.color = normalize_color(command.color)
        if "status" in fields:
            project.status = normalize_status(command.status)
        if "priority" in fields:
            project.priority = normalize_priority(command.priority)
        if "start_date" in fields:
            project.start_date = command.start_date
        if "end_date" in fields:
            project.end_date = command.end_date
        check_dates(project.start_date, project.end_date)
        if "owner_user_id" in fields:
            owner = await self._owner(command.owner_user_id)
            project.owner_user_id = owner.id if owner else None

        return await self._save(area, project, principal, fields)

    async def set_archived(
        self, area_slug: str, project_id: UUID, archived: bool, principal: Principal
    ) -> ProjectView:
        """Archive a project (hidden from default listings) or restore it.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            archived (bool): ``True`` archives, ``False`` restores.
            principal (Principal): The caller.

        Returns:
            ProjectView: The project in its new state.
        """

        area = await self.authorize(area_slug, principal, "manage")
        project = await self._project(area.id, project_id)
        if project.is_archived == archived:
            return await self._view(project)
        project.is_archived = archived
        project.archived_at = datetime.now(UTC) if archived else None
        return await self._save(area, project, principal, {"is_archived", "archived_at"})

    async def delete_project(self, area_slug: str, project_id: UUID, principal: Principal) -> None:
        """Delete a project permanently.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            principal (Principal): The caller; creator, area responsible or admin.

        Returns:
            None
        """

        area = await self.authorize(area_slug, principal, "manage")
        project = await self._project(area.id, project_id)
        self._require_manager(area, project.created_by, principal)
        await self.repository.delete_project(project.id)
        await self._publish(
            area.id,
            "project.deleted",
            {"id": str(project.id), "folder_id": str(project.folder_id)},
            principal,
        )

    async def _save(
        self, area: AreaRef, project: Project, principal: Principal, changed: Iterable[str]
    ) -> ProjectView:
        project.updated_by = principal.email
        view = await self._view(await self.repository.save_project(project))
        await self._publish(
            area.id, "project.updated", project_payload(view), principal, changed=changed
        )
        return view

    # --- epics ---

    async def _epic(self, area_id: UUID, epic_id: UUID) -> Epic:
        epic = await self.repository.get_epic(area_id, epic_id)
        if epic is None:
            raise NotFoundError("Épica no encontrada en esta área")
        return epic

    async def _epic_views(self, epics: list[Epic]) -> list[EpicView]:
        counts = await self.repository.epic_task_counts([b.id for b in epics]) if epics else {}
        return [EpicView(b, counts.get(b.id, TaskCounts())) for b in epics]

    async def _publish_counts(
        self, area: AreaRef, project_id: UUID, epic_ids: Iterable[UUID], principal: Principal
    ) -> None:
        """Send the new counts of a project and its epics after a task write."""

        project = await self.repository.get_project(area.id, project_id)
        if project is not None:
            view = await self._view(project)
            await self._publish(
                area.id, "project.updated", project_payload(view), principal, _COUNT_FIELDS
            )
        epics = [b for i in set(epic_ids) if (b := await self.repository.get_epic(area.id, i))]
        for epic_view in await self._epic_views(epics):
            await self._publish(
                area.id, "epic.updated", epic_payload(epic_view), principal, _COUNT_FIELDS
            )

    async def list_epics(
        self, area_slug: str, project_id: UUID, principal: Principal
    ) -> list[EpicView]:
        """List the epics of a project with their task counts.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            principal (Principal): The caller.

        Returns:
            list[EpicView]: epics ordered by position.
        """

        area = await self.authorize(area_slug, principal)
        project = await self._project(area.id, project_id)
        return await self._epic_views(await self.repository.list_epics(project.id))

    async def create_epic(
        self, area_slug: str, project_id: UUID, command: CreateEpicCommand, principal: Principal
    ) -> EpicView:
        """Create an epic at the end of a project.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            command (CreateEpicCommand): Name and the optional attributes.
            principal (Principal): The caller.

        Returns:
            EpicView: The created, empty epic.
        """

        area = await self.authorize(area_slug, principal, "manage")
        project = await self._project(area.id, project_id)
        check_dates(command.start_date, command.end_date)
        now = datetime.now(UTC)
        epic = Epic(
            id=uuid.uuid4(),
            area_id=area.id,
            project_id=project.id,
            name=clean_text(command.name, "nombre", 200),
            description=optional_text(command.description, "descripción", 5000),
            color=normalize_color(command.color) if command.color else DEFAULT_COLOR,
            start_date=command.start_date,
            end_date=command.end_date,
            position=await self.repository.next_epic_position(project.id),
            version=1,
            created_by=principal.email,
            updated_by=principal.email,
            created_at=now,
            updated_at=now,
        )
        view = EpicView(await self.repository.add_epic(epic))
        await self._publish(area.id, "epic.created", epic_payload(view), principal)
        return view

    async def update_epic(
        self, area_slug: str, epic_id: UUID, command: UpdateEpicCommand, principal: Principal
    ) -> EpicView:
        """Change attributes of an epic or reorder it inside its project.

        Args:
            area_slug (str): Area slug.
            epic_id (UUID): The epic.
            command (UpdateEpicCommand): Attributes listed in ``fields``.
            principal (Principal): The caller.

        Returns:
            EpicView: The updated epic.
        """

        area = await self.authorize(area_slug, principal, "manage")
        epic = await self._epic(area.id, epic_id)
        fields = command.fields
        for name in _REQUIRED_EPIC_FIELDS:
            if name in fields and getattr(command, name) is None:
                raise InvalidInputError(f"El campo {name} no puede quedar vacío")
        if "name" in fields:
            epic.name = clean_text(command.name, "nombre", 200)
        if "description" in fields:
            epic.description = optional_text(command.description, "descripción", 5000)
        if "color" in fields:
            epic.color = normalize_color(command.color)
        if "start_date" in fields:
            epic.start_date = command.start_date
        if "end_date" in fields:
            epic.end_date = command.end_date
        check_dates(epic.start_date, epic.end_date)
        if "position" in fields:
            epic.position = check_position(command.position)
        epic.updated_by = principal.email
        view = (await self._epic_views([await self.repository.save_epic(epic)]))[0]
        await self._publish(area.id, "epic.updated", epic_payload(view), principal, fields)
        return view

    async def delete_epic(self, area_slug: str, epic_id: UUID, principal: Principal) -> None:
        """Delete an empty epic.

        Args:
            area_slug (str): Area slug.
            epic_id (UUID): The epic.
            principal (Principal): The caller; creator, area responsible or admin.

        Returns:
            None
        """

        area = await self.authorize(area_slug, principal, "manage")
        epic = await self._epic(area.id, epic_id)
        self._require_manager(area, epic.created_by, principal)
        counts = await self.repository.epic_task_counts([epic.id])
        if epic.id in counts and counts[epic.id].total:
            raise ConflictError("La épica tiene tareas: muévelas o elimínalas antes")
        await self.repository.delete_epic(epic.id)
        await self._publish(
            area.id,
            "epic.deleted",
            {"id": str(epic.id), "project_id": str(epic.project_id)},
            principal,
        )

    # --- Tasks ---

    async def _task(self, area_id: UUID, task_id: UUID) -> Task:
        task = await self.repository.get_task(area_id, task_id)
        if task is None:
            raise NotFoundError("Tarea no encontrada en esta área")
        return task

    async def _task_views(self, tasks: list[Task]) -> list[TaskView]:
        owners = await self._owners(t.owner_user_id for t in tasks)
        return [TaskView(t, owners.get(t.owner_user_id)) for t in tasks]

    async def _project_epic(self, area_id: UUID, project_id: UUID, epic_id: UUID) -> Epic:
        epic = await self.repository.get_epic(area_id, epic_id)
        if epic is None or epic.project_id != project_id:
            raise NotFoundError("Épica no encontrada en este proyecto")
        return epic

    async def list_tasks(
        self, area_slug: str, project_id: UUID, principal: Principal
    ) -> list[TaskView]:
        """List the tasks of a project's board.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            principal (Principal): The caller.

        Returns:
            list[TaskView]: Tasks ordered by status column and position.
        """

        area = await self.authorize(area_slug, principal)
        project = await self._project(area.id, project_id)
        return await self._task_views(await self.repository.list_tasks(project.id))

    async def create_task(
        self, area_slug: str, project_id: UUID, command: CreateTaskCommand, principal: Principal
    ) -> TaskView:
        """Create a task at the end of its status column.

        Args:
            area_slug (str): Area slug.
            project_id (UUID): The project.
            command (CreateTaskCommand): Name and the optional attributes (epic,
                blocked reason); status starts as ``backlog``.
            principal (Principal): The caller.

        Returns:
            TaskView: The created task.
        """

        area = await self.authorize(area_slug, principal, "manage")
        project = await self._project(area.id, project_id)
        epic = (
            await self._project_epic(area.id, project.id, command.epic_id)
            if command.epic_id
            else None
        )
        check_dates(command.start_date, command.end_date)
        owner = await self._owner(command.owner_user_id)
        status = normalize_task_status(command.status) if command.status else DEFAULT_TASK_STATUS
        reason = clean_blocked_reason(status, command.blocked_reason)
        now = datetime.now(UTC)
        task = Task(
            id=uuid.uuid4(),
            area_id=area.id,
            project_id=project.id,
            epic_id=epic.id if epic else None,
            name=clean_text(command.name, "nombre", 300),
            description=optional_text(command.description, "descripción", 10000),
            status=status,
            priority=(
                normalize_priority(command.priority) if command.priority else DEFAULT_PRIORITY
            ),
            owner_user_id=owner.id if owner else None,
            start_date=command.start_date,
            end_date=command.end_date,
            position=await self.repository.next_task_position(project.id, status),
            blocked_reason=reason,
            version=1,
            created_by=principal.email,
            updated_by=principal.email,
            created_at=now,
            updated_at=now,
        )
        view = TaskView(await self.repository.add_task(task), owner)
        await self._publish(area.id, "task.created", task_payload(view), principal)
        await self._publish_counts(area, project.id, [epic.id] if epic else [], principal)
        return view

    async def update_task(
        self, area_slug: str, task_id: UUID, command: UpdateTaskCommand, principal: Principal
    ) -> TaskView:
        """Change a task, move it to another column or epic, or reorder it.

        Changing the status without a ``position`` places it last in the new
        column; changing the epic keeps its position.

        Args:
            area_slug (str): Area slug.
            task_id (UUID): The task.
            command (UpdateTaskCommand): Attributes listed in ``fields``.
            principal (Principal): The caller.

        Returns:
            TaskView: The updated task.
        """

        area = await self.authorize(area_slug, principal, "manage")
        task = await self._task(area.id, task_id)
        fields = command.fields
        for name in _REQUIRED_TASK_FIELDS:
            if name in fields and getattr(command, name) is None:
                raise InvalidInputError(f"El campo {name} no puede quedar vacío")

        recount: set[UUID] = set()
        counts_changed = False
        if "epic_id" in fields and command.epic_id != task.epic_id:
            new_epic_id: UUID | None = None
            if command.epic_id is not None:
                new_epic_id = (
                    await self._project_epic(area.id, task.project_id, command.epic_id)
                ).id
            recount |= {i for i in (task.epic_id, new_epic_id) if i}
            counts_changed = True
            task.epic_id = new_epic_id
        if "status" in fields:
            status = normalize_task_status(command.status)
            if status != task.status:
                if (status == DONE_TASK_STATUS) != (task.status == DONE_TASK_STATUS):
                    counts_changed = True
                    if task.epic_id:
                        recount.add(task.epic_id)
                task.status = status
                if status != BLOCKED_TASK_STATUS:
                    task.blocked_reason = None
                if "position" not in fields:
                    task.position = await self.repository.next_task_position(
                        task.project_id, status
                    )
        if "position" in fields:
            task.position = check_position(command.position)
        if "name" in fields:
            task.name = clean_text(command.name, "nombre", 300)
        if "description" in fields:
            task.description = optional_text(command.description, "descripción", 10000)
        if "priority" in fields:
            task.priority = normalize_priority(command.priority)
        if "start_date" in fields:
            task.start_date = command.start_date
        if "end_date" in fields:
            task.end_date = command.end_date
        check_dates(task.start_date, task.end_date)
        if "blocked_reason" in fields:
            task.blocked_reason = clean_blocked_reason(task.status, command.blocked_reason)
        if "owner_user_id" in fields:
            owner = await self._owner(command.owner_user_id)
            task.owner_user_id = owner.id if owner else None

        task.updated_by = principal.email
        saved = await self.repository.save_task(task, recount, counts_changed)
        view = (await self._task_views([saved]))[0]
        await self._publish(area.id, "task.updated", task_payload(view), principal, fields)
        if counts_changed:
            await self._publish_counts(area, task.project_id, recount, principal)
        return view

    async def delete_task(self, area_slug: str, task_id: UUID, principal: Principal) -> None:
        """Delete a task; any member of the area may.

        Args:
            area_slug (str): Area slug.
            task_id (UUID): The task.
            principal (Principal): The caller.

        Returns:
            None
        """

        area = await self.authorize(area_slug, principal, "manage")
        task = await self._task(area.id, task_id)
        await self.repository.delete_task(task)
        await self._publish(
            area.id,
            "task.deleted",
            {
                "id": str(task.id),
                "project_id": str(task.project_id),
                "epic_id": str(task.epic_id) if task.epic_id else None,
            },
            principal,
        )
        await self._publish_counts(
            area, task.project_id, [task.epic_id] if task.epic_id else [], principal
        )
