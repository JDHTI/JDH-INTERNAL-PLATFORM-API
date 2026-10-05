"""PostgreSQL implementation of the project repository."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.projects.domain.entities import (
    DONE_TASK_STATUS,
    POSITION_STEP,
    Epic,
    Project,
    ProjectFolder,
    Task,
    TaskCounts,
)
from app.modules.projects.domain.ports import ProjectFilters
from app.modules.projects.infrastructure.models import (
    ProjectEpicModel,
    ProjectFolderModel,
    ProjectModel,
    ProjectTaskModel,
)

_EPIC_FIELDS = (
    "id",
    "area_id",
    "project_id",
    "name",
    "description",
    "color",
    "start_date",
    "end_date",
    "position",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)
_MUTABLE_EPIC_FIELDS = (
    "name",
    "description",
    "color",
    "start_date",
    "end_date",
    "position",
    "updated_by",
)
_TASK_FIELDS = (
    "id",
    "area_id",
    "project_id",
    "epic_id",
    "name",
    "description",
    "status",
    "priority",
    "owner_user_id",
    "start_date",
    "end_date",
    "position",
    "blocked_reason",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)
_MUTABLE_TASK_FIELDS = (
    "epic_id",
    "name",
    "description",
    "status",
    "priority",
    "owner_user_id",
    "start_date",
    "end_date",
    "position",
    "blocked_reason",
    "updated_by",
)

_PROJECT_FIELDS = (
    "id",
    "area_id",
    "folder_id",
    "name",
    "description",
    "color",
    "status",
    "priority",
    "start_date",
    "end_date",
    "owner_user_id",
    "position",
    "is_archived",
    "archived_at",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)
# What a save may change; identity, area and authorship stay put.
_MUTABLE_PROJECT_FIELDS = (
    "folder_id",
    "name",
    "description",
    "color",
    "status",
    "priority",
    "start_date",
    "end_date",
    "owner_user_id",
    "position",
    "is_archived",
    "archived_at",
    "updated_by",
)
_DUPLICATE_FOLDER = "Ya existe una carpeta con ese nombre en el área"


def _folder(m: ProjectFolderModel) -> ProjectFolder:
    return ProjectFolder(
        id=m.id,
        area_id=m.area_id,
        name=m.name,
        description=m.description,
        color=m.color,
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _project(m: ProjectModel) -> Project:
    return Project(**{name: getattr(m, name) for name in _PROJECT_FIELDS})


def _epic(m: ProjectEpicModel) -> Epic:
    return Epic(**{name: getattr(m, name) for name in _EPIC_FIELDS})


def _task(m: ProjectTaskModel) -> Task:
    return Task(**{name: getattr(m, name) for name in _TASK_FIELDS})


def _bump_counts(project_id: UUID, epic_ids: Collection[UUID]) -> list:
    """Statements that raise the versions whose task counts just changed."""

    statements = [
        update(ProjectModel)
        .where(ProjectModel.id == project_id)
        .values(version=ProjectModel.version + 1, updated_at=func.now())
    ]
    if epic_ids:
        statements.append(
            update(ProjectEpicModel)
            .where(ProjectEpicModel.id.in_(list(epic_ids)))
            .values(version=ProjectEpicModel.version + 1, updated_at=func.now())
        )
    return statements


class SqlAlchemyProjectRepository:
    """Persist project folders and projects; each operation owns its transaction."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared engine and session factory.
        """

        self.database = database

    async def _commit(self, session, conflict: str) -> None:
        try:
            await session.commit()
        except IntegrityError as exc:
            raise ConflictError(conflict) from exc

    # --- Folders ---

    async def list_folders(self, area_id: UUID) -> list[tuple[ProjectFolder, int]]:
        """List the folders of an area with their active project count.

        Args:
            area_id (UUID): The area.

        Returns:
            list[tuple[ProjectFolder, int]]: Folders ordered by name, each with
                how many non-archived projects it holds.
        """

        counts = (
            select(ProjectModel.folder_id, func.count().label("n"))
            .where(ProjectModel.area_id == area_id, ProjectModel.is_archived.is_(False))
            .group_by(ProjectModel.folder_id)
            .subquery()
        )
        query = (
            select(ProjectFolderModel, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.folder_id == ProjectFolderModel.id)
            .where(ProjectFolderModel.area_id == area_id)
            .order_by(func.lower(ProjectFolderModel.name))
        )
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [(_folder(f), int(n)) for f, n in result.all()]

    async def get_folder(self, area_id: UUID, folder_id: UUID) -> ProjectFolder | None:
        """Find a folder inside a given area.

        Args:
            area_id (UUID): Area that must own the folder.
            folder_id (UUID): The folder.

        Returns:
            ProjectFolder | None: The folder when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(ProjectFolderModel).where(
                    ProjectFolderModel.area_id == area_id, ProjectFolderModel.id == folder_id
                )
            )
            model = result.scalar_one_or_none()
            return _folder(model) if model else None

    async def folder_name_taken(
        self, area_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the area already has a folder with this name.

        Args:
            area_id (UUID): The area.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Folder to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """

        condition = (ProjectFolderModel.area_id == area_id) & (
            func.lower(ProjectFolderModel.name) == name.lower()
        )
        if exclude_id is not None:
            condition = condition & (ProjectFolderModel.id != exclude_id)
        async with self.database.session_factory() as session:
            return bool((await session.execute(select(exists().where(condition)))).scalar())

    async def create_folder(
        self, area_id: UUID, name: str, description: str | None, color: str, created_by: str
    ) -> ProjectFolder:
        """Persist a folder.

        Args:
            area_id (UUID): Owning area.
            name (str): Folder name, unique inside the area.
            description (str | None): Optional description.
            color (str): Palette key.
            created_by (str): Email of the creator.

        Returns:
            ProjectFolder: The stored folder.
        """

        model = ProjectFolderModel(
            area_id=area_id,
            name=name,
            description=description,
            color=color,
            created_by=created_by,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            await self._commit(session, _DUPLICATE_FOLDER)
            await session.refresh(model)
            return _folder(model)

    async def save_folder(self, folder: ProjectFolder) -> ProjectFolder:
        """Persist changes to a folder.

        Args:
            folder (ProjectFolder): Folder with updated fields.

        Returns:
            ProjectFolder: The stored folder.
        """

        async with self.database.session_factory() as session:
            model = await session.get(ProjectFolderModel, folder.id)
            model.name = folder.name
            model.description = folder.description
            model.color = folder.color
            await self._commit(session, _DUPLICATE_FOLDER)
            await session.refresh(model)
            return _folder(model)

    async def delete_folder(self, folder_id: UUID) -> None:
        """Delete a folder.

        Args:
            folder_id (UUID): Folder to delete; fails with a conflict while it
                still holds projects.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            # The FK fires on the DELETE itself, before any commit.
            try:
                await session.execute(
                    delete(ProjectFolderModel).where(ProjectFolderModel.id == folder_id)
                )
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("La carpeta tiene proyectos") from exc

    async def count_folder_projects(self, folder_id: UUID, include_archived: bool) -> int:
        """Count the projects inside a folder.

        Args:
            folder_id (UUID): The folder.
            include_archived (bool): Whether archived projects count.

        Returns:
            int: Number of projects.
        """

        query = (
            select(func.count())
            .select_from(ProjectModel)
            .where(ProjectModel.folder_id == folder_id)
        )
        if not include_archived:
            query = query.where(ProjectModel.is_archived.is_(False))
        async with self.database.session_factory() as session:
            return int((await session.execute(query)).scalar() or 0)

    # --- Projects ---

    async def list_projects(self, area_id: UUID, filters: ProjectFilters) -> list[Project]:
        """List the projects of an area.

        Args:
            area_id (UUID): The area.
            filters (ProjectFilters): Folder, statuses and archived criteria.

        Returns:
            list[Project]: Projects ordered by folder and position.
        """

        query = select(ProjectModel).where(ProjectModel.area_id == area_id)
        if filters.folder_id is not None:
            query = query.where(ProjectModel.folder_id == filters.folder_id)
        if filters.statuses:
            query = query.where(ProjectModel.status.in_(list(filters.statuses)))
        if not filters.include_archived:
            query = query.where(ProjectModel.is_archived.is_(False))
        query = query.order_by(ProjectModel.folder_id, ProjectModel.position, ProjectModel.id)
        async with self.database.session_factory() as session:
            return [_project(m) for m in (await session.execute(query)).scalars()]

    async def get_project(self, area_id: UUID, project_id: UUID) -> Project | None:
        """Find a project inside a given area.

        Args:
            area_id (UUID): Area that must own the project.
            project_id (UUID): The project.

        Returns:
            Project | None: The project when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(ProjectModel).where(
                    ProjectModel.area_id == area_id, ProjectModel.id == project_id
                )
            )
            model = result.scalar_one_or_none()
            return _project(model) if model else None

    async def next_position(self, folder_id: UUID) -> float:
        """Position that places a project after every other one in the folder.

        Args:
            folder_id (UUID): The folder.

        Returns:
            float: The position for a new last project.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(func.max(ProjectModel.position)).where(ProjectModel.folder_id == folder_id)
            )
            current = result.scalar()
            return (float(current) if current is not None else 0.0) + POSITION_STEP

    async def add_project(self, project: Project) -> Project:
        """Persist a new project.

        Args:
            project (Project): Project with its identifier already assigned.

        Returns:
            Project: The stored project.
        """

        values = {n: getattr(project, n) for n in _PROJECT_FIELDS}
        values.pop("created_at")
        values.pop("updated_at")
        model = ProjectModel(**values)
        async with self.database.session_factory() as session:
            session.add(model)
            await self._commit(session, "La carpeta del proyecto ya no existe")
            await session.refresh(model)
            return _project(model)

    async def save_project(self, project: Project) -> Project:
        """Persist changes to a project and increase its version.

        Args:
            project (Project): Project with updated fields.

        Returns:
            Project: The stored project with its new version.
        """

        async with self.database.session_factory() as session:
            model = await session.get(ProjectModel, project.id)
            if model is None:
                raise NotFoundError("El proyecto fue eliminado")
            for name in _MUTABLE_PROJECT_FIELDS:
                setattr(model, name, getattr(project, name))
            # Incremented in SQL so two concurrent saves never share a version.
            model.version = ProjectModel.version + 1
            await self._commit(session, "La carpeta de destino ya no existe")
            await session.refresh(model)
            return _project(model)

    async def delete_project(self, project_id: UUID) -> None:
        """Delete a project permanently, with its epics and tasks (FK cascade).

        Args:
            project_id (UUID): The project.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            await session.execute(delete(ProjectModel).where(ProjectModel.id == project_id))
            await session.commit()

    async def project_task_counts(self, project_ids: Collection[UUID]) -> dict[UUID, TaskCounts]:
        """Count all and done tasks of several projects.

        Args:
            project_ids (Collection[UUID]): The projects.

        Returns:
            dict[UUID, TaskCounts]: Counts by project; projects without tasks are absent.
        """

        return await self._counts(ProjectTaskModel.project_id, project_ids)

    async def _counts(self, column, ids: Collection[UUID]) -> dict[UUID, TaskCounts]:
        if not ids:
            return {}
        query = (
            select(
                column,
                func.count(),
                func.count().filter(ProjectTaskModel.status == DONE_TASK_STATUS),
            )
            .where(column.in_(list(ids)))
            .group_by(column)
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
            return {key: TaskCounts(int(total), int(done)) for key, total, done in rows}

    # --- epics ---

    async def list_epics(self, project_id: UUID) -> list[Epic]:
        """List the epics of a project.

        Args:
            project_id (UUID): The project.

        Returns:
            list[Epic]: epics ordered by position.
        """

        query = (
            select(ProjectEpicModel)
            .where(ProjectEpicModel.project_id == project_id)
            .order_by(ProjectEpicModel.position, ProjectEpicModel.id)
        )
        async with self.database.session_factory() as session:
            return [_epic(m) for m in (await session.execute(query)).scalars()]

    async def get_epic(self, area_id: UUID, epic_id: UUID) -> Epic | None:
        """Find an epic inside a given area.

        Args:
            area_id (UUID): Area that must own the epic.
            epic_id (UUID): The epic.

        Returns:
            Epic | None: The epic when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(ProjectEpicModel).where(
                    ProjectEpicModel.area_id == area_id, ProjectEpicModel.id == epic_id
                )
            )
            model = result.scalar_one_or_none()
            return _epic(model) if model else None

    async def epic_task_counts(self, epic_ids: Collection[UUID]) -> dict[UUID, TaskCounts]:
        """Count all and done tasks of several epics.

        Args:
            epic_ids (Collection[UUID]): The epics.

        Returns:
            dict[UUID, TaskCounts]: Counts by epic; epics without tasks are absent.
        """

        return await self._counts(ProjectTaskModel.epic_id, epic_ids)

    async def next_epic_position(self, project_id: UUID) -> float:
        """Position that places an epic after every other one in the project.

        Args:
            project_id (UUID): The project.

        Returns:
            float: The position for a new last epic.
        """

        async with self.database.session_factory() as session:
            current = (
                await session.execute(
                    select(func.max(ProjectEpicModel.position)).where(
                        ProjectEpicModel.project_id == project_id
                    )
                )
            ).scalar()
            return (float(current) if current is not None else 0.0) + POSITION_STEP

    async def add_epic(self, epic: Epic) -> Epic:
        """Persist a new epic.

        Args:
            epic (Epic): Epic with its identifier already assigned.

        Returns:
            epic: The stored epic.
        """

        values = {n: getattr(epic, n) for n in _EPIC_FIELDS}
        values.pop("created_at")
        values.pop("updated_at")
        model = ProjectEpicModel(**values)
        async with self.database.session_factory() as session:
            session.add(model)
            await self._commit(session, "El proyecto de la épica ya no existe")
            await session.refresh(model)
            return _epic(model)

    async def save_epic(self, epic: Epic) -> Epic:
        """Persist changes to an epic and increase its version.

        Args:
            epic (Epic): Epic with updated fields.

        Returns:
            epic: The stored epic with its new version.
        """

        async with self.database.session_factory() as session:
            model = await session.get(ProjectEpicModel, epic.id)
            if model is None:
                raise NotFoundError("La épica fue eliminada")
            for name in _MUTABLE_EPIC_FIELDS:
                setattr(model, name, getattr(epic, name))
            model.version = ProjectEpicModel.version + 1
            await self._commit(session, "La épica entra en conflicto con otro cambio")
            await session.refresh(model)
            return _epic(model)

    async def delete_epic(self, epic_id: UUID) -> None:
        """Delete an epic; fails with a conflict while it still holds tasks.

        Args:
            epic_id (UUID): The epic.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            # The FK fires on the DELETE itself, before any commit.
            try:
                await session.execute(
                    delete(ProjectEpicModel).where(ProjectEpicModel.id == epic_id)
                )
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("La épica tiene tareas") from exc

    # --- Tasks ---

    async def list_tasks(self, project_id: UUID) -> list[Task]:
        """List the tasks of a project.

        Args:
            project_id (UUID): The project.

        Returns:
            list[Task]: Tasks ordered by status column and position.
        """

        query = (
            select(ProjectTaskModel)
            .where(ProjectTaskModel.project_id == project_id)
            .order_by(ProjectTaskModel.status, ProjectTaskModel.position, ProjectTaskModel.id)
        )
        async with self.database.session_factory() as session:
            return [_task(m) for m in (await session.execute(query)).scalars()]

    async def get_task(self, area_id: UUID, task_id: UUID) -> Task | None:
        """Find a task inside a given area.

        Args:
            area_id (UUID): Area that must own the task.
            task_id (UUID): The task.

        Returns:
            Task | None: The task when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(ProjectTaskModel).where(
                    ProjectTaskModel.area_id == area_id, ProjectTaskModel.id == task_id
                )
            )
            model = result.scalar_one_or_none()
            return _task(model) if model else None

    async def next_task_position(self, project_id: UUID, status: str) -> float:
        """Position that places a task last in one status column of a project.

        Args:
            project_id (UUID): The project.
            status (str): The column.

        Returns:
            float: The position for a new last task.
        """

        async with self.database.session_factory() as session:
            current = (
                await session.execute(
                    select(func.max(ProjectTaskModel.position)).where(
                        ProjectTaskModel.project_id == project_id,
                        ProjectTaskModel.status == status,
                    )
                )
            ).scalar()
            return (float(current) if current is not None else 0.0) + POSITION_STEP

    async def add_task(self, task: Task) -> Task:
        """Persist a new task and raise the version of its project and epic.

        Args:
            task (Task): Task with its identifier already assigned.

        Returns:
            Task: The stored task.
        """

        values = {n: getattr(task, n) for n in _TASK_FIELDS}
        values.pop("created_at")
        values.pop("updated_at")
        model = ProjectTaskModel(**values)
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.flush()
                for statement in _bump_counts(
                    task.project_id, [task.epic_id] if task.epic_id else []
                ):
                    await session.execute(statement)
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("La épica de la tarea ya no existe") from exc
            await session.refresh(model)
            return _task(model)

    async def save_task(
        self, task: Task, recount_epic_ids: Collection[UUID], project_changed: bool = False
    ) -> Task:
        """Persist changes to a task and increase its version.

        When ``recount_epic_ids`` is not empty or ``project_changed`` is set,
        the counts shown on the project and on those epics changed, so their
        versions rise in the same transaction.

        Args:
            task (Task): Task with updated fields.
            recount_epic_ids (Collection[UUID]): Epics whose counts changed.
            project_changed (bool): The project's counts changed even when no
                epic's did (a task without epic).

        Returns:
            Task: The stored task with its new version.
        """

        async with self.database.session_factory() as session:
            model = await session.get(ProjectTaskModel, task.id)
            if model is None:
                raise NotFoundError("La tarea fue eliminada")
            for name in _MUTABLE_TASK_FIELDS:
                setattr(model, name, getattr(task, name))
            model.version = ProjectTaskModel.version + 1
            try:
                await session.flush()
                if recount_epic_ids or project_changed:
                    for statement in _bump_counts(task.project_id, recount_epic_ids):
                        await session.execute(statement)
                await session.commit()
            except IntegrityError as exc:
                raise ConflictError("La épica de destino ya no existe") from exc
            await session.refresh(model)
            return _task(model)

    async def delete_task(self, task: Task) -> None:
        """Delete a task and raise the version of its project and epic.

        Args:
            task (Task): The task.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            await session.execute(delete(ProjectTaskModel).where(ProjectTaskModel.id == task.id))
            for statement in _bump_counts(task.project_id, [task.epic_id] if task.epic_id else []):
                await session.execute(statement)
            await session.commit()
