"""Ajuste de costos: read and change the cost rules of the quoter (module ``ajuste-costos``).

``read`` sees the parameters, the supplier branch rules and their history; ``manage``
changes them. Every change is stored with what it was, who and when. The quoter reads the
rules in force through :meth:`CostSettingsService.current` (no principal: it is the engine,
not a person), cached for a few seconds; a change here clears the cache of this process, so
the next quotation built or refreshed takes it. Stored quotations keep their prices until
they are refreshed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.cost_settings.domain.entities import (
    BRANCH_RULE,
    PARAMETER,
    PARAMETER_SPECS,
    PARAMETERS,
    THRESHOLD,
    BranchRule,
    Change,
    CostSettings,
    Overview,
    Parameter,
    QuoteRule,
)
from app.modules.cost_settings.domain.ports import CostSettingsRepository, ManagerPolicy
from app.modules.identity.domain.principal import Principal
from app.modules.quotations.domain import pricing

# Seconds the quoter keeps the rules in force before reading them again.
CACHE_SECONDS = 30.0
NOTE_LENGTH = 500
_NIT = re.compile(r"^\d{5,15}$")
_CODE = re.compile(r"^[0-9A-Za-z]{1,10}$")
# One process, one snapshot: every request of the quoter shares it.
_cache: dict[str, Any] = {}


def clear_cache() -> None:
    """Forget the snapshot (a change here, or a test)."""

    _cache.clear()


def default_values(threshold: Decimal) -> dict[str, Decimal]:
    """Each parameter's value while BI has not set it: the quoter's own."""

    values = {
        spec.key: pricing.COST_FACTOR[spec.family] for spec in PARAMETERS if spec.family is not None
    }
    values[THRESHOLD] = threshold
    return values


def _code(value: str | None, what: str) -> str:
    text = (value or "").strip().upper()
    if not _CODE.fullmatch(text):
        raise InvalidInputError(f"{what}: escribe el código como está en SIESA (por ejemplo 001)")
    return text


def _note(value: str | None) -> str | None:
    text = " ".join((value or "").split()) or None
    if text is not None and len(text) > NOTE_LENGTH:
        raise InvalidInputError(f"La nota admite máximo {NOTE_LENGTH} caracteres")
    return text


class CostSettingsService:
    """The parameters and the supplier branch rules of the quoter's cost."""

    def __init__(
        self,
        repository: CostSettingsRepository,
        managers: ManagerPolicy | None = None,
        threshold_default: Decimal | float | str = Decimal("0.01"),
    ) -> None:
        """Create the service.

        Args:
            repository (CostSettingsRepository): Stored values, rules and history.
            managers (ManagerPolicy | None): ``ajuste-costos`` (``read`` and ``manage``);
                without one only administrators.
            threshold_default (Decimal): The negotiation threshold while BI has not set it
                (``QUOTATIONS_MAX_POINTS_BELOW_TARGET`` / 100). The cost factors default to
                the quoter's (:data:`pricing.COST_FACTOR`).
        """

        self.repository = repository
        self.defaults = default_values(Decimal(str(threshold_default)))
        self.managers = managers

    # ------------------------------------------------------------ access

    async def can_manage(self, principal: Principal) -> bool:
        if principal.is_platform_admin:
            return True
        return self.managers is not None and await self.managers.is_manager(principal)

    async def can_read(self, principal: Principal) -> bool:
        if await self.can_manage(principal):
            return True
        return self.managers is not None and await self.managers.can_read(principal)

    async def _require(self, principal: Principal, manage: bool = False) -> None:
        if manage and not await self.can_manage(principal):
            raise ForbiddenError("Solo quien gestiona el ajuste de costos lo cambia")
        if not manage and not await self.can_read(principal):
            raise ForbiddenError("No tienes acceso al ajuste de costos")

    # ------------------------------------------------------------ the engine

    async def current(self) -> CostSettings:
        """The rules in force, for the quoter (cached :data:`CACHE_SECONDS`)."""

        found = _cache.get("settings")
        if found is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return found
        stored = await self.repository.parameters()
        values = {
            key: stored[key][0] if key in stored else default
            for key, default in self.defaults.items()
        }
        settings = CostSettings(values, tuple(await self.repository.rules()))
        _cache.update(settings=settings, at=time.monotonic())
        return settings

    async def quote_rules(self, center: str | None) -> tuple[QuoteRule, ...]:
        """The supplier branch rules for a cost plan of ``center``."""

        return (await self.current()).quote_rules(center)

    async def threshold_and_factors(self) -> tuple[Decimal, dict[str, Decimal]]:
        """The negotiation threshold and the cost factor of each list family in force."""

        settings = await self.current()
        return settings.value(THRESHOLD), settings.cost_factors()

    # ------------------------------------------------------------ the screen

    async def _parameters(self) -> tuple[Parameter, ...]:
        stored = await self.repository.parameters()
        result = []
        for spec in PARAMETERS:
            default = self.defaults[spec.key]
            if spec.key in stored:
                value, by, at = stored[spec.key]
                result.append(Parameter(spec, value, default, by, at))
            else:
                result.append(Parameter(spec, default, default))
        return tuple(result)

    async def overview(self, principal: Principal) -> Overview:
        """The parameters in force, the rules and the latest changes."""

        await self._require(principal)
        return Overview(
            parameters=await self._parameters(),
            rules=tuple(await self.repository.rules()),
            changes=tuple(await self.repository.changes()),
            can_manage=await self.can_manage(principal),
        )

    async def changes(self, principal: Principal, limit: int = 50) -> list[Change]:
        await self._require(principal)
        return await self.repository.changes(min(max(limit, 1), 200))

    async def set_parameters(
        self, principal: Principal, values: Mapping[str, Any], note: str | None = None
    ) -> tuple[Parameter, ...]:
        """New values for some parameters, each within its range.

        Raises:
            InvalidInputError: An unknown parameter, a value that is not a number or out of
                its range, or nothing to change.
        """

        await self._require(principal, manage=True)
        if not values:
            raise InvalidInputError("Envía al menos un parámetro")
        current = {item.spec.key: item for item in await self._parameters()}
        clean: dict[str, Decimal] = {}
        for key, raw in values.items():
            spec = PARAMETER_SPECS.get(key)
            if spec is None:
                raise InvalidInputError(f"Parámetro desconocido: {key}")
            try:
                value = Decimal(str(raw).strip().replace(",", "."))
            except (InvalidOperation, ValueError) as exc:
                raise InvalidInputError(f"{spec.label}: debe ser un número") from exc
            if not value.is_finite() or not spec.minimum <= value <= spec.maximum:
                raise InvalidInputError(
                    f"{spec.label}: debe estar entre {spec.minimum} y {spec.maximum}"
                )
            value = value.quantize(Decimal("0.000001"))
            if value != current[key].value:
                clean[key] = value
        if not clean:
            raise InvalidInputError("Los valores son los mismos de hoy: no hay nada que cambiar")
        now = datetime.now(UTC)
        text = _note(note)
        changes = [
            Change(
                id=uuid4(),
                entity=PARAMETER,
                ref=key,
                before={"value": str(current[key].value), "default": current[key].is_default},
                after={"value": str(value)},
                actor=principal.email,
                at=now,
                note=text,
            )
            for key, value in clean.items()
        ]
        await self.repository.set_parameters(clean, principal.email, now, changes)
        clear_cache()
        return await self._parameters()

    def _checked_rule(
        self,
        supplier_nit: str | None,
        general_branch: str | None,
        center_branches: Mapping[str, str] | None,
    ) -> tuple[str, str, dict[str, str]]:
        nit = re.sub(r"\D", "", supplier_nit or "")
        if not _NIT.fullmatch(nit):
            raise InvalidInputError("Escribe el NIT del proveedor, sin dígito de verificación")
        general = _code(general_branch, "Sucursal general")
        centers: dict[str, str] = {}
        for center, branch in (center_branches or {}).items():
            code = _code(center, "C.O.")
            centers[code] = _code(branch, f"Sucursal del C.O. {code}")
        if not centers:
            raise InvalidInputError("Indica al menos un C.O. con su sucursal")
        same = [code for code, branch in centers.items() if branch == general]
        if same:
            raise InvalidInputError(
                f"El C.O. {same[0]} ya toma la sucursal general: no hace falta en la regla"
            )
        return nit, general, dict(sorted(centers.items()))

    async def create_rule(
        self,
        principal: Principal,
        *,
        supplier_nit: str,
        general_branch: str = "001",
        center_branches: Mapping[str, str],
        supplier_name: str | None = None,
        is_active: bool = True,
        note: str | None = None,
    ) -> BranchRule:
        """A supplier's branch per operation center.

        Raises:
            InvalidInputError: A NIT, branch or center that is not a code, no center, or a
                center mapped to the general branch.
            ConflictError: The supplier already has a rule.
        """

        await self._require(principal, manage=True)
        nit, general, centers = self._checked_rule(supplier_nit, general_branch, center_branches)
        now = datetime.now(UTC)
        rule = BranchRule(
            id=uuid4(),
            supplier_nit=nit,
            supplier_name=" ".join((supplier_name or "").split()) or None,
            general_branch=general,
            center_branches=centers,
            is_active=is_active,
            note=_note(note),
            created_by=principal.email,
            created_at=now,
            updated_by=principal.email,
            updated_at=now,
        )
        change = Change(
            uuid4(), BRANCH_RULE, nit, None, rule.as_dict(), principal.email, now, rule.note
        )
        saved = await self.repository.save_rule(rule, change, create=True)
        clear_cache()
        return saved

    async def update_rule(
        self,
        principal: Principal,
        rule_id: UUID,
        *,
        general_branch: str | None = None,
        center_branches: Mapping[str, str] | None = None,
        supplier_name: str | None = None,
        is_active: bool | None = None,
        note: str | None = None,
    ) -> BranchRule:
        """Change a rule; ``None`` keeps a field. Turning it off (``is_active`` false) brings
        back the highest quotation for that supplier.

        Raises:
            NotFoundError: No such rule.
            InvalidInputError: As :meth:`create_rule`, or nothing changes.
        """

        await self._require(principal, manage=True)
        rule = await self.repository.get_rule(rule_id)
        if rule is None:
            raise NotFoundError("La regla no existe")
        _, general, centers = self._checked_rule(
            rule.supplier_nit,
            general_branch if general_branch is not None else rule.general_branch,
            center_branches if center_branches is not None else rule.center_branches,
        )
        now = datetime.now(UTC)
        changed = BranchRule(
            id=rule.id,
            supplier_nit=rule.supplier_nit,
            supplier_name=(
                " ".join(supplier_name.split()) or None
                if supplier_name is not None
                else rule.supplier_name
            ),
            general_branch=general,
            center_branches=centers,
            is_active=rule.is_active if is_active is None else is_active,
            note=_note(note) if note is not None else rule.note,
            created_by=rule.created_by,
            created_at=rule.created_at,
            updated_by=principal.email,
            updated_at=now,
        )
        if changed.as_dict() == rule.as_dict():
            raise InvalidInputError("La regla queda igual: no hay nada que cambiar")
        change = Change(
            uuid4(),
            BRANCH_RULE,
            rule.supplier_nit,
            rule.as_dict(),
            changed.as_dict(),
            principal.email,
            now,
            changed.note,
        )
        saved = await self.repository.save_rule(changed, change)
        clear_cache()
        return saved

    async def delete_rule(self, principal: Principal, rule_id: UUID) -> None:
        """Remove a rule (its history stays).

        Raises:
            NotFoundError: No such rule.
        """

        await self._require(principal, manage=True)
        rule = await self.repository.get_rule(rule_id)
        if rule is None:
            raise NotFoundError("La regla no existe")
        change = Change(
            uuid4(),
            BRANCH_RULE,
            rule.supplier_nit,
            rule.as_dict(),
            None,
            principal.email,
            datetime.now(UTC),
            "Regla eliminada",
        )
        await self.repository.delete_rule(rule_id, change)
        clear_cache()


__all__ = ["CACHE_SECONDS", "CostSettingsService", "clear_cache", "default_values"]
