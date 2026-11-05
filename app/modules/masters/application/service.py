"""Use cases for reading the shared master catalog, live from the DWH.

Administrators and moderators read every master. Anyone else reads the masters
switched on in the areas where their role grants ``maestros`` (read or manage); fields
marked ``is_pii`` are shown only to whoever manages the module or administers the
platform.

Records are not copied into the platform: each read goes to the master's ``consumo``
view and is cached under the last run of the DWH sequence that refreshes it. A new run
makes the cache old; while the sequence reloads (dbt re-creates the views) or the DWH
does not answer, the last cached answer is served and marked ``is_stale``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, TypeVar

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.masters.application.mapping import to_record
from app.modules.masters.domain.entities import (
    Freshness,
    MasterDetail,
    MasterRecord,
    MasterRecordPage,
    MasterSpec,
    MasterSummary,
    build_spec,
    clean_master_slug,
)
from app.modules.masters.domain.ports import (
    MasterAccess,
    MasterCache,
    MasterRepository,
    MasterWarehouse,
    WarehouseUnavailable,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_UNAVAILABLE = (
    "El DWH no respondió para este maestro; puede estar actualizándose. "
    "Intenta de nuevo en unos minutos."
)

T = TypeVar("T")


@dataclass(frozen=True)
class _Read:
    """A value read from the DWH or the cache."""

    value: Any
    ready_at: datetime | None
    stale: bool


class _EveryMaster:
    """Access when the module is not wired to the areas (scripts, tests)."""

    async def visible_masters(self, principal: Principal) -> frozenset[str] | None:
        return None

    async def area_masters(self, slug: str) -> frozenset[str] | None:
        return None

    async def can_see_private(self, principal: Principal) -> bool:
        return True


class MasterService:
    """Coordinate read access to masters."""

    def __init__(
        self,
        repository: MasterRepository,
        warehouse: MasterWarehouse,
        cache: MasterCache,
        access: MasterAccess | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (MasterRepository): The declared masters.
            warehouse (MasterWarehouse): Live reads of the ``consumo`` views.
            cache (MasterCache): What was last read, by sequence run.
            access (MasterAccess | None): Who sees which master; every master when
                not given.
        """

        self.repository = repository
        self.warehouse = warehouse
        self.cache = cache
        self.access = access or _EveryMaster()

    # --- Reads for people ---

    async def list_masters(
        self, principal: Principal, area_slug: str | None = None
    ) -> list[MasterSummary]:
        """List the active masters the person may read.

        Args:
            principal (Principal): The caller.
            area_slug (str | None): Only the masters switched on in this area.

        Returns:
            list[MasterSummary]: In sort order, with their DWH record counts.
        """

        only = await self.access.area_masters(area_slug) if area_slug else None
        visible = await self.access.visible_masters(principal)
        return [
            await self._with_count(detail)
            for detail in await self.repository.list_definitions()
            if (visible is None or detail.summary.slug in visible)
            and (only is None or detail.summary.slug in only)
        ]

    async def get_master(self, slug: str, principal: Principal) -> MasterDetail:
        """One master with the fields the person may see and its record count.

        Args:
            slug (str): Master slug.
            principal (Principal): The caller.

        Returns:
            MasterDetail: The master.
        """

        detail = await self._visible_definition(slug, principal)
        counted = replace(detail, summary=await self._with_count(detail))
        if await self.access.can_see_private(principal):
            return counted
        return replace(counted, fields=[field for field in detail.fields if not field.is_pii])

    async def list_records(
        self,
        slug: str,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
        principal: Principal,
    ) -> MasterRecordPage:
        """Search a master's records by code or name.

        Args:
            slug (str): Master slug.
            query (str | None): Text in the code or the name.
            is_active (bool | None): Only active or inactive records.
            offset (int): Records skipped.
            limit (int): Records returned, up to :data:`MAX_PAGE_SIZE`.
            principal (Principal): The caller.

        Returns:
            MasterRecordPage: The page, with when the DWH published it.

        Raises:
            DependencyUnavailableError: The DWH did not answer and nothing was cached.
        """

        if offset < 0:
            raise InvalidInputError("El desplazamiento no puede ser negativo")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El límite debe estar entre 1 y {MAX_PAGE_SIZE}")
        detail = await self._visible_definition(slug, principal)
        term = " ".join(query.split()) if query else None
        page = await self._page(detail, term or None, is_active, offset, limit)
        return await self._without_private(detail, page, principal)

    # --- Background work ---

    async def warm(self, sequence: str) -> dict[str, Any]:
        """Read the masters of a sequence that just published, so the cache holds them.

        The count and the first page of each master are what people open first, and
        what is served while the sequence reloads the next time.

        Args:
            sequence (str): DWH sequence name.

        Returns:
            dict[str, Any]: ``masters`` (slug, total, error) and ``errors``.
        """

        await self.warehouse.freshness([sequence], refresh=True)
        reports: list[dict[str, Any]] = []
        for detail in await self.repository.list_definitions():
            if detail.summary.dwh_sequence != sequence:
                continue
            report: dict[str, Any] = {"slug": detail.summary.slug, "total": None, "error": None}
            try:
                page = await self._page(detail, None, None, 0, DEFAULT_PAGE_SIZE)
                report["total"] = page.total
            except (DependencyUnavailableError, ValueError) as exc:
                report["error"] = str(exc)
            reports.append(report)
        return {
            "masters": reports,
            "errors": [f"{r['slug']}: {r['error']}" for r in reports if r["error"]],
        }

    # --- Helpers ---

    async def _visible_definition(self, slug: str, principal: Principal) -> MasterDetail:
        slug = clean_master_slug(slug)
        visible = await self.access.visible_masters(principal)
        if visible is not None and slug not in visible:
            raise ForbiddenError("No tienes acceso a este maestro")
        detail = await self.repository.get_definition(slug)
        if detail is None:
            raise NotFoundError("Maestro no encontrado")
        return detail

    @staticmethod
    def _spec(detail: MasterDetail) -> MasterSpec:
        try:
            return build_spec(detail)
        except ValueError as exc:
            logger.error("Masters: %s", exc)
            raise DependencyUnavailableError("El maestro está mal declarado") from exc

    async def _freshness(self, detail: MasterDetail) -> Freshness | None:
        sequence = detail.summary.dwh_sequence
        if not sequence:
            return None
        return (await self.warehouse.freshness([sequence])).get(sequence)

    async def _cached(
        self,
        key: Hashable,
        detail: MasterDetail,
        load: Callable[[datetime | None], Awaitable[T]],
    ) -> _Read:
        """A value of the last sequence run, from the cache or the DWH.

        Raises:
            DependencyUnavailableError: The DWH did not answer and nothing was cached.
        """

        state = await self._freshness(detail)
        version = state.corrida_id if state else None
        hit = self.cache.get(key)
        if hit is not None:
            value, ready_at = hit.value
            if version is not None and hit.version == version:
                return _Read(value, ready_at, stale=False)
            if state is not None and state.reloading:
                return _Read(value, ready_at, stale=True)
        ready_at = state.data_ready_at if state else None
        try:
            value = await load(ready_at)
        except WarehouseUnavailable as exc:
            if hit is None:
                raise DependencyUnavailableError(_UNAVAILABLE) from exc
            logger.warning("Masters: serving the cached %s: %s", key, exc)
            cached, cached_ready_at = hit.value
            return _Read(cached, cached_ready_at, stale=True)
        self.cache.put(key, version, (value, ready_at))
        return _Read(value, ready_at, stale=False)

    async def _with_count(self, detail: MasterDetail) -> MasterSummary:
        """The summary with the master's DWH record count, or ``None`` when unknown."""

        summary = detail.summary
        try:
            spec = build_spec(detail)
            read = await self._cached(
                ("count", summary.slug),
                detail,
                lambda _ready_at: self.warehouse.count(spec, None, None),
            )
        except (DependencyUnavailableError, ValueError) as exc:
            logger.warning("Masters: no count for %s: %s", summary.slug, exc)
            return summary
        return replace(
            summary, record_count=read.value, last_synced_at=read.ready_at, is_stale=read.stale
        )

    async def _page(
        self,
        detail: MasterDetail,
        term: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> MasterRecordPage:
        spec = self._spec(detail)

        async def load(ready_at: datetime | None) -> tuple[int, list[MasterRecord]]:
            total = await self.warehouse.count(spec, term, is_active)
            rows = await self.warehouse.page(spec, term, is_active, offset, limit) if total else []
            return total, [r for row in rows if (r := to_record(spec, row, ready_at))]

        key = ("page", spec.slug, term, is_active, offset, limit)
        read = await self._cached(key, detail, load)
        total, items = read.value
        return MasterRecordPage(
            items=list(items),
            total=total,
            offset=offset,
            limit=limit,
            last_synced_at=read.ready_at,
            is_stale=read.stale,
        )

    async def _without_private(
        self, detail: MasterDetail, page: MasterRecordPage, principal: Principal
    ) -> MasterRecordPage:
        if await self.access.can_see_private(principal):
            return page
        private = {field.name for field in detail.fields if field.is_pii}
        if not private:
            return page
        return replace(
            page,
            items=[
                replace(
                    record,
                    attributes={k: v for k, v in record.attributes.items() if k not in private},
                )
                for record in page.items
            ],
        )
