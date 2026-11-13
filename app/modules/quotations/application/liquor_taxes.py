"""The liquor tax of every price list: review it and change it in bulk (module
``listas-principales``).

The screen lists every active product with a liquor tax key, the value each regional
carries, every distinct value with its lists and the margin master's values, plus the
liquors of a liquor tax group that have no key. A bulk change sets the national and Coast
value of some SKUs and goes straight to SIESA, with no validation: only the collaborators
who manage the liquor tax (``impuesto-licor``) make it. It fixes at once the prices it writes
(every active list where the SKU has a price), so SIESA's two pages (prices and value taxes)
download right away; once loaded it is marked so, and the margin master takes the values.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.quotations.application.service import ExportFile, QuotationService
from app.modules.quotations.domain import liquor, vocabulary
from app.modules.quotations.domain.entities import ProductMaster
from app.modules.quotations.domain.liquor import (
    LiquorProduct,
    LiquorSummary,
    LiquorSuspect,
    LiquorTaxChange,
    LiquorTaxUpdate,
)
from app.modules.quotations.domain.ports import Calendar, ExportWriter, ManagerPolicy

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_STEP = Decimal("0.0001")


class LiquorSource(Protocol):
    async def liquor_products(self, skus: Sequence[str] | None = None) -> list[LiquorProduct]: ...

    async def liquor_suspects(self) -> list[LiquorSuspect]: ...


class LiquorMasters(Protocol):
    async def products(self, skus: Sequence[str]) -> dict[str, ProductMaster]: ...

    async def set_liquor_taxes(
        self, values: Mapping[str, tuple[Decimal, Decimal, str | None]], actor: str
    ) -> int: ...


class LiquorTaxRepository(Protocol):
    async def create(self, item: LiquorTaxUpdate) -> LiquorTaxUpdate: ...

    async def get(self, update_id: UUID) -> LiquorTaxUpdate | None: ...

    async def list(self, limit: int = 50) -> list[LiquorTaxUpdate]: ...

    async def save(self, item: LiquorTaxUpdate, expected_version: int) -> LiquorTaxUpdate: ...


@dataclass(frozen=True)
class LiquorOverview:
    summaries: tuple[LiquorSummary, ...]
    suspects: tuple[LiquorSuspect, ...]
    coast_lists: tuple[str, ...]
    # The caller is one of the collaborators who change the liquor tax.
    can_manage: bool = False


@dataclass(frozen=True)
class ChangeInput:
    sku: str
    national: Decimal | str | int
    coast: Decimal | str | int


class LiquorTaxService:
    """Review and bulk change of the liquor tax of every list."""

    def __init__(
        self,
        quotations: QuotationService,
        source: LiquorSource,
        masters: LiquorMasters,
        repository: LiquorTaxRepository,
        calendar: Calendar,
        workbook: ExportWriter,
        flat_file: ExportWriter,
        coast_lists: Sequence[str] = (),
        managers: ManagerPolicy | None = None,
    ) -> None:
        """Create the service.

        Args:
            quotations (QuotationService): Who reads the main lists.
            managers (ManagerPolicy | None): The collaborators who change the liquor tax and
                generate its files (``impuesto-licor``); without one only administrators.
            source (LiquorSource): The liquors and their prices in the warehouse.
            masters (LiquorMasters): The margin master (reads and the values written).
            repository (LiquorTaxRepository): The bulk changes.
            calendar (Calendar): Bogotá calendar.
            workbook (ExportWriter): SIESA workbook (``Precios`` and ``Impuestos en valor``).
            flat_file (ExportWriter): SIESA import file (``0126`` and ``0127``).
            coast_lists (Sequence[str]): Lists that pay the Coast value.
        """

        self.quotations = quotations
        self.managers = managers
        self.source = source
        self.masters = masters
        self.repository = repository
        self.calendar = calendar
        self.workbook = workbook
        self.flat_file = flat_file
        self.coast_lists = liquor.regional_lists(coast_lists)

    async def can_manage(self, principal: Principal) -> bool:
        """Whether the caller is one of the collaborators who manage the liquor tax
        (``manage`` on ``impuesto-licor``); without a policy only administrators are."""

        if principal.is_platform_admin:
            return True
        return self.managers is not None and await self.managers.is_manager(principal)

    async def _require(self, principal: Principal, action: str) -> None:
        if action == "manage":
            allowed = await self.can_manage(principal)
            message = "Solo los colaboradores del impuesto licor lo cambian y generan sus archivos"
        else:
            allowed = await self.quotations.can_read(
                principal, vocabulary.MAIN_LIST
            ) or await self.can_manage(principal)
            message = "No tienes permiso para consultar el impuesto licor"
        if not allowed:
            raise ForbiddenError(message)

    async def _summaries(self, skus: Sequence[str] | None = None) -> list[LiquorSummary]:
        products = await self.source.liquor_products(skus)
        masters = await self.masters.products([product.sku for product in products])
        return [
            liquor.summarize(product, self.coast_lists, masters.get(product.sku))
            for product in products
        ]

    async def overview(self, principal: Principal) -> LiquorOverview:
        """Every active liquor with its values per list, and the liquors without a key."""

        await self._require(principal, "read")
        summaries, suspects = await asyncio.gather(self._summaries(), self.source.liquor_suspects())
        return LiquorOverview(
            tuple(summaries),
            tuple(suspects),
            tuple(sorted(self.coast_lists)),
            await self.can_manage(principal),
        )

    async def create(
        self,
        principal: Principal,
        changes: Sequence[ChangeInput],
        note: str | None,
        activation_date: date | None = None,
    ) -> LiquorTaxUpdate:
        """New national and Coast values for some liquors, with their files ready at once.

        There is no validation: the collaborators who manage the liquor tax change it. The
        prices it writes are read now from every active list where each SKU has a price and
        fixed in the change, so SIESA's two pages can be downloaded right away.

        Raises:
            InvalidInputError: No changes, too many, a repeated or unknown SKU, a value that is
                not a number from 0, a date in the past, or no list prices any of the SKUs.
        """

        await self._require(principal, "manage")
        today = self.calendar.today()
        activation = activation_date or today + timedelta(days=1)
        if activation < today:
            raise InvalidInputError("La fecha de activación no puede ser anterior a hoy")
        if not changes:
            raise InvalidInputError("Elige los productos a cambiar")
        if len(changes) > liquor.MAX_CHANGES:
            raise InvalidInputError(f"Máximo {liquor.MAX_CHANGES} productos por cambio")
        skus = [change.sku.strip() for change in changes]
        if len(set(skus)) != len(skus):
            raise InvalidInputError("Hay productos repetidos en el cambio")
        summaries = {item.product.sku: item for item in await self._summaries(skus)}
        unknown = [sku for sku in skus if sku not in summaries]
        if unknown:
            raise InvalidInputError(
                f"El producto {unknown[0]} no está activo o no tiene llave de impuesto licor"
            )
        rows = []
        for change in changes:
            summary = summaries[change.sku.strip()]
            national = _tax(change.national, change.sku)
            coast = _tax(change.coast, change.sku)
            rows.append(
                LiquorTaxChange(
                    sku=summary.product.sku,
                    description=summary.product.description,
                    tax_key=summary.product.tax_key,
                    tax_group=summary.product.tax_group,
                    national=national,
                    coast=coast,
                    previous_national=summary.national,
                    previous_coast=summary.coast,
                    lists=summary.lists_with_price,
                )
            )
        text = " ".join((note or "").split()) or None
        if text is not None and len(text) > liquor.NOTE_LENGTH:
            raise InvalidInputError(f"La nota admite máximo {liquor.NOTE_LENGTH} caracteres")
        products = {product.sku: product for product in await self.source.liquor_products(skus)}
        export = liquor.export_rows(products, rows, self.coast_lists)
        if not export:
            raise InvalidInputError("Ninguna lista activa tiene precio para estos productos")
        counts = liquor.list_count(export)
        item = LiquorTaxUpdate(
            id=uuid4(),
            status=liquor.UPLOAD_PENDING,
            changes=tuple(replace(change, lists=counts.get(change.sku, 0)) for change in rows),
            export_rows=tuple(export),
            note=text,
            review_note=None,
            activation_date=activation,
            created_by=principal.email,
            created_at=datetime.now(UTC),
        )
        return await self.repository.create(item)

    async def list(self, principal: Principal) -> list[LiquorTaxUpdate]:
        await self._require(principal, "read")
        return await self.repository.list()

    async def get(self, principal: Principal, update_id: UUID) -> LiquorTaxUpdate:
        await self._require(principal, "read")
        item = await self.repository.get(update_id)
        if item is None:
            raise NotFoundError("Cambio de impuesto no encontrado")
        return item

    @staticmethod
    def _check(item: LiquorTaxUpdate, version: int, status: str) -> None:
        if item.version != version:
            raise ConflictError("El cambio de impuesto cambió desde que lo abriste; recárgalo")
        if item.status != status:
            raise ConflictError(
                f"El cambio está «{liquor.STATUS_LABELS.get(item.status, item.status)}»"
            )

    async def cancel(
        self, principal: Principal, update_id: UUID, version: int, note: str | None
    ) -> LiquorTaxUpdate:
        """Cancel a change that was not loaded in SIESA (its files must not be loaded)."""

        item = await self.get(principal, update_id)
        await self._require(principal, "manage")
        self._check(item, version, liquor.UPLOAD_PENDING)
        text = " ".join((note or "").split()) or None
        if text is not None and len(text) > liquor.NOTE_LENGTH:
            raise InvalidInputError(f"El motivo admite máximo {liquor.NOTE_LENGTH} caracteres")
        cancelled = replace(
            item, status=liquor.RETURNED, review_note=text, version=item.version + 1
        )
        return await self.repository.save(cancelled, version)

    async def file(self, principal: Principal, update_id: UUID, fmt: str) -> ExportFile:
        """SIESA's two pages of a change: workbook (``xlsx``) or import file (``txt``)."""

        item = await self.get(principal, update_id)
        await self._require(principal, "manage")
        if item.status not in (liquor.UPLOAD_PENDING, liquor.UPLOADED) or not item.activation_date:
            raise ConflictError("El cambio está anulado: sus archivos ya no se cargan")
        export = liquor.to_export(item.export_rows, item.activation_date)
        stamp = item.activation_date.strftime("%Y%m%d")
        if fmt == "xlsx":
            return ExportFile(
                await asyncio.to_thread(self.workbook.write, export),
                f"IMPUESTO LICOR LISTAS_{stamp}.xlsx",
                _XLSX,
            )
        if fmt != "txt":
            raise InvalidInputError("Formato desconocido: xlsx o txt")
        return ExportFile(
            await asyncio.to_thread(self.flat_file.write, export),
            f"impuesto-licor-listas-{stamp}.txt",
            "text/plain; charset=us-ascii",
        )

    async def mark_uploaded(
        self, principal: Principal, update_id: UUID, version: int
    ) -> LiquorTaxUpdate:
        """The files were loaded in SIESA: the margin master takes the new values."""

        item = await self.get(principal, update_id)
        await self._require(principal, "manage")
        self._check(item, version, liquor.UPLOAD_PENDING)
        now = datetime.now(UTC)
        uploaded = await self.repository.save(
            replace(
                item,
                status=liquor.UPLOADED,
                uploaded_by=principal.email,
                uploaded_at=now,
                version=item.version + 1,
            ),
            version,
        )
        await self.masters.set_liquor_taxes(
            {
                change.sku: (change.national, change.coast, change.tax_group)
                for change in item.changes
            },
            principal.email,
        )
        return uploaded


def _tax(value: Decimal | str | int, sku: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidInputError(f"El impuesto de {sku} debe ser un número")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InvalidInputError(f"El impuesto de {sku} debe ser un número") from exc
    if not number.is_finite() or number < 0 or number >= liquor.MAX_TAX:
        raise InvalidInputError(f"El impuesto de {sku} va de 0 en adelante")
    return number.quantize(_STEP, ROUND_HALF_UP)


__all__ = ["ChangeInput", "LiquorOverview", "LiquorTaxService"]
