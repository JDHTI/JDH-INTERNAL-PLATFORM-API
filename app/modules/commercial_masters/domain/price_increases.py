"""Price increase history of the products (the purchasing area's ``Acumulado Alza Precios``).

Each row is one cost change a supplier notified for a reference: when it was notified, when
it takes effect, when it was loaded in SIESA (``system_updated_on``), the old and new cost
and whether it went up, down or stayed. The product's own data (description, supplier,
category, state, pack) is not stored here: it stays the product master's.

A row whose ``system_updated_on`` is empty is an increase SIESA does not have yet: the quoter
compares its new cost with the supplier's quotation and the inventory cost (see
:func:`pending_cost`).

The workbook is the area's own and comes as it is kept: money as text in two formats
(``$ 81.960,00`` and ``$ 95.484``), formula errors (``#N/D``, ``#¡REF!``, ``#¡VALOR!``),
notes typed where a cost goes (``DESCODIFICADO``), dates of 1900 or with a mistyped year
(``18/09/0206``), dashes for zero. All of it loads:

- a formula error is a cell without a value: it stays empty, it is not a problem;
- a note in a money column goes to the observations (:class:`CellNote`);
- a date is kept as the file has it, however old (the history is the area's record);
- only what is neither (a number that cannot be read) is reported for the row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

UP = "subio"
DOWN = "bajo"
FLAT = "estable"
STATUSES = (UP, DOWN, FLAT)
STATUS_LABELS = {UP: "Subió", DOWN: "Bajó", FLAT: "Estable"}
SOURCE_BOOK = "libro"
SOURCE_REQUEST = "solicitud"
SOURCES = (SOURCE_BOOK, SOURCE_REQUEST)
_STATUS_WORDS = {"subio": UP, "bajo": DOWN, "estable": FLAT}

# The system update date set by hand in the portal cannot be older than this (a typo). The
# workbook's dates are kept as they are.
EARLIEST_DATE = date(2000, 1, 1)
_DIGIT = re.compile(r"\d")
_EXCEL_EPOCH = date(1899, 12, 30)
_THOUSANDS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y")
_YES = {"ok", "si", "sí", "x", "true", "1"}


class CellError(ValueError):
    """A cell that holds something other than its kind of value."""


class CellNote(CellError):
    """Words where a number goes (``CODIGO YA NO EXISTE``): a note about the row, kept in
    its observations rather than reported."""


def _text(value: Any) -> str:
    return str(value).replace("\xa0", " ").strip()


def parse_money(value: Any) -> Decimal | None:
    """A money cell: ``53958``, ``"$ 81.960,00"``, ``"$ 95.484"`` or ``"$ 1,00"``.

    Blank, ``-``, ``$ -`` and Excel's formula errors (``#N/D``, ``#¡REF!``) are empty. In
    text, a comma is the decimal separator and dots group thousands; without a comma, dots in
    groups of three are thousands.

    Raises:
        CellNote: Words without a digit (``CODIGO YA NO EXISTE``).
        CellError: Text with digits that is not a number.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = _text(value).replace("$", "").replace(" ", "")
    if text in ("", "-") or text.startswith("#"):
        return None
    if not _DIGIT.search(text):
        raise CellNote(_text(value))
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif _THOUSANDS.match(text):
        text = text.replace(".", "")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise CellError(_text(value)) from exc
    if not number.is_finite():
        raise CellError(_text(value))
    return number


def parse_ratio(value: Any) -> Decimal | None:
    """A percentage as a fraction: ``0.165`` stays, ``"16,5 %"`` is ``0.165``.

    Raises:
        CellError: Text that is not a number.
    """

    if isinstance(value, str) and "%" in value:
        number = parse_money(value.replace("%", ""))
        return None if number is None else number / 100
    return parse_money(value)


def parse_date(value: Any) -> date | None:
    """A date cell: a date, an Excel serial number or ``dd/mm/aaaa`` text, kept as the file
    has it (1900-01-18, or ``18/09/0206`` read as the year 206).

    Raises:
        CellError: Text that is not a date.
    """

    if value is None or (isinstance(value, str) and _text(value) in ("", "-")):
        return None
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        day = _EXCEL_EPOCH + timedelta(days=int(value))
    else:
        text = _text(value)
        for pattern in _DATE_FORMATS:
            try:
                day = datetime.strptime(text, pattern).date()
                break
            except ValueError:
                continue
        else:
            raise CellError(text)
    return day


def parse_status(value: Any) -> str | None:
    """``SUBIO``, ``BAJO`` or ``ESTABLE`` (any case and accents); anything else is empty."""

    if value is None:
        return None
    word = _text(value).lower().replace("ó", "o").replace("í", "i")
    return _STATUS_WORDS.get(word)


def parse_flag(value: Any) -> bool:
    """A check column (``OK``): marked or not."""

    return value is not None and _text(value).lower() in _YES


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    return text or None


def status_of(old: Decimal | None, new: Decimal | None) -> str | None:
    """Up, down or flat, comparing the new cost with the old one; ``None`` without both."""

    if old is None or new is None:
        return None
    if new > old:
        return UP
    if new < old:
        return DOWN
    return FLAT


