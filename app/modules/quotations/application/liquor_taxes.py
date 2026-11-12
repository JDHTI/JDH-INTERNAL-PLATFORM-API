"""The liquor tax of every price list: review it and change it in bulk (module
``listas-principales``).

The screen lists every active product with a liquor tax key, the value each regional
carries, every distinct value with its lists and the margin master's values, plus the
liquors of a liquor tax group that have no key. A bulk change sets the national and Coast
value of some SKUs; who validates the main lists validates it, which fixes the prices it
writes (every active list where the SKU has a price), downloads SIESA's two pages (prices
and value taxes), loads them and marks it loaded; then the margin master takes the values.
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
from app.modules.quotations.domain.ports import Calendar, ExportWriter

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
    ) -> None:
        """Create the service.

        Args:
            quotations (QuotationService): Who manages and validates the main lists.
            source (LiquorSource): The liquors and their prices in the warehouse.
            masters (LiquorMasters): The margin master (reads and the values written).
            repository (LiquorTaxRepository): The bulk changes.
            calendar (Calendar): Bogotá calendar.
            workbook (ExportWriter): SIESA workbook (``Precios`` and ``Impuestos en valor``).
            flat_file (ExportWriter): SIESA import file (``0126`` and ``0127``).
            coast_lists (Sequence[str]): Lists that pay the Coast value.
        """

        self.quotations = quotations
        self.source = source
        self.masters = masters
        self.repository = repository
        self.calendar = calendar
        self.workbook = workbook
        self.flat_file = flat_file
        self.coast_lists = liquor.regional_lists(coast_lists)

    async def _require(self, principal: Principal, action: str) -> None:
        kind = vocabulary.MAIN_LIST
        if action == "validate":
            allowed = await self.quotations.can_validate(principal, kind)
            message = "Solo quien valida las listas principales valida y carga el impuesto"
        elif action == "manage":
            allowed = await self.quotations.can_manage(principal, kind)
            message = "No tienes permiso para cambiar el impuesto licor"
        else:
            allowed = await self.quotations.can_read(principal, kind)
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
        return LiquorOverview(tuple(summaries), tuple(suspects), tuple(sorted(self.coast_lists)))

    async def create(
        self, principal: Principal, changes: Sequence[ChangeInput], note: str | None
    ) -> LiquorTaxUpdate:
        """Propose new national and Coast values for some liquors; it waits for validation.

        Raises:
            InvalidInputError: No changes, too many, a repeated or unknown SKU, or a value
                that is not a number from 0.
        """

        await self._require(principal, "manage")
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
        item = LiquorTaxUpdate(
            id=uuid4(),
            status=liquor.PENDING,
            changes=tuple(rows),
            export_rows=(),
            note=text,
            review_note=None,
            activation_date=None,
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

    async def validate(
        self, principal: Principal, update_id: UUID, version: int, activation_date: date | None
    ) -> LiquorTaxUpdate:
        """Accept the change: the prices it writes are read from the lists now and fixed.

        Raises:
            ConflictError: Stale version, or not pending validation.
            InvalidInputError: A date in the past, or no list prices any of its SKUs.
        """

        item = await self.get(principal, update_id)
        await self._require(principal, "validate")
        self._check(item, version, liquor.PENDING)
        today = self.calendar.today()
        activation = activation_date or today + timedelta(days=1)
        if activation < today:
            raise InvalidInputError("La fecha de activación no puede ser anterior a hoy")
        skus = [change.sku for change in item.changes]
        products = {product.sku: product for product in await self.source.liquor_products(skus)}
        rows = liquor.export_rows(products, item.changes, self.coast_lists)
        if not rows:
            raise InvalidInputError("Ninguna lista activa tiene precio para estos productos")
        counts = liquor.list_count(rows)
        changes = tuple(replace(change, lists=counts.get(change.sku, 0)) for change in item.changes)
        now = datetime.now(UTC)
        validated = replace(
            item,
            status=liquor.UPLOAD_PENDING,
            changes=changes,
            export_rows=tuple(rows),
            activation_date=activation,
            validated_by=principal.email,
            validated_at=now,
            version=item.version + 1,
        )
        return await self.repository.save(validated, version)

    async def return_(
        self, principal: Principal, update_id: UUID, version: int, note: str | None
    ) -> LiquorTaxUpdate:
        """Turn the change down (who validates, with a reason) or withdraw it (its author)."""

        item = await self.get(principal, update_id)
        self._check(item, version, liquor.PENDING)
        validator = await self.quotations.can_validate(principal, vocabulary.MAIN_LIST)
        author = item.created_by == principal.email
        if not validator and not author:
            raise ForbiddenError("Solo quien valida o quien lo propuso retira el cambio")
        text = " ".join((note or "").split()) or None
        if not author and text is None:
            raise InvalidInputError("Escribe por qué devuelves el cambio")
        returned = replace(item, status=liquor.RETURNED, review_note=text, version=item.version + 1)
        return await self.repository.save(returned, version)

    async def file(self, principal: Principal, update_id: UUID, fmt: str) -> ExportFile:
        """SIESA's two pages of a validated change: workbook (``xlsx``) or file (``txt``)."""

        item = await self.get(principal, update_id)
        await self._require(principal, "validate")
        if item.status not in (liquor.UPLOAD_PENDING, liquor.UPLOADED) or not item.activation_date:
            raise ConflictError("Los archivos se generan cuando el cambio está validado")
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
        await self._require(principal, "validate")
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
