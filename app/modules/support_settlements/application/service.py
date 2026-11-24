"""The settlement of a month: calculate, read, close and reopen.

- BI reads and downloads with ``read`` on ``liquidacion-apoyos``; ``manage`` recalculates and
  closes. Only the area's leader (or a platform administrator) reopens a closed month.
- A month is settled whole and finished (in November, October); the current month can be
  calculated as a preview. Months before ``start_period`` are not settled (the supports of
  then were not loaded in the platform).
- A closed month is a frozen picture: later edits to supports or sales never change it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.support_settlements.domain import periods
from app.modules.support_settlements.domain.entities import (
    BREAKDOWNS,
    CLOSED,
    ITEM_STATUSES,
    NO_SUPPLIER,
    Settlement,
    SettlementResult,
)
from app.modules.support_settlements.domain.ports import (
    LeaderPolicy,
    Policy,
    SalesSource,
    SettlementMasters,
    SettlementRepository,
)
from app.modules.support_settlements.domain.rules import build_settlement

logger = logging.getLogger(__name__)
SYSTEM_ACTOR = "liquidacion-automatica"
BOGOTA = ZoneInfo("America/Bogota")
MAX_PAGE_SIZE = 500


@dataclass(frozen=True)
class Permissions:
    can_read: bool
    can_manage: bool
    can_reopen: bool


@dataclass(frozen=True)
class PeriodView:
    """A month the screen offers: stored or not yet calculated."""

    period: str
    settlement: Settlement | None
    is_finished: bool
    is_current: bool


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str


class SettlementService:
    """Settle the supplier supports of a month."""

    def __init__(
        self,
        repository: SettlementRepository,
        masters: SettlementMasters,
        sales: SalesSource,
        policy: Policy,
        leaders: LeaderPolicy,
        start_period: str = "2026-10",
        writer: Callable[..., bytes] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (SettlementRepository): Stored settlements.
            masters (SettlementMasters): Supports, products and customers of the platform.
            sales (SalesSource): The month's sales and quotations from the warehouse.
            policy (Policy): ``liquidacion-apoyos``.
            leaders (LeaderPolicy): Who reopens a closed month.
            start_period (str): First month settled in the platform.
            writer (Callable[..., bytes] | None): Builds the workbook.
            today (Callable[[], date] | None): The day in Bogotá (tests).
        """

        periods.bounds(start_period)
        self.repository = repository
        self.masters = masters
        self.sales = sales
        self.policy = policy
        self.leaders = leaders
        self.start_period = start_period
        self.writer = writer
        self._today = today or (lambda: datetime.now(BOGOTA).date())

    # --- access ---

    async def permissions(self, principal: Principal) -> Permissions:
        manage = await self.policy.is_manager(principal)
        read = manage or await self.policy.can_read(principal)
        return Permissions(read, manage, await self.leaders.is_manager(principal))

    async def _require_read(self, principal: Principal) -> None:
        if not await self.policy.can_read(principal) and not await self.policy.is_manager(
            principal
        ):
            raise ForbiddenError("No tienes permiso para consultar la liquidación de apoyos")

    async def _require_manage(self, principal: Principal) -> None:
        if not await self.policy.is_manager(principal):
            raise ForbiddenError("No tienes permiso para calcular o cerrar la liquidación")

    def _check_period(self, period: str) -> str:
        period = period.strip()
        start, _ = periods.bounds(period)
        if period < self.start_period:
            raise InvalidInputError(
                f"La liquidación empieza en {self.start_period}: los apoyos de antes no están "
                "cargados en la plataforma"
            )
        if start > self._today():
            raise InvalidInputError("No se liquida un mes que no ha empezado")
        return period

    async def _settlement(self, period: str) -> Settlement:
        found = await self.repository.get(self._check_period(period))
        if found is None:
            raise NotFoundError(f"La liquidación de {period} no se ha calculado")
        return found

    # --- calculation ---

    async def calculate(self, period: str) -> SettlementResult:
        """Settle the month from the supports, the sales and the quotations, without storing
        it."""

        start, following = periods.bounds(period)
        supports = await self.masters.supports(start, following)
        codes = sorted(
            {item.supplier_code for item in supports if item.supplier_code and not item.product_sku}
        )
        skus = sorted({item.product_sku for item in supports if item.product_sku})
        products = await self.masters.products(codes, skus)
        keys = sorted(
            {product.producto_key for product in products.values() if product.producto_key}
        )
        sales = await self.sales.sales(start, following, keys)
        quotes = await self.sales.quotes(start, following, keys)
        customers = await self.masters.customers(
            sorted({line.customer_key for line in sales if line.customer_key})
        )
        return build_settlement(
            sales=sales, supports=supports, products=products, customers=customers, quotes=quotes
        )

    async def recalculate(self, principal: Principal, period: str) -> Settlement:
        """Calculate the month again and store it in place of the previous calculation.

        Raises:
            ConflictError: The month is closed.
        """

        await self._require_manage(principal)
        period = self._check_period(period)
        current = await self.repository.get(period)
        if current is not None and current.status == CLOSED:
            raise ConflictError(
                f"La liquidación de {period} está cerrada; reábrela para recalcular"
            )
        return await _detached(self._store(period, principal.email))

    async def _store(self, period: str, actor: str, close: bool = False) -> Settlement:
        result = await self.calculate(period)
        return await self.repository.replace(period, result, actor, datetime.now(UTC), close=close)

    async def close(self, principal: Principal, period: str) -> Settlement:
        """Calculate the finished month one last time and freeze it.

        Raises:
            InvalidInputError: The month has not finished.
            ConflictError: It is already closed.
        """

        await self._require_manage(principal)
        period = self._check_period(period)
        if not periods.is_finished(period, self._today()):
            raise InvalidInputError("Solo se cierra un mes terminado (mes vencido)")
        return await _detached(self._store(period, principal.email, close=True))

    async def reopen(self, principal: Principal, period: str) -> Settlement:
        """Put a closed month back to draft (the area's leader only)."""

        if not await self.leaders.is_manager(principal):
            raise ForbiddenError("Solo el líder del área reabre una liquidación cerrada")
        return await self.repository.reopen(
            self._check_period(period), principal.email, datetime.now(UTC)
        )

    async def refresh_open(self) -> Mapping[str, Any]:
        """The daily job: recalculate the current month and the previous one when open."""

        today = self._today()
        done: dict[str, Any] = {}
        for period in dict.fromkeys((periods.previous_period(today), periods.period_of(today))):
            if period < self.start_period:
                continue
            current = await self.repository.get(period)
            if current is not None and current.status == CLOSED:
                done[period] = "cerrado"
                continue
            result = await self.calculate(period)
            stored = await self.repository.replace(period, result, SYSTEM_ACTOR, datetime.now(UTC))
            done[period] = {"lineas": stored.supported_lines, "apoyo": str(stored.amount)}
        return {"periodos": done}

    # --- reading ---

    async def periods(self, principal: Principal) -> list[PeriodView]:
        """The stored months, plus the current one and the previous one even when not
        calculated yet, the newest first."""

        await self._require_read(principal)
        today = self._today()
        stored = {item.period: item for item in await self.repository.list()}
        wanted = set(stored) | {
            period
            for period in (periods.period_of(today), periods.previous_period(today))
            if period >= self.start_period
        }
        current = periods.period_of(today)
        return [
            PeriodView(
                period=period,
                settlement=stored.get(period),
                is_finished=periods.is_finished(period, today),
                is_current=period == current,
            )
            for period in sorted(wanted, reverse=True)
        ]

    async def summary(
        self, principal: Principal, period: str
    ) -> tuple[Settlement, list[Mapping[str, Any]]]:
        await self._require_read(principal)
        settlement = await self._settlement(period)
        return settlement, await self.repository.suppliers(settlement.id)

    async def references(
        self, principal: Principal, period: str, supplier_code: str | None
    ) -> tuple[Settlement, list[Mapping[str, Any]]]:
        await self._require_read(principal)
        settlement = await self._settlement(period)
        return settlement, await self.repository.references(settlement.id, supplier_code)

    async def lines(
        self,
        principal: Principal,
        period: str,
        *,
        supplier_code: str | None = None,
        sku: str | None = None,
        nit: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 100,
    ) -> tuple[list[Mapping[str, Any]], int]:
        await self._require_read(principal)
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página o tamaño inválido (máximo {MAX_PAGE_SIZE})")
        if status is not None and status not in ITEM_STATUSES:
            raise InvalidInputError(f"Estado desconocido: {status}")
        settlement = await self._settlement(period)
        return await self.repository.lines(
            settlement.id,
            supplier_code=_clean(supplier_code),
            sku=_clean(sku),
            nit=_clean(nit),
            status=status,
            page=page,
            size=size,
        )

    async def breakdown(
        self, principal: Principal, period: str, by: str, supplier_code: str | None
    ) -> list[Mapping[str, Any]]:
        await self._require_read(principal)
        if by not in BREAKDOWNS:
            raise InvalidInputError(f"Desglose desconocido (válidos: {', '.join(BREAKDOWNS)})")
        settlement = await self._settlement(period)
        return await self.repository.breakdown(settlement.id, by, _clean(supplier_code))

    async def export(
        self, principal: Principal, period: str, supplier_code: str | None
    ) -> ExportFile:
        """The workbook of the month: one supplier, or all of them."""

        await self._require_read(principal)
        if self.writer is None:
            raise InvalidInputError("La descarga no está disponible")
        settlement = await self._settlement(period)
        code = _clean(supplier_code)
        sales, references = await self.repository.export_rows(settlement.id, code)
        content = self.writer(
            settlement.period,
            "Cerrado" if settlement.status == CLOSED else "Borrador",
            references,
            sales,
        )
        name = f"liquidacion-apoyos-{settlement.period}"
        if code:
            name += "-sin-proveedor" if code == NO_SUPPLIER else f"-proveedor-{code}"
        return ExportFile(content, f"{name}.xlsx")


_RUNNING: set[asyncio.Task] = set()


async def _detached(work: Coroutine[Any, Any, Settlement]) -> Settlement:
    """Run a calculation to the end even if the request is cancelled (the client or a proxy
    hung up after minutes reading the DWH): the month is stored all the same, and asking again
    shows it."""

    task = asyncio.ensure_future(work)
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return await asyncio.shield(task)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


__all__ = ["ExportFile", "PeriodView", "Permissions", "SettlementService"]