@dataclass(frozen=True)
class PriceIncreaseValues:
    """What a row of the history says, as it is stored and upserted."""

    sku: str
    notified_on: date | None = None
    system_updated_on: date | None = None
    increased_on: date | None = None
    supplier_sku: str | None = None
    ean: str | None = None
    regional: str | None = None
    icui_pct: Decimal | None = None
    ibua: Decimal | None = None
    old_cost_with_tax: Decimal | None = None
    old_cost: Decimal | None = None
    new_cost: Decimal | None = None
    new_cost_with_tax: Decimal | None = None
    variation_amount: Decimal | None = None
    variation_pct: Decimal | None = None
    status: str | None = None
    observations: str | None = None
    margin_pct: Decimal | None = None
    price: Decimal | None = None
    notified: bool = False
    siesa_changed: bool = False
    # ``libro``: the area's workbook; ``solicitud``: a supplier's request approved in Costos.
    source: str = SOURCE_BOOK
    request_line_id: UUID | None = None

    @property
    def key(self) -> str:
        """What identifies a row for the upsert: the reference, when the increase takes
        effect and when it was notified."""

        return "|".join(
            (
                self.sku,
                self.increased_on.isoformat() if self.increased_on else "",
                self.notified_on.isoformat() if self.notified_on else "",
            )
        )

    @property
    def effective_new_cost(self) -> Decimal | None:
        """The new cost as the supplier charges it: with the healthy-food tax (ICUI and
        IBUA) when the row has it, else without; the supplier's quotation carries it too."""

        return self.new_cost_with_tax if self.new_cost_with_tax is not None else self.new_cost

    def comparable(self) -> tuple:
        """Every value but the key, to tell an unchanged row from an updated one."""

        return tuple(getattr(self, name) for name in VALUE_FIELDS)


VALUE_FIELDS = tuple(name for name in PriceIncreaseValues.__dataclass_fields__ if name != "sku")


@dataclass(frozen=True)
class FileRow:
    """A row of the uploaded workbook: its values and what could not be read."""

    row_number: int
    values: PriceIncreaseValues
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class PriceIncrease:
    """A stored row, with the product's description, supplier and category from the master."""

    id: UUID
    values: PriceIncreaseValues
    updated_by: str
    updated_at: datetime
    description: str | None = None
    supplier_name: str | None = None
    category: str | None = None
    is_known_product: bool = True

    @property
    def is_pending(self) -> bool:
        """SIESA does not have it yet: no system update date."""

        return self.values.system_updated_on is None


@dataclass(frozen=True)
class PriceIncreaseFilters:
    # Reference prefix or description; the supplier is picked from a list (``supplier``).
    q: str | None = None
    status: str | None = None
    pending: bool | None = None
    sku: str | None = None
    # A commercial supplier code.
    supplier: str | None = None


@dataclass(frozen=True)
class SupplierOption:
    """A commercial supplier with references in the history."""

    code: str
    name: str | None
    references: int


@dataclass(frozen=True)
class MonthOption:
    """A calendar month with increases taking effect, as ``YYYY-MM``."""

    month: str
    changes: int
    references: int


@dataclass(frozen=True)
class FilterOptions:
    suppliers: tuple[SupplierOption, ...]
    # The latest month first; only from the year 2000 (older dates are typing slips).
    months: tuple[MonthOption, ...]


@dataclass(frozen=True)
class PriceIncreasePage:
    items: tuple[PriceIncrease, ...]
    total: int
    page: int
    size: int
    # Rows by status, and how many are pending, for the whole filter (not the page).
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadIssue:
    row_number: int
    sku: str | None
    problem: str


@dataclass(frozen=True)
class UploadReport:
    """What an upload did, or would do (a simulation)."""

    applied: bool
    rows_read: int
    inserted: int
    updated: int
    unchanged: int
    duplicated: int
    unknown_skus: int
    pending: int
    issues: tuple[UploadIssue, ...]
    issues_truncated: bool


def pending_cost(values: PriceIncreaseValues) -> Decimal | None:
    """The cost the quoter compares for a reference whose latest increase SIESA does not
    have yet; ``None`` when it is loaded or has no new cost."""

    if values.system_updated_on is not None:
        return None
    cost = values.effective_new_cost
    return cost if cost is not None and cost > 0 else None


__all__ = [
    "DOWN",
    "FLAT",
    "SOURCES",
    "SOURCE_BOOK",
    "SOURCE_REQUEST",
    "STATUSES",
    "STATUS_LABELS",
    "UP",
    "CellError",
    "CellNote",
    "FileRow",
    "FilterOptions",
    "MonthOption",
    "PriceIncrease",
    "PriceIncreaseFilters",
    "PriceIncreasePage",
    "PriceIncreaseValues",
    "SupplierOption",
    "UploadIssue",
    "UploadReport",
    "clean_text",
    "parse_date",
    "parse_flag",
    "parse_money",
    "parse_ratio",
    "parse_status",
    "pending_cost",
    "status_of",
]
