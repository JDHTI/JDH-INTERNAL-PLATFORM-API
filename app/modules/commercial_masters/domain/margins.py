"""Margins by product and channel: how a value is read and which channel uses which margin.

The margin master gives every product one margin per channel. The order validator picks
the one for the channel of the order (see :func:`channel_field`); the others are stored
and shown but not applied yet.
"""

from __future__ import annotations

import re
import unicodedata
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
    "margin_list": "Margen Lista 1",
    "margin_wholesale": "Margen Mayoristas",
    "margin_mass_food": "Margen Masivo Food",
    "margin_market": "Margen Market Apartadó",
    "margin_salsamentarias": "Margen Salsamentarias",
}

# Margins the validator applies today. Masivo Food and Market Apartadó are loaded and shown,
# but no rule uses them yet.
APPLIED_FIELDS = ("margin_list", "margin_wholesale", "margin_salsamentarias")

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
    """The margin column that applies to a client channel.

    - ``MAYORISTAS Y DISTRIBUIDORES`` and ``SUBDISTRIBUIDOR`` use the wholesale margin.
    - ``SALSAMENTARIAS`` uses the salsamentarias margin.
    - Any other channel, or none, uses list margin 1.
    """

    name = normalize(channel)
    if "mayorista" in name or "subdistribuidor" in name:
        return "margin_wholesale"
    if "salsamentaria" in name:
        return "margin_salsamentarias"
    return "margin_list"


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
