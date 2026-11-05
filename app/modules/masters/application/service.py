"""Use cases for reading the shared master catalog.

Administrators and moderators read every master. Anyone else reads the masters
switched on in the areas where their role grants ``maestros`` (read or manage); fields
marked ``is_pii`` are shown only to whoever manages the module or administers the
platform.
"""

from __future__ import annotations

from dataclasses import replace

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterRecord,
    MasterRecordPage,
    MasterSummary,
    clean_master_slug,
)
from app.modules.masters.domain.ports import MasterAccess, MasterRepository

MAX_PAGE_SIZE = 200
# A download stops here; clientes, the largest master, has about 30 000 rows.
MAX_EXPORT_ROWS = 100_000
_EXPORT_PAGE = 2000


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

    def __init__(self, repository: MasterRepository, access: MasterAccess | None = None) -> None:
        """Create the service.

        Args:
            repository (MasterRepository): Master persistence port.
            access (MasterAccess | None): Who sees which master; every master when
                not given.
        """

        self.repository = repository
        self.access = access or _EveryMaster()

    async def _visible(self, principal: Principal) -> frozenset[str] | None:
        return await self.access.visible_masters(principal)

    async def _require_visible(self, principal: Principal, slug: str) -> None:
        visible = await self._visible(principal)
        if visible is not None and slug not in visible:
            raise ForbiddenError("No tienes acceso a este maestro")

    async def list_masters(
        self, principal: Principal, area_slug: str | None = None
    ) -> list[MasterSummary]:
        """List the active masters the person may read.

        Args:
            principal (Principal): The caller.
            area_slug (str | None): Only the masters switched on in this area.

        Returns:
            list[MasterSummary]: In sort order, with their record counts.
        """

        only = await self.access.area_masters(area_slug) if area_slug else None
        visible = await self._visible(principal)
        return [
            summary
            for summary in await self.repository.list_masters()
            if (visible is None or summary.slug in visible)
            and (only is None or summary.slug in only)
        ]

    async def get_master(self, slug: str, principal: Principal) -> MasterDetail:
        """Return one master with its fields or raise not found."""

        slug = clean_master_slug(slug)
        await self._require_visible(principal, slug)
        detail = await self.repository.get_master(slug)
        if detail is None:
            raise NotFoundError("Maestro no encontrado")
        if await self.access.can_see_private(principal):
            return detail
        return replace(detail, fields=[field for field in detail.fields if not field.is_pii])

    async def list_records(
        self,
        slug: str,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
        principal: Principal,
    ) -> MasterRecordPage:
        """Search a master's records by code or name."""

        if offset < 0:
            raise InvalidInputError("El desplazamiento no puede ser negativo")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El límite debe estar entre 1 y {MAX_PAGE_SIZE}")
        slug = clean_master_slug(slug)
        await self._require_visible(principal, slug)
        term = " ".join(query.split()) if query else None
        page = await self.repository.list_records(slug, term or None, is_active, offset, limit)
        if page is None:
            raise NotFoundError("Maestro no encontrado")
        return await self._without_private(slug, page, principal)

    async def export(
        self, slug: str, is_active: bool | None, principal: Principal
    ) -> tuple[MasterDetail, list[MasterRecord]]:
        """Every record of a master (up to :data:`MAX_EXPORT_ROWS`) for a download.

        Args:
            slug (str): Master slug.
            is_active (bool | None): Only active or inactive records.
            principal (Principal): The caller.

        Returns:
            tuple[MasterDetail, list[MasterRecord]]: The fields to show and the records.
        """

        detail = await self.get_master(slug, principal)
        records: list[MasterRecord] = []
        while len(records) < MAX_EXPORT_ROWS:
            page = await self.repository.list_records(
                detail.summary.slug, None, is_active, len(records), _EXPORT_PAGE
            )
            if page is None or not page.items:
                break
            records.extend(page.items)
            if len(page.items) < _EXPORT_PAGE:
                break
        page = MasterRecordPage(records[:MAX_EXPORT_ROWS], len(records), 0, len(records))
        return detail, (await self._without_private(detail.summary.slug, page, principal)).items

    async def _without_private(
        self, slug: str, page: MasterRecordPage, principal: Principal
    ) -> MasterRecordPage:
        if await self.access.can_see_private(principal):
            return page
        detail = await self.repository.get_master(slug)
        private = {field.name for field in detail.fields if field.is_pii} if detail else set()
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
