"""Contracts required by the project use cases."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.projects.domain.entities import (
    Epic,
    Project,
    ProjectFolder,
    Task,
    TaskCounts,
)


@dataclass(frozen=True)
class ProjectFilters:
    """Listing criteria inside one area; archived projects are opt-in."""

    folder_id: UUID | None = None
    statuses: Sequence[str] = ()
    include_archived: bool = False


class AreaRef(Protocol):
    id: UUID
    slug: str
    responsible_user_id: UUID | None


class AreaAccess(Protocol):
    """Answer area access questions without reading the areas module's tables."""

    async def authorize(
        self, slug: str, principal: Principal, module: str | None = None, action: str = "read"
    ) -> AreaRef:
        """Return the area when the principal may use ``module`` of its space.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.
            module (str | None): Module of the permission catalog.
            action (str): ``read`` or ``manage``.

        Returns:
            AreaRef: The authorized area.
        """
        ...


class People(Protocol):
    """Platform users, read through the identity module."""

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, DirectoryUser]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Users to load.

        Returns:
            dict[UUID, DirectoryUser]: The users that exist, by identifier.
        """
        ...

    async def search_users(self, query: str, limit: int = 20) -> list[DirectoryUser]:
        """Find active users by name or email.

        Args:
            query (str): Text contained in the name or email.
            limit (int): Maximum number of users to return.

        Returns:
            list[DirectoryUser]: Matching active users ordered by name.
        """
        ...


class ProjectEventPublisher(Protocol):
    """Fan out changes to everyone watching an area's projects."""

    async def publish(self, area_id: UUID, event: dict[str, Any]) -> None:
        """Deliver an event to the area's live connections.

        Args:
            area_id (UUID): Area whose watchers receive it.
            event (dict[str, Any]): JSON-ready event.

        Returns:
            None
        """
        ...


class LiveSubscription(Protocol):
    async def get(self) -> dict[str, Any]:
        """Wait for the next message of the topic.

        Returns:
            dict[str, Any]: The message as published.
        """
        ...


