"""Prospects use cases: the map, the list, a prospect's card, hand edits and the monthly load."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.core.errors import InvalidInputError, NotFoundError, PayloadTooLargeError
from app.modules.identity.domain.principal import Principal
from app.modules.territory.domain.entities import (
    ImportReport,
    LoadCounts,
    MapPoint,
    ParsedFile,
    Point,
    Prospect,
    ProspectChange,
    ProspectData,
    ProspectFacets,
    ProspectFilters,
    ProspectLoad,
    ProspectPage,
    RowIssue,
    SheetSummary,
)
from app.modules.territory.domain.places import fold, mentions, municipality_in_address
from app.modules.territory.domain.ports import (
    ManagerPolicy,
    ProspectFileReader,
    ProspectRepository,
)
from app.modules.territory.domain.vocabulary import (
    MANUAL,
    MISSING,
    VISIT_STATUSES,
    in_colombia,
)

MAX_ERRORS = 200
_EXTENSIONS = (".xlsx", ".xlsm")


def _sheets(parsed: ParsedFile, rows: list[ProspectData]) -> list[SheetSummary]:
    """Per-sheet counts after the points the load leaves out."""

    summaries = []
    for sheet in parsed.sheets:
        in_sheet = [
            row for row, (name, _) in zip(rows, parsed.positions, strict=True) if name == sheet.name
        ]
        located = sum(1 for row in in_sheet if row.location is not None)
        summaries.append(
            SheetSummary(
                name=sheet.name,
                rows=len(in_sheet),
                with_location=located,
                without_location=len(in_sheet) - located,
            )
        )
    return summaries


class ProspectService:
    """Read for anyone with ``read`` of the module; load and edit with ``manage``."""

    def __init__(
        self,
        repository: ProspectRepository,
        reader: ProspectFileReader,
        managers: ManagerPolicy,
        max_upload_bytes: int,
    ) -> None:
        """Create the service.

        Args:
            repository (ProspectRepository): Prospects storage.
            reader (ProspectFileReader): Reader of the monthly workbook.
            managers (ManagerPolicy): Who manages ``mapa-comercial``.
            max_upload_bytes (int): Largest file accepted.
        """

        self.repository = repository
        self.reader = reader
        self.managers = managers
        self.max_upload_bytes = max_upload_bytes

    async def permissions(self, principal: Principal) -> tuple[bool, bool]:
        """What the caller may do.

        Args:
            principal (Principal): The caller.

        Returns:
            tuple[bool, bool]: ``(can_read, can_manage)``.
        """

        can_manage = await self.managers.is_manager(principal)
        return can_manage or await self.managers.can_read(principal), can_manage

    async def map_points(self, filters: ProspectFilters) -> list[MapPoint]:
        """The located prospects that match the filters.

        Args:
            filters (ProspectFilters): Filters of the map.

        Returns:
            list[MapPoint]: Light rows for the map.
        """

        return await self.repository.map_points(filters)

    async def page(self, filters: ProspectFilters, page: int, size: int) -> ProspectPage:
        """A page of prospects, located or not.

        Args:
            filters (ProspectFilters): Filters of the list.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            ProspectPage: The page.
        """

        return await self.repository.page(filters, page, size)

    async def facets(self) -> ProspectFacets:
        """Values of the filters with their counts.

        Returns:
            ProspectFacets: Zones, categories and located counts.
        """

        return await self.repository.facets()

    async def get(self, prospect_id: UUID) -> Prospect:
        """One prospect, for its card.

        Args:
            prospect_id (UUID): Its id.

        Returns:
            Prospect: The prospect.

        Raises:
            NotFoundError: When it does not exist.
        """

        prospect = await self.repository.get(prospect_id)
        if prospect is None:
            raise NotFoundError("Prospecto no encontrado")
        return prospect

    async def update(
        self,
        principal: Principal,
        prospect_id: UUID,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        visit_status: str | None = None,
        visit_note: str | None = None,
        set_note: bool = False,
    ) -> Prospect:
        """Move a prospect's point or record the result of a visit (``manage``).

        Args:
            principal (Principal): The caller.
            prospect_id (UUID): The prospect.
            latitude (float | None): New latitude, with ``longitude``.
            longitude (float | None): New longitude, with ``latitude``.
            visit_status (str | None): New visit status.
            visit_note (str | None): New note.
            set_note (bool): Whether the note was sent (``None`` clears it).

        Returns:
            Prospect: The prospect after the change.

        Raises:
            ForbiddenError: Without ``manage``.
            InvalidInputError: With half a point, a point outside Colombia or an unknown status.
            NotFoundError: When it does not exist.
        """

        await self.managers.require(principal)
        if (latitude is None) != (longitude is None):
            raise InvalidInputError("Envía la latitud y la longitud juntas")
        location = None
        if latitude is not None and longitude is not None:
            if not in_colombia(latitude, longitude):
                raise InvalidInputError("La ubicación está fuera de Colombia")
            location = Point(latitude=latitude, longitude=longitude)
        if visit_status is not None and visit_status not in VISIT_STATUSES:
            raise InvalidInputError(f"Estado de visita desconocido: {visit_status}")
        prospect = await self.repository.update(
            prospect_id,
            ProspectChange(
                location=location,
                visit_status=visit_status,
                visit_note=(visit_note or "").strip() or None,
                set_note=set_note,
            ),
        )
        if prospect is None:
            raise NotFoundError("Prospecto no encontrado")
        return prospect

    async def load(
        self, principal: Principal, file_name: str, content: bytes, *, apply: bool
    ) -> ImportReport:
        """Simulate the monthly file and, with ``apply`` and no errors, store it (``manage``).

        Creates or updates by the file's ``ID``; prospects the file no longer brings are left
        as they are. A point moved by hand and the visit results are kept.

        Args:
            principal (Principal): The caller.
            file_name (str): Uploaded file name.
            content (bytes): The workbook (one byte over the limit is enough to reject it).
            apply (bool): Write when there are no errors.

        Returns:
            ImportReport: What the file brings and what the load does (or did).

        Raises:
            ForbiddenError: Without ``manage``.
            PayloadTooLargeError: Over the size limit.
            InvalidInputError: When it is not a prospects workbook.
        """

        await self.managers.require(principal)
        if len(content) > self.max_upload_bytes:
            megabytes = self.max_upload_bytes // (1024 * 1024)
            raise PayloadTooLargeError(f"El archivo supera el máximo de {megabytes} MB")
        if not file_name.lower().endswith(_EXTENSIONS):
            raise InvalidInputError(
                "Formato no soportado: sube el archivo de prospectos en Excel (.xlsx)"
            )
        parsed = self.reader.read(content)
        rows, mismatches = await self._check_municipalities(parsed)
        warnings = parsed.warnings + mismatches
        stored = await self.repository.stored([row.external_id for row in rows])
        created = updated = unchanged = 0
        for row in rows:
            current = stored.get(row.external_id)
            if current is None:
                created += 1
                continue
            incoming = row
            if current.location_quality == MANUAL:
                incoming = replace(row, location=current.location, location_quality=MANUAL)
            if incoming.comparable() == current.comparable():
                unchanged += 1
            else:
                updated += 1
        blocked = bool(parsed.errors)
        load_id = None
        if apply and not blocked:
            load_id = await self.repository.apply_load(
                file_name,
                rows,
                LoadCounts(created=created, updated=updated, unchanged=unchanged),
                principal.user_id,
                principal.email,
            )
        located = sum(1 for row in rows if row.location is not None)
        return ImportReport(
            applied=load_id is not None,
            blocked=blocked,
            load_id=load_id,
            rows_read=parsed.rows_read,
            with_location=located,
            without_location=len(rows) - located,
            approximate=sum(1 for row in rows if row.location_quality == "approximate"),
            to_create=created,
            to_update=updated,
            unchanged=unchanged,
            sheets=_sheets(parsed, rows),
            errors=parsed.errors[:MAX_ERRORS],
            errors_truncated=len(parsed.errors) > MAX_ERRORS,
            discarded_points=len(warnings),
            warnings=warnings[:MAX_ERRORS],
            warnings_truncated=len(warnings) > MAX_ERRORS,
        )

    async def _check_municipalities(
        self, parsed: ParsedFile
    ) -> tuple[list[ProspectData], list[RowIssue]]:
        """Drop the points that fall in another municipality than the address names.

        An agent may geocode a Bello address with Medellín's street grid. Only checked when the
        official municipalities are loaded and the address ends with one of them.
        """

        rows = list(parsed.rows)
        candidates = [
            (index, named)
            for index, row in enumerate(rows)
            if row.location is not None and (named := municipality_in_address(row.address))
        ]
        if not candidates:
            return rows, []
        found = await self.repository.municipalities(
            [rows[index].location for index, _ in candidates]  # type: ignore[misc]
        )
        warnings = []
        for (index, named), actual in zip(candidates, found, strict=True):
            row = rows[index]
            # Consistent when the address also names where the point is ("Envigado - Caldas").
            if actual is None or fold(actual) == fold(named) or mentions(row.address, actual):
                continue
            rows[index] = replace(row, location=None, location_quality=MISSING)
            sheet, row_number = parsed.positions[index]
            warnings.append(
                RowIssue(
                    sheet,
                    row_number,
                    "Dirección",
                    f"{row.external_id}: la dirección dice {named} pero el punto cae en "
                    f"{actual}; queda por ubicar",
                )
            )
        return rows, warnings

    async def loads(self, principal: Principal, limit: int = 20) -> list[ProspectLoad]:
        """The latest loads (``manage``).

        Args:
            principal (Principal): The caller.
            limit (int): How many.

        Returns:
            list[ProspectLoad]: Newest first.

        Raises:
            ForbiddenError: Without ``manage``.
        """

        await self.managers.require(principal)
        return await self.repository.loads(limit)


__all__ = ["MAX_ERRORS", "ProspectService"]
