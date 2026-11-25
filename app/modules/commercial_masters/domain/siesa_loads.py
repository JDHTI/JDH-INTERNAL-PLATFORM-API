"""Loading the pending increases into SIESA: the supplier quotation file (Impodatos ``0212``).

An increase of the history without a SIESA date is pending: the quoter compares its new cost.
Whoever manages the history picks pending increases (one by one or every one of a filter) and
generates a **load**: the import file (``.txt``) and its workbook (``IncrementoCostos
Entradas``). They load the file in SIESA and **confirm** the load, which dates its increases
(``system_updated_on``); or they **void** it and the increases are free again. An increase is
in one open load at a time.

Each increase becomes the supplier quotation SIESA keeps per reference (``t212``):

- **Supplier, branch, unit and delivery days:** those of the reference's quotation in force in
  its inventory unit, the latest activated, as the supplier's «MAESTRA» takes its old cost.
- **Price:** the new cost without the healthy-food tax (SIESA's unit price before taxes).
- **Activation:** the increase date; a past one is the day the file is generated, so SIESA's
  cost never moves backwards (decision of 2026-11-25).

What cannot go (:func:`plan` says why): an increase of one regional (SIESA's quotation is the
whole company's, decision of 2026-11-25), without a new cost or an increase date, already in
SIESA, in another open load, a reference without a quotation in force, or an older increase of
a reference picked twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.modules.commercial_masters.domain.price_increases import PriceIncrease, regional_key

GENERATED = "generado"
LOADED = "cargado"
VOIDED = "anulado"
LOAD_STATUSES = (GENERATED, LOADED, VOIDED)
LOAD_STATUS_LABELS = {GENERATED: "Generado", LOADED: "Cargado en SIESA", VOIDED: "Anulado"}
# The most increases one load takes (the importer reads thousands; a load is a day's work).
MAX_LOAD_ROWS = 2000
# SIESA's quotation has no end: the area's files close it on 2099-12-31.
OPEN_END = date(2099, 12, 31)
_PRICE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class CurrentQuotation:
    """A reference's supplier quotation in force in its inventory unit (the warehouse)."""

    sku: str
    nit: str
    branch: str
    unit: str
    currency: str
    delivery_days: int
    activated_on: date | None = None
    # Other suppliers with a quotation in force for the reference (their NITs).
    others: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadRow:
    """One increase as it goes to SIESA."""

    increase_id: UUID
    sku: str
    description: str | None
    nit: str
    branch: str
    currency: str
    unit: str
    price: Decimal
    activation_date: date
    delivery_days: int
    increased_on: date | None = None
    # Other suppliers with a quotation in force: SIESA gets the latest one's.
    other_nits: tuple[str, ...] = ()


@dataclass(frozen=True)
class Excluded:
    """A picked increase that does not go, and why."""

    increase_id: UUID
    sku: str
    reason: str


@dataclass(frozen=True)
class SiesaLoad:
    """A generated file of pending increases and what became of it."""

    id: UUID
    number: int
    status: str
    row_count: int
    created_by: str
    created_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    loaded_on: date | None = None
    voided_by: str | None = None
    voided_at: datetime | None = None
    rows: tuple[LoadRow, ...] = ()


@dataclass(frozen=True)
class LoadPage:
    items: tuple[SiesaLoad, ...]
    total: int
    page: int
    size: int
    counts: dict[str, int]


@dataclass(frozen=True)
class LoadPlan:
    """What a selection would load, and what it leaves out."""

    rows: tuple[LoadRow, ...]
    excluded: tuple[Excluded, ...]


def _newest_first(item: PriceIncrease) -> tuple:
    values = item.values
    return (
        values.increased_on or date.min,
        values.notified_on or date.min,
        values.occurrence,
    )


def plan(
    increases: Sequence[PriceIncrease],
    quotations: Mapping[str, CurrentQuotation],
    open_loads: Mapping[UUID, int],
    today: date,
) -> LoadPlan:
    """The load of the picked increases (see the module's rules), in reference order.

    Args:
        increases: The picked increases.
        quotations: The quotation in force of each reference, by reference.
        open_loads: Increases already in an open load, with its number.
        today: The day the file is generated (Bogotá).
    """

    excluded: list[Excluded] = []
    candidates: dict[str, PriceIncrease] = {}
    for item in sorted(increases, key=_newest_first, reverse=True):
        values = item.values
        reason = None
        if values.system_updated_on is not None:
            reason = "Ya está cargada en SIESA"
        elif item.id in open_loads:
            reason = f"Ya está en el lote {open_loads[item.id]}, sin confirmar"
        elif regional_key(values.regional) is not None:
            reason = (
                f"Es un alza de la regional {values.regional.strip()}: la cotización de SIESA "
                "es de toda la compañía"
            )
        elif values.new_cost is None or values.new_cost <= 0:
            reason = "No tiene costo nuevo sin impuesto saludable"
        elif values.increased_on is None:
            reason = "No tiene fecha de incremento"
        elif values.sku in candidates:
            reason = "La referencia tiene un alza más reciente en la selección"
        elif values.sku not in quotations:
            reason = (
                "La referencia no tiene cotización vigente en SIESA en su unidad de inventario; "
                "la primera se crea a mano"
            )
        if reason:
            excluded.append(Excluded(item.id, values.sku, reason))
        else:
            candidates[values.sku] = item
    rows = []
    for sku in sorted(candidates):
        item = candidates[sku]
        quote = quotations[sku]
        increased_on = item.values.increased_on
        rows.append(
            LoadRow(
                increase_id=item.id,
                sku=sku,
                description=item.description,
                nit=quote.nit,
                branch=quote.branch,
                currency=quote.currency,
                unit=quote.unit,
                price=item.values.new_cost.quantize(_PRICE_PLACES),
                activation_date=max(increased_on, today),
                delivery_days=quote.delivery_days,
                increased_on=increased_on,
                other_nits=quote.others,
            )
        )
    excluded.sort(key=lambda item: (item.sku, item.reason))
    return LoadPlan(tuple(rows), tuple(excluded))


def latest_quotations(rows: Iterable[CurrentQuotation]) -> dict[str, CurrentQuotation]:
    """One quotation per reference: the latest activated; the others' NITs go with it."""

    by_sku: dict[str, list[CurrentQuotation]] = {}
    for row in rows:
        by_sku.setdefault(row.sku, []).append(row)
    chosen: dict[str, CurrentQuotation] = {}
    for sku, items in by_sku.items():
        items.sort(key=lambda item: item.activated_on or date.min, reverse=True)
        first = items[0]
        others = tuple(sorted({item.nit for item in items[1:] if item.nit != first.nit}))
        chosen[sku] = CurrentQuotation(
            sku=first.sku,
            nit=first.nit,
            branch=first.branch,
            unit=first.unit,
            currency=first.currency,
            delivery_days=first.delivery_days,
            activated_on=first.activated_on,
            others=others,
        )
    return chosen


def file_name(day: date, number: int, extension: str) -> str:
    """``IncrementoCostos Entradas 18-11-2026 L12.txt``: the area's name, with the load."""

    return f"IncrementoCostos Entradas {day:%d-%m-%Y} L{number}.{extension}"


__all__ = [
    "GENERATED",
    "LOADED",
    "LOAD_STATUSES",
    "LOAD_STATUS_LABELS",
    "MAX_LOAD_ROWS",
    "OPEN_END",
    "VOIDED",
    "CurrentQuotation",
    "Excluded",
    "LoadPage",
    "LoadPlan",
    "LoadRow",
    "SiesaLoad",
    "file_name",
    "latest_quotations",
    "plan",
]
