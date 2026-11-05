"""In-memory adapters for the projects module (no PostgreSQL or Redis)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import replace

import pytest

from app.core.errors import ConflictError
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.projects.application.live import ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.domain.entities import (
    DONE_TASK_STATUS,
    POSITION_STEP,
    Epic,
    Project,
    ProjectFolder,
    Task,
    TaskCounts,
)
from tests.modules.fakes import NOW


class FakeProjectRepository:
    def __init__(self) -> None:
        self.folders: dict[uuid.UUID, ProjectFolder] = {}
        self.projects: dict[uuid.UUID, Project] = {}
        self.epics: dict[uuid.UUID, Epic] = {}
        self.tasks: dict[uuid.UUID, Task] = {}

    async def list_folders(self, area_id):
        return [
            (
                f,
                sum(1 for p in self.projects.values() if p.folder_id == f.id and not p.is_archived),
            )
            for f in self.folders.values()
            if f.area_id == area_id
        ]

    async def get_folder(self, area_id, folder_id):
        folder = self.folders.get(folder_id)
        return replace(folder) if folder and folder.area_id == area_id else None

    async def folder_name_taken(self, area_id, name, exclude_id=None):
        return any(
            f.area_id == area_id and f.name.lower() == name.lower() and f.id != exclude_id
            for f in self.folders.values()
        )

    async def create_folder(self, area_id, name, description, color, created_by):
        folder = ProjectFolder(
            uuid.uuid4(), area_id, name, description, color, created_by, NOW, NOW
        )
        self.folders[folder.id] = folder
        return replace(folder)

    async def save_folder(self, folder):
        self.folders[folder.id] = replace(folder)
        return replace(folder)

    async def delete_folder(self, folder_id):
        self.folders.pop(folder_id)

    async def count_folder_projects(self, folder_id, include_archived):
        return sum(
            1
            for p in self.projects.values()
            if p.folder_id == folder_id and (include_archived or not p.is_archived)
        )

    async def list_projects(self, area_id, filters):
        items = [
            p
            for p in self.projects.values()
            if p.area_id == area_id
            and (filters.folder_id is None or p.folder_id == filters.folder_id)
            and (not filters.statuses or p.status in filters.statuses)
            and (filters.include_archived or not p.is_archived)
        ]
        return [replace(p) for p in sorted(items, key=lambda p: (str(p.folder_id), p.position))]

    async def get_project(self, area_id, project_id):
        project = self.projects.get(project_id)
        return replace(project) if project and project.area_id == area_id else None

    async def next_position(self, folder_id):
        positions = [p.position for p in self.projects.values() if p.folder_id == folder_id]
        return (max(positions) if positions else 0.0) + POSITION_STEP

    async def add_project(self, project):
        self.projects[project.id] = replace(project)
        return replace(project)

    async def save_project(self, project):
        stored = replace(project, version=self.projects[project.id].version + 1)
        self.projects[project.id] = stored
        return replace(stored)

    async def delete_project(self, project_id):
        self.projects.pop(project_id)
        self.epics = {k: b for k, b in self.epics.items() if b.project_id != project_id}
        self.tasks = {k: t for k, t in self.tasks.items() if t.project_id != project_id}

    def _count(self, key, ids):
        counts = {}
        for t in self.tasks.values():
            k = getattr(t, key)
            if k in ids:
                total, done = counts.get(k, (0, 0))
                counts[k] = (total + 1, done + (t.status == DONE_TASK_STATUS))
        return {k: TaskCounts(*v) for k, v in counts.items()}

    async def project_task_counts(self, project_ids):
        return self._count("project_id", set(project_ids))

    def _bump(self, project_id, epic_ids):
        p = self.projects[project_id]
        self.projects[project_id] = replace(p, version=p.version + 1)
        for i in epic_ids:
            if i in self.epics:
                self.epics[i] = replace(self.epics[i], version=self.epics[i].version + 1)

    # --- epics ---

    async def list_epics(self, project_id):
        found = [b for b in self.epics.values() if b.project_id == project_id]
        return [replace(b) for b in sorted(found, key=lambda b: b.position)]

    async def get_epic(self, area_id, epic_id):
        epic = self.epics.get(epic_id)
        return replace(epic) if epic and epic.area_id == area_id else None

    async def epic_task_counts(self, epic_ids):
        return self._count("epic_id", set(epic_ids))

    async def next_epic_position(self, project_id):
        found = [b.position for b in self.epics.values() if b.project_id == project_id]
        return (max(found) if found else 0.0) + POSITION_STEP

    async def add_epic(self, epic):
        self.epics[epic.id] = replace(epic)
        return replace(epic)

    async def save_epic(self, epic):
        self.epics[epic.id] = replace(epic, version=self.epics[epic.id].version + 1)
        return replace(self.epics[epic.id])

    async def delete_epic(self, epic_id):
        if any(t.epic_id == epic_id for t in self.tasks.values()):
            raise ConflictError("La épica tiene tareas")
        self.epics.pop(epic_id)

    # --- Tasks ---

    async def list_tasks(self, project_id):
        found = [t for t in self.tasks.values() if t.project_id == project_id]
        return [replace(t) for t in sorted(found, key=lambda t: (t.status, t.position))]

    async def get_task(self, area_id, task_id):
        task = self.tasks.get(task_id)
        return replace(task) if task and task.area_id == area_id else None

    async def next_task_position(self, project_id, status):
        found = [
            t.position
            for t in self.tasks.values()
            if t.project_id == project_id and t.status == status
        ]
        return (max(found) if found else 0.0) + POSITION_STEP

    async def add_task(self, task):
        self.tasks[task.id] = replace(task)
        self._bump(task.project_id, [task.epic_id])
        return replace(task)

    async def save_task(self, task, recount_epic_ids, project_changed=False):
        self.tasks[task.id] = replace(task, version=self.tasks[task.id].version + 1)
        if recount_epic_ids or project_changed:
            self._bump(task.project_id, recount_epic_ids)
        return replace(self.tasks[task.id])

    async def delete_task(self, task):
        self.tasks.pop(task.id)
        self._bump(task.project_id, [task.epic_id])


class FakeSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    async def get(self):
        return await self.queue.get()


class FakeLiveChannel:
    """Topics and presence of a single process."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.subscribers: dict[str, list[FakeSubscription]] = {}
        self.presence: dict[str, dict[str, dict]] = {}
        self.fail_publish = False

    async def publish(self, topic, message):
        if self.fail_publish:
            raise ConnectionError("redis down")
        self.published.append((topic, message))
        for subscription in self.subscribers.get(topic, []):
            subscription.queue.put_nowait(message)

    @contextlib.asynccontextmanager
    async def subscribe(self, topic):
        subscription = FakeSubscription()
        self.subscribers.setdefault(topic, []).append(subscription)
        try:
            yield subscription
        finally:
            self.subscribers[topic].remove(subscription)

    async def touch_presence(self, topic, member_id, data, ttl_seconds):
        self.presence.setdefault(topic, {})[member_id] = dict(data)

    async def drop_presence(self, topic, member_id):
        self.presence.get(topic, {}).pop(member_id, None)

    async def list_presence(self, topic):
        return list(self.presence.get(topic, {}).values())

    def events(self, prefix: str = "") -> list[dict]:
        return [m for _, m in self.published if m["type"].startswith(prefix)]


@pytest.fixture
def project_repo() -> FakeProjectRepository:
    return FakeProjectRepository()


@pytest.fixture
def channel() -> FakeLiveChannel:
    return FakeLiveChannel()


@pytest.fixture
def live(channel) -> ProjectLiveService:
    return ProjectLiveService(channel=channel, presence_ttl=45)


@pytest.fixture
def projects(project_repo, areas, directory, live) -> ProjectService:
    return ProjectService(
        repository=project_repo,
        areas=areas,
        people=directory,
        events=live,
    )


@pytest.fixture
async def ti(areas, admin, login, directory):
    """Area "ti" with Ana and Luis as members and Marta as responsible."""

    ana = await login("ana@jdh.com.co")
    luis = await login("luis@jdh.com.co")
    marta = await login("marta@jdh.com.co")
    area = await areas.create_area(
        CreateAreaCommand(slug="ti", name="TI", responsible_email=marta.email), admin
    )
    role = await areas.create_role("ti", "Desarrollador", admin)
    await areas.add_member("ti", ana.email, role.id, admin)
    await areas.add_member("ti", luis.email, role.id, admin)
    return area, ana, luis, marta
