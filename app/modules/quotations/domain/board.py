"""The board of Listas principales: every main list in one table.

Each main list keeps its own update underneath (the quoter's engine, its items' review, its
staging and files). The board is the latest update of every main list read as one table:
one row per SKU and list, filtered, sorted and paged on the server (about 50 thousand rows
for the 14 lists on 2026-11-12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.modules.quotations.domain.entities import QuotationLine

# The filters of the review and of where the list price leaves the margin (the UI's chips).
REVIEW_CHIPS = ("sin_marcar", "por_validar", "validada", "cargada")
MARGIN_CHIPS = ("alza", "objetivo", "encima", "alerta")
CHIPS = REVIEW_CHIPS + MARGIN_CHIPS
# Half a point of room either way around the target (decided 2026-11-12).
TARGET_TOLERANCE = Decimal("0.005")
# Least increase that makes a row an «alza»: under 1 % of the current price it is on target
# (2026-11-23). The board only shows rows in stock.
MIN_INCREASE = Decimal("0.01")
SORTS = (
    "lista",
    "sku",
    "precio_actual",
    "margen_actual",
    "precio_nuevo",
    "variacion",
    "existencia",
)
MAX_PAGE_SIZE = 200
# Most rows one bulk action takes ("select every row of the filter").
MAX_KEYS = 10_000


@dataclass(frozen=True)
class BoardFilter:
    """What the board shows: lists (none is every one), text, suppliers, categories, one
    chip, and the order."""

    lists: tuple[str, ...] = ()
    q: str | None = None
    suppliers: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    chip: str | None = None
    sort: str = "lista"
    descending: bool = False


@dataclass(frozen=True)
class BoardRow:
    """One SKU on one main list, with the update it lives in."""

    quotation_id: UUID
    quotation_version: int
    price_list_id: str
    price_list_name: str | None
    line: QuotationLine
    # Stock in the list's warehouses as the sync read it: 0 without stock, ``None`` when it
    # was not read.
    stock_on_hand: Decimal | None = None
    stock_available: Decimal | None = None


@dataclass(frozen=True)
class BoardKey:
    """A row of the board: the update and the SKU."""

    quotation_id: UUID
    sku: str


@dataclass(frozen=True)
class BoardList:
    """A main list on the board: its current update (if it was built) and its counts."""

    code: str
    name: str | None
    quotation_id: UUID | None = None
    built_at: datetime | None = None
    line_count: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoardPage:
    rows: tuple[BoardRow, ...]
    total: int
    page: int
    size: int
    # Rows of each chip under the other filters (lists, text, suppliers, categories), and
    # ``todos``: every row under those filters.
    counts: dict[str, int]


__all__ = [
    "CHIPS",
    "MARGIN_CHIPS",
    "MAX_KEYS",
    "MAX_PAGE_SIZE",
    "MIN_INCREASE",
    "REVIEW_CHIPS",
    "SORTS",
    "TARGET_TOLERANCE",
    "BoardFilter",
    "BoardKey",
    "BoardList",
    "BoardPage",
    "BoardRow",
]
