"""Purchase entries against the supplier's quotation in force on the day of the entry.

An entry line is one line of a purchase receipt (``EA``, «ENTRADA POR COMPRAS») read live from
the warehouse. Its unit price comes before taxes and after the quotation's scale discount
(«Tasa 1»): «UN SOLO PROVEEDOR S.A.» quotes 8,246 with a 20 % Tasa 1 and its receipts come in at
6,596.80 (2026-11-24). So the quotation an entry is compared with is the quotation's unit price
less its Tasa 1 (:func:`net_quotation`):

- **ok** («Sin cambio»): the price is within the threshold of the quotation (0.1 % by default);
- **novedad** («Con novedad»): it is farther away, up or down;
- **sin_cotizacion** («Sin cotización»): the supplier had no quotation for the reference and
  unit on that day.

The quotation is the one *in force on the entry's date* (its latest activation on or before
it), never a later one: the former application compared every entry with the latest quotation
and flagged some 8,000 lines a semester that were right on their day (2026-11-23).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

OK = "ok"
NOVELTY = "novedad"
NO_QUOTATION = "sin_cotizacion"
ENTRY_STATUSES = (OK, NOVELTY, NO_QUOTATION)
ENTRY_STATUS_LABELS = {OK: "Sin cambio", NOVELTY: "Con novedad", NO_QUOTATION: "Sin cotización"}
# What «only novelties» shows: a difference or a missing quotation.
NOVELTY_STATUSES = (NOVELTY, NO_QUOTATION)

ENTRY_SORTS = ("date", "difference", "difference_pct", "sku", "supplier", "amount")
DEFAULT_THRESHOLD = Decimal("0.001")


@dataclass(frozen=True)
class EntryLine:
    """One purchase entry line and the quotation it is compared with."""

    line_key: str
    entry_date: date
    co: str | None
    regional_code: str | None
    regional_name: str | None
    warehouse: str | None
    document: str | None
    purchase_order: str | None
    # The supplier's invoice typed at the reception (free text).
    invoice_ref: str | None
    sku: str
    description: str | None
    # The commercial supplier of the item («399 - UNILEVER ANDINA S.A») and the NIT that billed.
    supplier_label: str | None
    supplier_nit: str | None
    quantity: Decimal | None
    unit: str | None
    # The price of this entry (the latest of the three purchase prices shown).
    unit_price: Decimal
    # What the entry should have cost: the quotation in force less its Tasa 1.
    quotation: Decimal | None = None
    quotation_since: date | None = None
    # «Tasa 1»: the quotation's first scale discount, as a fraction (0.2 is 20 %).
    discount_pct: Decimal | None = None
    # The quotation's unit price before the Tasa 1.
    quotation_list_price: Decimal | None = None
    # The two purchases before this one of the reference and unit: the previous and the one
    # before it («antepenúltimo»).
    previous_price: Decimal | None = None
    antepenultimate_price: Decimal | None = None
    status: str = OK

    @property
    def supplier_code(self) -> str | None:
        return supplier_code(self.supplier_label)

    @property
    def supplier_name(self) -> str | None:
        return supplier_name(self.supplier_label)

    @property
    def difference(self) -> Decimal | None:
        """Entry price minus quotation, in pesos; ``None`` without a quotation."""

        if self.quotation is None:
            return None
        return self.unit_price - self.quotation

    @property
    def difference_pct(self) -> Decimal | None:
        """The difference as a fraction of the quotation (0.05 is 5 %)."""

        return difference_pct(self.unit_price, self.quotation)

    @property
    def amount(self) -> Decimal | None:
        """Quantity times the difference: what the line cost above (or below) the quotation."""

        if self.difference is None or self.quantity is None:
            return None
        return self.difference * self.quantity


def supplier_code(label: str | None) -> str | None:
    """``"399 - UNILEVER ANDINA S.A"`` → ``"399"``; a label without a code stays ``None``."""

    if not label:
        return None
    code, separator, _ = label.partition(" - ")
    code = code.strip()
    return code if separator and code else None


def supplier_name(label: str | None) -> str | None:
    """``"399 - UNILEVER ANDINA S.A"`` → ``"UNILEVER ANDINA S.A"``."""

    if not label:
        return None
    _, separator, name = label.partition(" - ")
    return (name.strip() or None) if separator else label.strip() or None


_QUOTATION_PLACES = Decimal("0.0001")


def discount_fraction(points: Decimal | None) -> Decimal | None:
    """The warehouse's Tasa 1 (``descuento_escala_pct``, in points: ``20.0000``) as a positive
    fraction (``0.2``)."""

    return None if points is None else abs(points) / 100


def net_quotation(list_price: Decimal | None, discount: Decimal | None) -> Decimal | None:
    """The quotation's unit price less its Tasa 1 (a fraction), to four places."""

    if list_price is None:
        return None
    return (list_price * (1 - (discount or 0))).quantize(_QUOTATION_PLACES)


def difference_pct(price: Decimal | None, quotation: Decimal | None) -> Decimal | None:
    """``price / quotation - 1``; ``None`` without both or with a zero quotation."""

    if price is None or quotation is None or quotation == 0:
        return None
    return price / quotation - 1


