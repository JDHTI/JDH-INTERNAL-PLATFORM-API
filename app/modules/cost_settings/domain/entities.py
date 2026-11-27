"""The cost rules of the quoter that BI adjusts (module ``ajuste-costos``, 2026-11-25).

Two kinds of rule live here:

- **Parameters**: numbers the quoter applies to every line. The factor on the cost of each
  list family (1,004: 0,4 % more, except the wholesale lists; Salsamentarias is no family
  since 2026-11-25, its customers use Mayoristas) and the negotiation threshold
  (how many margin points under the target a price may go and still pass without an alert,
  1 point). A parameter without a stored value takes its default (the code's, or the
  setting ``QUOTATIONS_MAX_POINTS_BELOW_TARGET``), so nothing changes until BI sets one.
- **Supplier branch rules**: which branch of a supplier quotes for each operation center. In
  SIESA a supplier may keep one price list per branch (``consumo.cotizaciones_proveedores``,
  ``proveedor_sucursal_id``; ``centro_operacion_id`` is always empty): Team Foods quotes
  Antioquia from branch 002 since 2026-11-19 and Lactalis Centro from branch 003. A rule says
  "for C.O. 001 take branch 002; elsewhere the general branch 001". For a reference the
  branch does not quote, the general one; if neither does, the highest of the others.
  Suppliers without a rule take, as always, the highest quotation in force.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

# Parameters, by key. The cost of each list family is raised by a fraction (2026-11-27,
# BI): 0.004 is 0,4 % more and 0 leaves the standard cost. Until then they were factors
# (1.004) that could not go under 1; a stored value of the old keys is ignored.
INCREMENT_GENERAL = "incremento_costo_generales"
INCREMENT_WHOLESALE = "incremento_costo_mayoristas"
INCREMENT_MARKET = "incremento_costo_market"
THRESHOLD = "umbral_negociacion"
# Former names, kept for the callers.
FACTOR_GENERAL = INCREMENT_GENERAL
FACTOR_WHOLESALE = INCREMENT_WHOLESALE
FACTOR_MARKET = INCREMENT_MARKET

# Entities of the change history.
PARAMETER = "parametro"
BRANCH_RULE = "regla_sucursal"


@dataclass(frozen=True)
class ParameterSpec:
    """A parameter: what it is, its unit and the range it accepts (inclusive)."""

    key: str
    label: str
    description: str
    # ``incremento`` raises the cost by a fraction (0.004 is 0,4 % more; 0 is the standard
    # cost); ``margen`` is a fraction of margin (0.01 is one point).
    unit: str
    minimum: Decimal
    maximum: Decimal
    # The list family a cost increment belongs to (``pricing.FAMILY_LABELS``).
    family: str | None = None


INCREMENT = "incremento"
PARAMETERS: tuple[ParameterSpec, ...] = (
    ParameterSpec(
        INCREMENT_GENERAL,
        "Incremento de costo: Generales",
        "Sube el costo de las listas Generales (0,4 % hoy). En 0 queda el costo estándar.",
        INCREMENT,
        Decimal("0"),
        Decimal("0.1"),
        "generales",
    ),
    ParameterSpec(
        INCREMENT_MARKET,
        "Incremento de costo: Market",
        "Sube el costo de la lista Market (0,4 % hoy). En 0 queda el costo estándar.",
        INCREMENT,
        Decimal("0"),
        Decimal("0.1"),
        "market",
    ),
    ParameterSpec(
        INCREMENT_WHOLESALE,
        "Incremento de costo: Mayoristas",
        "Sube el costo de las listas Mayoristas y de los clientes con margen Mayoristas (0 hoy: "
        "costo estándar).",
        INCREMENT,
        Decimal("0"),
        Decimal("0.1"),
        "mayoristas",
    ),
    ParameterSpec(
        THRESHOLD,
        "Umbral de negociación",
        "Puntos de margen por debajo del objetivo que un precio puede bajar y pasar sin "
        "alerta (0,01 es 1 punto: con objetivo 20 %, el mínimo es 19 %).",
        "margen",
        Decimal("0"),
        Decimal("0.1"),
    ),
)
PARAMETER_SPECS = {spec.key: spec for spec in PARAMETERS}


@dataclass(frozen=True)
class Parameter:
    """A parameter's value in force: the stored one, else its default."""

    spec: ParameterSpec
    value: Decimal
    default: Decimal
    # Who set the stored value and when; ``None`` while it is the default.
    updated_by: str | None = None
    updated_at: datetime | None = None

    @property
    def is_default(self) -> bool:
        return self.updated_at is None


@dataclass(frozen=True)
class BranchRule:
    """Which branch of a supplier quotes for each operation center."""

    id: UUID
    supplier_nit: str
    general_branch: str
    # Operation center -> the supplier's branch that quotes for it.
    center_branches: dict[str, str]
    is_active: bool
    supplier_name: str | None = None
    note: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None

    def branch_for(self, center: str | None) -> str | None:
        """The branch of ``center`` (``None`` when the center has none: the general one)."""

        return self.center_branches.get(center) if center else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier_nit": self.supplier_nit,
            "supplier_name": self.supplier_name,
            "general_branch": self.general_branch,
            "center_branches": dict(self.center_branches),
            "is_active": self.is_active,
            "note": self.note,
        }


@dataclass(frozen=True)
class QuoteRule:
    """What the cost query needs of a rule for one operation center: the supplier, the
    branch that quotes for that center (``None``: none for it) and the general branch."""

    supplier_nit: str
    branch: str | None
    general_branch: str


@dataclass(frozen=True)
class CostSettings:
    """The rules in force, as the quoter reads them."""

    parameters: Mapping[str, Decimal]
    rules: tuple[BranchRule, ...] = ()

    def value(self, key: str) -> Decimal:
        return self.parameters[key]

    def cost_factors(self) -> dict[str, Decimal]:
        """The factor on the cost by list family (``pricing.FAMILY_LABELS`` keys): 1 plus
        its increment (0 increment is a factor of 1, the standard cost)."""

        return {
            spec.family: 1 + self.parameters[spec.key]
            for spec in PARAMETERS
            if spec.family is not None and spec.key in self.parameters
        }

    def quote_rules(self, center: str | None) -> tuple[QuoteRule, ...]:
        """The active branch rules for a plan of ``center``. *Nacional* and list 1 (no
        center) take, as always, the highest quotation: no rule applies."""

        if not center:
            return ()
        return tuple(
            QuoteRule(rule.supplier_nit, rule.branch_for(center), rule.general_branch)
            for rule in self.rules
            if rule.is_active
        )


@dataclass(frozen=True)
class Change:
    """One change of the rules: what it was, what it is, who and when."""

    id: UUID
    entity: str
    ref: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    actor: str
    at: datetime
    note: str | None = None


@dataclass(frozen=True)
class Overview:
    """Everything the screen shows."""

    parameters: tuple[Parameter, ...]
    rules: tuple[BranchRule, ...]
    changes: tuple[Change, ...] = field(default_factory=tuple)
    can_manage: bool = False


__all__ = [
    "BRANCH_RULE",
    "FACTOR_GENERAL",
    "FACTOR_MARKET",
    "FACTOR_WHOLESALE",
    "INCREMENT",
    "INCREMENT_GENERAL",
    "INCREMENT_MARKET",
    "INCREMENT_WHOLESALE",
    "PARAMETER",
    "PARAMETERS",
    "PARAMETER_SPECS",
    "THRESHOLD",
    "BranchRule",
    "Change",
    "CostSettings",
    "Overview",
    "Parameter",
    "ParameterSpec",
    "QuoteRule",
]
