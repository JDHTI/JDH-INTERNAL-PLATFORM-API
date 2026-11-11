"""Use cases of the price increase history: read it, upload the workbook, set SIESA's date.

Reading is open to whoever reads the commercial masters (the router's gate). Uploading and
changing the system update date need the masters' manager permission (BI, module
``maestros``). An upload is a simulation unless ``apply``: it says what it would insert,
update and leave as it is, and what it could not read, before anything is written.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.commercial_masters.domain.ports import ManagerPolicy
from app.modules.commercial_masters.domain.price_increases import (
    EARLIEST_DATE,
    STATUSES,
    FileRow,
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreasePage,
    PriceIncreaseValues,
    UploadIssue,
    UploadReport,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    PriceIncreaseFileReader,
)
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 100
MAX_REPORTED_ISSUES = 200


class PriceIncreaseService:
    """The price increase history of the products."""

    def __init__(
        self,
        repository: SqlAlchemyPriceIncreaseRepository,
        managers: ManagerPolicy,
        reader: PriceIncreaseFileReader,
        max_upload_bytes: int,
    ) -> None:
        """Create the service.

        Args:
            repository (SqlAlchemyPriceIncreaseRepository): Stored rows.
            managers (ManagerPolicy): Who manages the commercial masters.
            reader (PriceIncreaseFileReader): Reads the workbook.
            max_upload_bytes (int): Largest workbook accepted.
        """

        self.repository = repository
        self.managers = managers
        self.reader = reader
        self.max_upload_bytes = max_upload_bytes

    async def can_manage(self, principal: Principal) -> bool:
        return await self.managers.is_manager(principal)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.can_manage(principal):
            raise ForbiddenError("No tienes permiso para actualizar el histórico de alzas")

    async def page(self, filters: PriceIncreaseFilters, page: int, size: int) -> PriceIncreasePage:
        """A page of the history, the latest increase first, with the counts of the filter."""

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if filters.status is not None and filters.status not in STATUSES:
            raise InvalidInputError("Estado desconocido: subio, bajo o estable")
        return await self.repository.page(filters, page, size)

    async def upload(
        self, principal: Principal, filename: str | None, content: bytes, *, apply: bool
    ) -> UploadReport:
        """Upsert the workbook's rows by reference, increase date and notification date.

        A key repeated in the file keeps its last row. Rows of references the product master
        does not have are kept too (the history outlives the products) and counted. With
        ``apply`` false nothing is written.

        Raises:
            ForbiddenError: The caller does not manage the masters.
            InvalidInputError: Empty, too large or unreadable file.
        """

        await self._require_manager(principal)
        if not content:
            raise InvalidInputError("El archivo está vacío")
        if len(content) > self.max_upload_bytes:
            megas = self.max_upload_bytes // (1024 * 1024)
            raise InvalidInputError(f"El archivo pasa de {megas} MB")
        # openpyxl is synchronous: in a thread, a 50 000-row file never holds up the API.
        rows = await asyncio.to_thread(self.reader.read, filename, content)
        by_key: dict[str, FileRow] = {}
        for row in rows:
            by_key[row.values.key] = row
        duplicated = len(rows) - len(by_key)
        stored = await self.repository.existing(list(by_key))
        known = await self.repository.known_skus(sorted({row.values.sku for row in rows}))
        inserted: list[PriceIncreaseValues] = []
        updated: list[PriceIncreaseValues] = []
        for key, row in by_key.items():
            current = stored.get(key)
            if current is None:
                inserted.append(row.values)
            elif current.comparable() != row.values.comparable():
                updated.append(row.values)
        issues = [
            UploadIssue(row.row_number, row.values.sku, problem)
            for row in rows
            for problem in row.problems
        ]
        if apply:
            await self.repository.upsert([*inserted, *updated], principal.email, datetime.now(UTC))
        return UploadReport(
            applied=apply,
            rows_read=len(rows),
            inserted=len(inserted),
            updated=len(updated),
            unchanged=len(by_key) - len(inserted) - len(updated),
            duplicated=duplicated,
            unknown_skus=len({row.values.sku for row in rows} - known),
            pending=sum(1 for row in by_key.values() if row.values.system_updated_on is None),
            issues=tuple(issues[:MAX_REPORTED_ISSUES]),
            issues_truncated=len(issues) > MAX_REPORTED_ISSUES,
        )

    async def set_system_date(
        self, principal: Principal, increase_id: UUID, day: date | None
    ) -> PriceIncrease:
        """Store the day SIESA got the increase; ``None`` marks it pending again.

        Raises:
            ForbiddenError: The caller does not manage the masters.
            InvalidInputError: A day before 2000.
            NotFoundError: The row does not exist.
        """

        await self._require_manager(principal)
        if day is not None and day < EARLIEST_DATE:
            raise InvalidInputError("La fecha de actualización es anterior al año 2000")
        await self.repository.set_system_date(increase_id, day, principal.email, datetime.now(UTC))
        return await self.repository.get(increase_id)


__all__ = ["MAX_PAGE_SIZE", "PriceIncreaseService"]