def entry_status(
    price: Decimal, quotation: Decimal | None, threshold: Decimal = DEFAULT_THRESHOLD
) -> str:
    """Whether a line matches its quotation: within ``threshold`` (a fraction) it is ok.

    Args:
        price (Decimal): Unit price of the entry.
        quotation (Decimal | None): Unit price of the quotation in force on its day.
        threshold (Decimal): Largest relative difference that is still no novelty.

    Returns:
        str: ``ok``, ``novedad`` or ``sin_cotizacion``.
    """

    pct = difference_pct(price, quotation)
    if pct is None:
        return NO_QUOTATION
    return OK if abs(pct) <= threshold else NOVELTY


def with_status(lines: Iterable[EntryLine], threshold: Decimal) -> list[EntryLine]:
    """The lines with their status worked out against ``threshold``."""

    return [
        replace(line, status=entry_status(line.unit_price, line.quotation, threshold))
        for line in lines
    ]


def fill_previous_prices(lines: Sequence[EntryLine]) -> list[EntryLine]:
    """The previous two prices of each reference and unit, inside the lines given.

    The warehouse works them out over the whole history when it publishes them; this is the
    fallback while it does not: the first purchases of the window have none. Lines go in date
    order, then operation center and line key, so a day with two entries is deterministic.
    """

    ordered = sorted(lines, key=lambda line: (line.entry_date, line.co or "", line.line_key))
    seen: dict[tuple[str, str], list[Decimal]] = {}
    filled: dict[str, EntryLine] = {}
    for line in ordered:
        history = seen.setdefault((line.sku, (line.unit or "").strip().upper()), [])
        filled[line.line_key] = replace(
            line,
            previous_price=history[-1] if history else None,
            antepenultimate_price=history[-2] if len(history) > 1 else None,
        )
        history.append(line.unit_price)
    return [filled[line.line_key] for line in lines]


@dataclass(frozen=True)
class EntryFilters:
    """What the entries analysis narrows by (the period is apart)."""

    # Reference prefix, description, document or purchase order.
    q: str | None = None
    status: str | None = None
    # Only lines with a novelty (``novedad`` and ``sin_cotizacion``).
    novelty_only: bool = False
    # Commercial supplier code («399»).
    supplier: str | None = None
    co: str | None = None
    warehouse: str | None = None
    reason_id: UUID | None = None


@dataclass(frozen=True)
class NoveltyBrief:
    """What the platform keeps of a line, shown beside it in the analysis."""

    id: UUID
    status: str
    reason_id: UUID | None
    reason_name: str | None
    credit_note_number: str | None
    credit_note_amount: Decimal | None


@dataclass(frozen=True)
class EntryRow:
    """An entry line and what the platform keeps of it, if anything."""

    line: EntryLine
    novelty: NoveltyBrief | None = None


@dataclass(frozen=True)
class CenterOption:
    """An operation center to filter by: its code («003») and its name in SIESA («CENTRO»)."""

    code: str
    name: str | None


@dataclass(frozen=True)
class WarehouseOption:
    """A warehouse to filter by, with the operation center it belongs to."""

    code: str
    co: str | None


@dataclass(frozen=True)
class FilterOptions:
    """The operation centers and warehouses a period has lines in, by code."""

    cos: tuple[CenterOption, ...]
    warehouses: tuple[WarehouseOption, ...]


@dataclass(frozen=True)
class Placement:
    """Where a line was received: what the filter options are built from."""

    co: str | None
    warehouse: str | None
    regional_name: str | None = None


def filter_options(places: Iterable[Placement], names: Mapping[str, str]) -> FilterOptions:
    """The distinct centers and warehouses of the lines, by code.

    A center takes its SIESA name from ``names``; without it, its regional's.
    """

    centers: dict[str, str | None] = {}
    warehouses: dict[str, str | None] = {}
    for place in places:
        co = (place.co or "").strip()
        if co and centers.get(co) is None:
            centers[co] = names.get(co) or place.regional_name
        warehouse = (place.warehouse or "").strip().upper()
        if warehouse and warehouse not in warehouses:
            warehouses[warehouse] = co or None
    return FilterOptions(
        cos=tuple(CenterOption(code, centers[code]) for code in sorted(centers)),
        warehouses=tuple(WarehouseOption(code, warehouses[code]) for code in sorted(warehouses)),
    )


@dataclass(frozen=True)
class EntryPage:
    items: tuple[EntryRow, ...]
    total: int
    page: int
    size: int
    since: date
    until: date
    # Lines by status for the whole search (before the status filter), and how many have a
    # reason.
    counts: dict[str, int] = field(default_factory=dict)
    # When the warehouse was read; the analysis is kept a few minutes.
    fetched_at: datetime | None = None
    # Whether the previous prices come from the warehouse (whole history) or the window only.
    previous_from_warehouse: bool = True


__all__ = [
    "DEFAULT_THRESHOLD",
    "ENTRY_SORTS",
    "ENTRY_STATUSES",
    "ENTRY_STATUS_LABELS",
    "NOVELTY",
    "NOVELTY_STATUSES",
    "NO_QUOTATION",
    "OK",
    "CenterOption",
    "EntryFilters",
    "FilterOptions",
    "Placement",
    "WarehouseOption",
    "filter_options",
    "EntryLine",
    "EntryPage",
    "EntryRow",
    "NoveltyBrief",
    "difference_pct",
    "discount_fraction",
    "entry_status",
    "fill_previous_prices",
    "net_quotation",
    "supplier_code",
    "supplier_name",
    "with_status",
]
