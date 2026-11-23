"""Rules and persistence boundary for JDH support requests."""

from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ConflictError, InvalidInputError
from app.modules.supplier_supports.domain.validity import validity_on

# Rules v2: JDH supports a support with an authorized minimum margin (Excepción margen
# mínimo autorizado JDH, ``supported_margin_pct``). The JDH % and the JDH net cost are the
# earlier modes: their pending requests can still be decided, no new one is created.
MARGIN_MODE = "margen_minimo"
# A % Margen Garantizado asks the supplier, not JDH (2026-11-23): a margin under the
# supplier's guaranteed minimum (``supplier_min_margin_pct``), negative allowed. It is the
# supplier's exception, not profitability JDH gives up.
SUPPLIER_MARGIN_MODE = "margen_proveedor"
LEGACY_MODES = ("porcentaje", "costo_neto")
GUARANTEED_METHOD = "precio_neto"


def request_mode(calc_method: str) -> str:
    """A % Margen Garantizado asks for the supplier's minimum margin; every other type asks
    JDH for the authorized minimum margin."""

    return SUPPLIER_MARGIN_MODE if calc_method == GUARANTEED_METHOD else MARGIN_MODE


def require_below_supplier_minimum(support: Any, value: Decimal) -> None:
    """A supplier margin request asks for less than the margin in force: the supplier's
    minimum when set, else the guaranteed margin.

    Raises:
        InvalidInputError: When it asks for that margin or more.
    """

    current = support.supplier_min_margin_pct
    if current is None:
        current = support.target_margin_pct
    if current is not None and value >= current:
        raise InvalidInputError(
            f"El margen pedido debe ser menor que el margen mínimo del proveedor vigente "
            f"({current * 100:.2f} %)"
        )


def legacy_mode(calc_method: str) -> str:
    """The mode a request had before rules v2 (to decide one still pending)."""

    return "costo_neto" if calc_method == "costo_neto" else "porcentaje"


def validate_value(mode: str, value: Decimal) -> None:
    if not value.is_finite() or value.as_tuple().exponent < -6:
        raise InvalidInputError("El valor debe ser finito y tener máximo seis decimales")
    if mode == MARGIN_MODE and not Decimal(0) <= value < Decimal(1):
        raise InvalidInputError("El margen mínimo debe estar entre 0 y 1 (0.12 = 12 %)")
    if mode == SUPPLIER_MARGIN_MODE and not Decimal(-1) < value < Decimal(1):
        raise InvalidInputError(
            "El margen mínimo del proveedor debe estar entre -1 y 1 (0.07 = 7 %, -0.05 = -5 %)"
        )
    if mode == "porcentaje" and not Decimal(-1) <= value <= Decimal(1):
        raise InvalidInputError("El porcentaje JDH debe estar entre -1 y 1 (0.05 = 5 %)")
    if mode == "costo_neto" and not Decimal(0) <= value < Decimal("1000000000000"):
        raise InvalidInputError("El costo pactado JDH debe ser positivo o cero y menor a 10^12")


def require_eligible(support: Any, today: date) -> None:
    if (
        support.status != "activo"
        or not support.is_enabled
        or support.issues
        or validity_on(support.valid_from, support.valid_to, today) != "vigente"
    ):
        raise ConflictError(
            "Solo se puede solicitar o aprobar sobre un acuerdo vigente sin revisión"
        )


class SupportRequestRepository(Protocol):
    async def create(
        self,
        support_id: UUID,
        version: int,
        value: Decimal,
        reason: str,
        actor_id: UUID,
        actor: str,
        today: date,
    ) -> dict[str, Any]: ...

    async def create_many(
        self, items: list[tuple[UUID, int, Decimal, str]], actor_id: UUID, actor: str, today: date
    ) -> list[dict[str, Any]]: ...

    async def search(
        self, owner: UUID | None, status: str | None, q: str | None, page: int, size: int
    ) -> dict[str, Any]: ...

    async def get(self, request_id: UUID, owner: UUID | None) -> dict[str, Any]: ...

    async def decide(
        self,
        request_id: UUID,
        version: int,
        support_version: int | None,
        actor: str,
        approved: bool,
        reason: str | None,
        today: date,
    ) -> dict[str, Any]: ...

    async def cancel(self, support_id: UUID, version: int, actor: str, reason: str) -> None: ...

    async def history(self, support_id: UUID) -> list[dict[str, Any]]: ...
