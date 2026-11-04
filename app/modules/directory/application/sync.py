"""Copy the Entra users into the directory and provision their platform users."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.modules.directory.domain.entities import (
    Employee,
    GraphUser,
    RunStatus,
    RunTrigger,
    SyncCounts,
    SyncRun,
    auto_visible,
)
from app.modules.directory.domain.ports import (
    DirectoryRepository,
    GraphDirectory,
    PhotoStorage,
    SyncRunRepository,
    UserProvisioning,
)
from app.modules.directory.domain.search import normalize_text

logger = logging.getLogger(__name__)

PHOTO_PREFIX = "directory-photos"
_NO_PHOTO = {
    "photo_key": None,
    "photo_thumb_key": None,
    "photo_etag": None,
    "photo_content_type": None,
    "photo_updated_at": None,
}


def photo_keys(object_id: str) -> tuple[str, str]:
    """Storage keys of the large (432 px) and thumbnail (96 px) photos.

    Args:
        object_id (str): Entra object id.

    Returns:
        tuple[str, str]: Large key and thumbnail key.
    """

    return f"{PHOTO_PREFIX}/{object_id}/432", f"{PHOTO_PREFIX}/{object_id}/96"


def account_change(user: GraphUser, previous: Employee | None) -> bool | None:
    """What to write in ``users.is_active`` for this account.

    Only edges are written, so an administrator's manual block survives the night:
    a disabled account blocks the user, an account enabled again unblocks it, and
    otherwise the user is left as it is.

    Args:
        user (GraphUser): The account as Graph lists it today.
        previous (Employee | None): The employee as the last sync left it.

    Returns:
        bool | None: ``False`` to block, ``True`` to unblock, ``None`` to keep.
    """

    if not user.account_enabled:
        return False
    if previous is not None and previous.account_enabled is False:
        return True
    return None


class DirectorySyncService:
    """One directory sync: read Graph, upsert employees and users, retire the leavers.

    Only one sync runs at a time: ``start`` inserts the run and the database refuses
    a second running one.
    """

    def __init__(
        self,
        graph: GraphDirectory,
        repository: DirectoryRepository,
        runs: SyncRunRepository,
        users: UserProvisioning,
        storage: PhotoStorage,
        corporate_domains: list[str],
        batch_size: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            graph (GraphDirectory): Microsoft Graph with application permissions.
            repository (DirectoryRepository): Employees and sites.
            runs (SyncRunRepository): Sync history.
            users (UserProvisioning): Identity port that creates and blocks users.
            storage (PhotoStorage): Object storage for the photos.
            corporate_domains (list[str]): Mail domains shown by default.
            batch_size (int): Users whose manager and photo are read at once.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.graph = graph
        self.repository = repository
        self.runs = runs
        self.users = users
        self.storage = storage
        self.corporate_domains = [domain.lower() for domain in corporate_domains]
        self.batch_size = max(1, batch_size)
        self.clock = clock or (lambda: datetime.now(UTC))

    async def start(self, trigger: RunTrigger, triggered_by: str | None) -> SyncRun:
        """Register a running sync.

        Args:
            trigger (RunTrigger): Manual or scheduled.
            triggered_by (str | None): Who asked for it.

        Returns:
            SyncRun: The running sync.

        Raises:
            ConflictError: When another sync is running.
        """

        return await self.runs.start(trigger, triggered_by, self.clock())

    async def run(self, trigger: RunTrigger, triggered_by: str | None) -> SyncRun:
        """Start a sync and carry it out.

        Args:
            trigger (RunTrigger): Manual or scheduled.
            triggered_by (str | None): Who asked for it.

        Returns:
            SyncRun: The finished sync.
        """

        started = await self.start(trigger, triggered_by)
        return await self.execute(started.id)

    async def recover_stale_runs(self) -> int:
        """Close the syncs a stopped process left running.

        Returns:
            int: How many were closed.
        """

        return await self.runs.fail_running(
            "interrupted", "El proceso se detuvo durante la sincronización", self.clock()
        )

    async def needs_catch_up(self) -> bool:
        """Whether no sync succeeded in the last 24 hours.

        Returns:
            bool: ``True`` when a sync should run now.
        """

        last = await self.runs.last_success()
        if last is None or last.finished_at is None:
            return True
        return last.finished_at < self.clock() - timedelta(hours=24)

    async def execute(self, run_id: UUID) -> SyncRun:
        """Carry out a sync already registered with ``start``.

        A failure while reading Graph or writing the employees fails the run; a
        failure with one person's manager or photo is counted and the run goes on.

        Args:
            run_id (UUID): The running sync.

        Returns:
            SyncRun: The finished sync, successful or failed.
        """

        counts = SyncCounts()
        try:
            await self._sync(counts)
        except Exception as exc:
            logger.exception("Directory sync %s failed", run_id)
            return await self.runs.finish(
                run_id,
                RunStatus.FAILED,
                counts.as_dict(),
                self.clock(),
                error_code=type(exc).__name__,
                error_message=(str(exc) or type(exc).__name__)[:1000],
            )
        logger.info("Directory sync %s: %s", run_id, counts.as_dict())
        return await self.runs.finish(run_id, RunStatus.SUCCESS, counts.as_dict(), self.clock())

    async def _sync(self, counts: SyncCounts) -> None:
        graph_users = await self.graph.list_users()
        previous = {
            employee.entra_object_id: employee
            for employee in await self.repository.synced_employees()
            if employee.entra_object_id
        }
        sites = {
            normalize_text(site.name): site.id
            for site in await self.repository.list_sites(include_inactive=True)
        }
        now = self.clock()
        synced: list[tuple[Employee, GraphUser]] = []
        for user in graph_users:
            if not user.is_member:
                counts.skipped_guests += 1
                continue
            counts.total += 1
            employee = await self._upsert(user, previous.get(user.object_id), sites, now, counts)
            synced.append((employee, user))

        employee_ids = {user.object_id: employee.id for employee, user in synced}
        limit = asyncio.Semaphore(self.batch_size)

        async def details(employee: Employee, user: GraphUser) -> None:
            async with limit:
                try:
                    changes = await self._details(employee, user, employee_ids, counts)
                    await self.repository.update_employee(employee.id, changes)
                except Exception:
                    logger.warning(
                        "Directory sync: manager or photo of %s failed",
                        user.object_id,
                        exc_info=True,
                    )
                    counts.errors += 1

        await asyncio.gather(*(details(employee, user) for employee, user in synced))

        leavers = await self.repository.mark_unseen_inactive(employee_ids)
        counts.deactivated = len(leavers)
        if leavers:
            result = await self.users.deactivate_users(leavers)
            counts.last_admin_protected += len(result.protected)
        await self.repository.recompute_is_manager()

    async def _upsert(
        self,
        user: GraphUser,
        previous: Employee | None,
        sites: dict[str, UUID],
        now: datetime,
        counts: SyncCounts,
    ) -> Employee:
        provisioned = await self.users.upsert_user(
            user.object_id,
            name=user.name,
            email=user.email,
            job_title=user.job_title,
            is_active=account_change(user, previous),
        )
        if provisioned.last_admin_protected:
            counts.last_admin_protected += 1
        fields: dict[str, Any] = {
            "full_name": user.name,
            "email": user.email,
            "job_title": user.job_title,
            "department": user.department,
            "office_location": user.office_location,
            "is_active": user.account_enabled,
            "account_enabled": user.account_enabled,
            "user_type": user.user_type,
            "auto_visible": auto_visible(user, self.corporate_domains),
            "synced_at": now,
        }
        if previous is None:
            # The site comes from Entra only once; after that it belongs to the directory.
            site_id = sites.get(normalize_text(user.office_location))
            if site_id is None:
                counts.without_site += 1
            counts.created += 1
            return await self.repository.create_employee(
                {
                    **fields,
                    "user_id": provisioned.user_id,
                    "entra_object_id": user.object_id,
                    "position": "",
                    "site_id": site_id,
                }
            )
        if previous.site is None:
            counts.without_site += 1
        counts.updated += 1
        updated = await self.repository.update_employee(previous.id, fields)
        return updated or previous

    async def _details(
        self,
        employee: Employee,
        user: GraphUser,
        employee_ids: dict[str, UUID],
        counts: SyncCounts,
    ) -> dict[str, Any]:
        manager = await self.graph.get_manager(user.object_id)
        changes: dict[str, Any] = {
            "hire_date": user.hire_date,
            "manager_id": employee_ids.get(manager) if manager else None,
        }
        changes.update(await self._photo(employee, user.object_id, counts))
        return changes

    async def _photo(self, employee: Employee, object_id: str, counts: SyncCounts) -> dict:
        meta = await self.graph.get_photo_meta(object_id)
        if meta is None:
            if employee.photo_key is None:
                return {}
            for key in (employee.photo_key, employee.photo_thumb_key):
                if key:
                    await self.storage.delete_object(key)
            return dict(_NO_PHOTO)
        if employee.photo_key and meta.etag and employee.photo_etag == meta.etag:
            return {}
        large = await self.graph.get_photo(object_id, 432)
        small = await self.graph.get_photo(object_id, 96)
        large, small = large or small, small or large
        if large is None or small is None:
            return {}
        large_key, small_key = photo_keys(object_id)
        await self.storage.put_object(large_key, large.content, content_type=large.content_type)
        await self.storage.put_object(small_key, small.content, content_type=small.content_type)
        counts.photos += 1
        return {
            "photo_key": large_key,
            "photo_thumb_key": small_key,
            "photo_etag": meta.etag,
            "photo_content_type": large.content_type,
            "photo_updated_at": self.clock(),
        }
