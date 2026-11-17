"""Margins by product and business-unit list: how a value is read and which customer uses which.

The margin master gives every product one optimal margin per business-unit list (Food service,
Mayoristas, Masivo food, Market Apartadó; Salsamentarias is kept but no longer applied). The
quoter, the order validator and the supports master pick it with :func:`customer_margin_field`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Product columns that hold a margin, in the order the master lists them.
MARGIN_FIELDS = (
    "margin_list",
    "margin_wholesale",
    "margin_mass_food",
    "margin_market",
    "margin_salsamentarias",
)

MARGIN_LABELS = {
    "margin_list": "Margen Food Service",
    "margin_wholesale": "Margen Mayoristas",
    "margin_mass_food": "Margen Masivo Food",
    "margin_market": "Margen Market Apartadó",
    "margin_salsamentarias": "Margen Salsamentarias",
}

# Margins the rules apply (see :func:`customer_margin_field`). Salsamentarias is loaded and
# shown, but those customers use the Mayoristas margin.
APPLIED_FIELDS = ("margin_list", "margin_wholesale", "margin_mass_food", "margin_market")

# Billing operation center of JDH Market Apartadó (``cod_co_factura``).
MARKET_CENTER = "200"
# ``consumo.vendedores.clase_equipo`` of the Masivo food sales teams.
MASS_FOOD_TEAM = "masivo food"
# Lists that fall back to Food service when the product has no margin of their own.
_FALLBACK_TO_LIST = ("margin_mass_food", "margin_market")

# Liquor consumption tax per unit by regional (``IMPTOLIC``): Nacional and Costa, and the tax
# group (``0503`` wine, ``0509`` spirits). The margin master keeps them in the ``IMPUESTOS``
# table of ``Margen Proveedor-Impuestos``; some SKUs pay more on the Coast.
LIQUOR_TAX_FIELDS = ("liquor_tax", "liquor_tax_coast", "liquor_tax_group")
LIQUOR_TAX_LABELS = {
    "liquor_tax": "Impuesto licor Nacional",
    "liquor_tax_coast": "Impuesto licor Costa",
    "liquor_tax_group": "Grupo impositivo",
}

_WORDS = re.compile(r"[^a-z0-9]+")
ONE = Decimal(1)
HUNDRED = Decimal(100)


def normalize(value: object) -> str:
    """``"Margen Market Apartadó"`` -> ``"margen market apartado"``."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(_WORDS.sub(" ", text.lower()).split())


def channel_field(channel: str | None) -> str:
    """The margin column a channel alone gives (no operation center or sales team known).

    ``MAYORISTAS Y DISTRIBUIDORES``, ``SUBDISTRIBUIDOR`` and ``SALSAMENTARIAS`` use the
    Mayoristas margin; any other channel, or none, Food service (``margin_list``).
    """

    name = normalize(channel)
    if "mayorista" in name or "subdistribuidor" in name or "salsamentaria" in name:
        return "margin_wholesale"
    return "margin_list"


def customer_margin_field(
    channel: str | None,
    billing_center: str | None = None,
    team_class: str | None = None,
) -> str:
    """The margin list («Margen objetivo Listas de Unidad Negocio») of a customer.

    In this order:

    1. Billing operation center ``200`` (JDH Market Apartadó): ``margin_market``.
    2. Sales team class ``MASIVO FOOD``: ``margin_mass_food``.
    3. Channel Mayoristas, Subdistribuidores or Salsamentarias: ``margin_wholesale``.
    4. Anything else, or no customer at all: Food service (``margin_list``).
    """

    if (billing_center or "").strip() == MARKET_CENTER:
        return "margin_market"
    if MASS_FOOD_TEAM in normalize(team_class):
        return "margin_mass_food"
    return channel_field(channel)


def margin_for(margins: Mapping[str, Decimal | None], field_name: str) -> Decimal | None:
    """A product's margin on a list; Masivo food and Market fall back to Food service.

    Those two lists are new in the rules and many products have no value for them yet; the
    Food service margin is then the closest optimal margin. Mayoristas never falls back.
    """

    value = margins.get(field_name)
    if value is None and field_name in _FALLBACK_TO_LIST:
        return margins.get("margin_list")
    return value


def parse_margin(value: Any) -> tuple[bool, Decimal | None]:
    """Read one margin cell.

    A blank cell leaves the margin as it is. ``0`` means "no margin defined" (the margin
    master writes 0 where it has none) and clears it. A value below 1 is a fraction
    (``0.18``); from 1 up to 100 it is percentage points (``18`` or ``18 %``).

    Returns:
        tuple[bool, Decimal | None]: Whether the cell carries a value, and the fraction
        (``None`` to clear).

    Raises:
        ValueError: If the cell is not a number or is out of range.
    """

    if value is None:
        return False, None
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace("%", "").replace(" ", "")
        if not text:
            return False, None
        try:
            number = Decimal(text.replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(value) from exc
    if number < 0 or number > HUNDRED:
        raise ValueError(value)
    if number == 0:
        return True, None
    if number >= ONE:
        number = number / HUNDRED
    if number >= ONE:
        raise ValueError(value)
    return True, number.quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class MarginRow:
    """One row of a margin file: the SKU and the margin cells it carried.

    Only the fields present in ``values`` are changed; ``None`` clears a margin. ``taxes``
    holds the liquor tax fields the file carried for the SKU (see ``LIQUOR_TAX_FIELDS``);
    ``None`` clears one.
    """

    row_number: int
    sku: str
    values: dict[str, Decimal | None] = field(default_factory=dict)
    notes: str | None = None
    has_notes: bool = False
    problems: tuple[str, ...] = ()
    taxes: dict[str, Decimal | str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class MarginChange:
    """What a row would change in a product."""

    sku: str
    description: str | None
    changes: dict[str, tuple[Decimal | None, Decimal | None]]
    notes: tuple[str | None, str | None] | None = None
    taxes: dict[str, tuple[Decimal | str | None, Decimal | str | None]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class MarginIssue:
    """A row that could not be applied."""

    row_number: int
    sku: str | None
    problem: str


@dataclass(frozen=True)
class MarginReport:
    """Result of a margin update, simulated or applied."""

    applied: bool
    rows_read: int
    updated: int
    unchanged: int
    duplicated: int
    unknown_skus: tuple[str, ...]
    issues: tuple[MarginIssue, ...]
    changes: tuple[MarginChange, ...]
    changes_truncated: bool = False
