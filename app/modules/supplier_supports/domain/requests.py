"""Rules and persistence boundary for JDH support requests."""

from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ConflictError, InvalidInputError
from app.modules.supplier_supports.domain.validity import validity_on


def request_mode(calc_method: str) -> str:
    return "costo_neto" if calc_method == "costo_neto" else "porcentaje"


def validate_value(mode: str, value: Decimal) -> None:
    if not value.is_finite() or value.as_tuple().exponent < -6:
        raise InvalidInputError("El valor debe ser finito y tener máximo seis decimales")
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