class LiveChannel(Protocol):
    """Cross-process topics with presence; implemented over Redis."""

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Send a message to every subscriber of the topic.

        Args:
            topic (str): Topic name.
            message (dict[str, Any]): JSON-ready message.

        Returns:
            None
        """
        ...

    def subscribe(self, topic: str) -> AbstractAsyncContextManager[LiveSubscription]:
        """Receive the topic's messages while the context is open.

        Args:
            topic (str): Topic name.

        Returns:
            AbstractAsyncContextManager[LiveSubscription]: The subscription.
        """
        ...

    async def touch_presence(
        self, topic: str, member_id: str, data: dict[str, Any], ttl_seconds: int
    ) -> None:
        """Register or refresh a member of the topic.

        Args:
            topic (str): Topic name.
            member_id (str): One connection.
            data (dict[str, Any]): What other members see about it.
            ttl_seconds (int): Lifetime without another touch.

        Returns:
            None
        """
        ...

    async def drop_presence(self, topic: str, member_id: str) -> None:
        """Remove a member of the topic.

        Args:
            topic (str): Topic name.
            member_id (str): The connection.

        Returns:
            None
        """
        ...

    async def list_presence(self, topic: str) -> list[dict[str, Any]]:
        """List the live members of the topic.

        Args:
            topic (str): Topic name.

        Returns:
            list[dict[str, Any]]: Member data.
        """
        ...


class ProjectRepository(Protocol):
    """Define persistence operations for project folders and projects."""

    async def list_folders(self, area_id: UUID) -> list[tuple[ProjectFolder, int]]:
        """List the folders of an area with their active project count.

        Args:
            area_id (UUID): The area.

        Returns:
            list[tuple[ProjectFolder, int]]: Folders ordered by name, each with
                how many non-archived projects it holds.
        """
        ...

    async def get_folder(self, area_id: UUID, folder_id: UUID) -> ProjectFolder | None:
        """Find a folder inside a given area.

        Args:
            area_id (UUID): Area that must own the folder.
            folder_id (UUID): The folder.

        Returns:
            ProjectFolder | None: The folder when it belongs to the area.
        """
        ...

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
        ...

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
        ...

    async def save_folder(self, folder: ProjectFolder) -> ProjectFolder:
        """Persist changes to a folder.

        Args:
            folder (ProjectFolder): Folder with updated fields.

        Returns:
            ProjectFolder: The stored folder.
        """
        ...

    async def delete_folder(self, folder_id: UUID) -> None:
        """Delete a folder.

        Args:
            folder_id (UUID): Folder to delete; fails with a conflict while it
                still holds projects.

        Returns:
            None
        """
        ...

    async def count_folder_projects(self, folder_id: UUID, include_archived: bool) -> int:
        """Count the projects inside a folder.

        Args:
            folder_id (UUID): The folder.
            include_archived (bool): Whether archived projects count.

        Returns:
            int: Number of projects.
        """
        ...

    async def list_projects(self, area_id: UUID, filters: ProjectFilters) -> list[Project]:
        """List the projects of an area.

        Args:
            area_id (UUID): The area.
            filters (ProjectFilters): Folder, statuses and archived criteria.

        Returns:
            list[Project]: Projects ordered by folder and position.
        """
        ...

    async def get_project(self, area_id: UUID, project_id: UUID) -> Project | None:
        """Find a project inside a given area.

        Args:
            area_id (UUID): Area that must own the project.
            project_id (UUID): The project.

        Returns:
            Project | None: The project when it belongs to the area.
        """
        ...

    async def next_position(self, folder_id: UUID) -> float:
        """Position that places a project after every other one in the folder.

        Args:
            folder_id (UUID): The folder.

        Returns:
            float: The position for a new last project.
        """
        ...

    async def add_project(self, project: Project) -> Project:
        """Persist a new project.

        Args:
            project (Project): Project with its identifier already assigned.

        Returns:
            Project: The stored project.
        """
        ...

    async def save_project(self, project: Project) -> Project:
        """Persist changes to a project and increase its version.

        Args:
            project (Project): Project with updated fields.

        Returns:
            Project: The stored project with its new version.
        """
        ...

    async def delete_project(self, project_id: UUID) -> None:
        """Delete a project permanently, with its epics and tasks.

        Args:
            project_id (UUID): The project.

        Returns:
            None
        """
        ...

    async def project_task_counts(self, project_ids: Collection[UUID]) -> dict[UUID, TaskCounts]:
        """Count all and done tasks of several projects.

        Args:
            project_ids (Collection[UUID]): The projects.

        Returns:
            dict[UUID, TaskCounts]: Counts by project; projects without tasks are absent.
        """
        ...

    # --- epics ---

    async def list_epics(self, project_id: UUID) -> list[Epic]:
        """List the epics of a project.

        Args:
            project_id (UUID): The project.

        Returns:
            list[Epic]: epics ordered by position.
        """
        ...

    async def get_epic(self, area_id: UUID, epic_id: UUID) -> Epic | None:
        """Find an epic inside a given area.

        Args:
            area_id (UUID): Area that must own the epic.
            epic_id (UUID): The epic.

        Returns:
            Epic | None: The epic when it belongs to the area.
        """
        ...

    async def epic_task_counts(self, epic_ids: Collection[UUID]) -> dict[UUID, TaskCounts]:
        """Count all and done tasks of several epics.

        Args:
            epic_ids (Collection[UUID]): The epics.

        Returns:
            dict[UUID, TaskCounts]: Counts by epic; epics without tasks are absent.
        """
        ...

    async def next_epic_position(self, project_id: UUID) -> float:
        """Position that places an epic after every other one in the project.

        Args:
            project_id (UUID): The project.

        Returns:
            float: The position for a new last epic.
        """
        ...

    async def add_epic(self, epic: Epic) -> Epic:
        """Persist a new epic.

        Args:
            epic (Epic): Epic with its identifier already assigned.

        Returns:
            epic: The stored epic.
        """
        ...

    async def save_epic(self, epic: Epic) -> Epic:
        """Persist changes to an epic and increase its version.

        Args:
            epic (Epic): Epic with updated fields.

        Returns:
            epic: The stored epic with its new version.
        """
        ...

    async def delete_epic(self, epic_id: UUID) -> None:
        """Delete an epic; fails with a conflict while it still holds tasks.

        Args:
            epic_id (UUID): The epic.

        Returns:
            None
        """
        ...

    # --- Tasks ---

    async def list_tasks(self, project_id: UUID) -> list[Task]:
        """List the tasks of a project.

        Args:
            project_id (UUID): The project.

        Returns:
            list[Task]: Tasks ordered by status column and position.
        """
        ...

    async def get_task(self, area_id: UUID, task_id: UUID) -> Task | None:
        """Find a task inside a given area.

        Args:
            area_id (UUID): Area that must own the task.
            task_id (UUID): The task.

        Returns:
            Task | None: The task when it belongs to the area.
        """
        ...

    async def next_task_position(self, project_id: UUID, status: str) -> float:
        """Position that places a task last in one status column of a project.

        Args:
            project_id (UUID): The project.
            status (str): The column.

        Returns:
            float: The position for a new last task.
        """
        ...

    async def add_task(self, task: Task) -> Task:
        """Persist a new task and raise the version of its project and epic.

        Args:
            task (Task): Task with its identifier already assigned.

        Returns:
            Task: The stored task.
        """
        ...

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
        ...

    async def delete_task(self, task: Task) -> None:
        """Delete a task and raise the version of its project and epic.

        Args:
            task (Task): The task.

        Returns:
            None
        """
        ...
