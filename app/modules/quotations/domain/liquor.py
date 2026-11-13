"""The liquor tax of the price lists: what each list carries and a bulk change of it.

A liquor (tax class ``99``: key ``0024`` national or ``0011`` imported) carries its tax as a
value per unit on every price of every list (SIESA ``t127``, "impuestos en valor"). The value
depends on the list's regional: the Coast lists pay their own (``LIQUOR_TAX_COAST_LISTS``,
004, 11N, 22N, 36J and 46V as of 2026-11-12) and every other list pays the national one. The
regional cannot be derived from the operation center: list 009 is on the Coast and pays the
national value.

A bulk change sets the national and Coast value of some SKUs and writes them to **every
active list** where the SKU has a price in force: SIESA's two pages, the price (record
``0126``, unchanged) and its tax (record ``0127``, the new value).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.quotations.domain.entities import ProductMaster
from app.modules.quotations.domain.export import ExportRow, ExportTaxRow, PriceListExport

PENDING = "pendiente_validacion"
UPLOAD_PENDING = "pendiente_carga"
UPLOADED = "cargada"
RETURNED = "devuelta"
STATUSES = (PENDING, UPLOAD_PENDING, UPLOADED, RETURNED)
STATUS_LABELS = {
    PENDING: "Pendiente de validación",
    UPLOAD_PENDING: "Pendiente de carga",
    UPLOADED: "Cargada",
    RETURNED: "Anulada",
}
# The general list: its value is the national reference.
NATIONAL_REFERENCE = "1"
# Most SKUs one change may carry.
MAX_CHANGES = 200
MAX_TAX = Decimal(10) ** 9
NOTE_LENGTH = 500

# What the screen flags on a liquor.
SEVERAL_VALUES = "varios_valores"
MISSING_VALUE = "sin_valor"
DIFFERS_FROM_MASTER = "difiere_maestro"
FLAG_LABELS = {
    SEVERAL_VALUES: "Tiene valores distintos entre listas de la misma regional",
    MISSING_VALUE: "Hay listas con precio (no marcador) y sin impuesto",
    DIFFERS_FROM_MASTER: "El valor de las listas no es el del maestro de márgenes",
}


@dataclass(frozen=True)
class LiquorListPrice:
    """A liquor's price in force on one list (inventory-unit row) and its tax per unit."""

    price_list_id: str
    list_active: bool
    unit: str | None
    price: Decimal | None
    tax_amount: Decimal | None
    valid_from: date | None = None


@dataclass(frozen=True)
class LiquorProduct:
    """An active product with a liquor tax key and its price on every list."""

    sku: str
    description: str | None
    tax_key: str
    tax_key_name: str | None
    tax_group: str | None
    prices: tuple[LiquorListPrice, ...]


@dataclass(frozen=True)
class LiquorSuspect:
    """An active liquor or wine without the liquor tax key (a tax group of liquor, such as
    ``0507``): the area decides whether it should carry it."""

    sku: str
    description: str | None
    tax_group: str | None
    tax_group_name: str | None
    lists_with_value: int


@dataclass(frozen=True)
class TaxValue:
    """One value a liquor carries and the lists that carry it."""

    amount: Decimal | None
    lists: tuple[str, ...]


@dataclass(frozen=True)
class LiquorSummary:
    """A liquor on the screen: the value of each regional, every distinct value with its
    lists, the margin master's values and what does not add up."""

    product: LiquorProduct
    national: Decimal | None
    coast: Decimal | None
    national_values: tuple[TaxValue, ...]
    coast_values: tuple[TaxValue, ...]
    master_national: Decimal | None
    master_coast: Decimal | None
    lists_with_price: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class LiquorTaxChange:
    """New values of one SKU in a bulk change, with the ones it replaces."""

    sku: str
    description: str | None
    tax_key: str
    tax_group: str | None
    national: Decimal
    coast: Decimal
    previous_national: Decimal | None = None
    previous_coast: Decimal | None = None
    lists: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "description": self.description,
            "tax_key": self.tax_key,
            "tax_group": self.tax_group,
            "national": str(self.national),
            "coast": str(self.coast),
            "previous_national": _text(self.previous_national),
            "previous_coast": _text(self.previous_coast),
            "lists": self.lists,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LiquorTaxChange:
        return cls(
            sku=data["sku"],
            description=data.get("description"),
            tax_key=data["tax_key"],
            tax_group=data.get("tax_group"),
            national=Decimal(str(data["national"])),
            coast=Decimal(str(data["coast"])),
            previous_national=_decimal(data.get("previous_national")),
            previous_coast=_decimal(data.get("previous_coast")),
            lists=int(data.get("lists") or 0),
        )


@dataclass(frozen=True)
class LiquorTaxUpdate:
    """A bulk change of the liquor tax and its review.

    ``export_rows`` are the prices it writes (``{price_list_id, sku, unit, price, tax_key,
    amount}``), read from the lists when it is validated and fixed from then on.
    """

    id: UUID
    status: str
    changes: tuple[LiquorTaxChange, ...]
    export_rows: tuple[dict[str, Any], ...]
    note: str | None
    review_note: str | None
    activation_date: date | None
    created_by: str
    created_at: datetime
    validated_by: str | None = None
    validated_at: datetime | None = None
    uploaded_by: str | None = None
    uploaded_at: datetime | None = None
    version: int = 1


def regional_lists(coast_lists: Iterable[str]) -> frozenset[str]:
    return frozenset(code.strip() for code in coast_lists if code.strip())


def _values(prices: Sequence[LiquorListPrice]) -> tuple[TaxValue, ...]:
    """Distinct values, the most common first; ``None`` (no value) last."""

    by_amount: dict[Decimal | None, list[str]] = {}
    for price in prices:
        by_amount.setdefault(price.tax_amount, []).append(price.price_list_id)
    ordered = sorted(
        by_amount.items(), key=lambda item: (item[0] is None, -len(item[1]), item[0] or 0)
    )
    return tuple(TaxValue(amount, tuple(sorted(lists))) for amount, lists in ordered)


def _reference(values: Sequence[TaxValue], preferred: Decimal | None) -> Decimal | None:
    if preferred is not None:
        return preferred
    return next((value.amount for value in values if value.amount is not None), None)


def summarize(
    product: LiquorProduct, coast_lists: frozenset[str], master: ProductMaster | None
) -> LiquorSummary:
    """A liquor's values per regional, over its prices in active lists.

    The national value is list 1's when it has one, else the most common among the
    national lists; the Coast value, the most common among the Coast lists (the national
    one when no Coast list prices it).
    """

    active = [price for price in product.prices if price.list_active]
    national_prices = [price for price in active if price.price_list_id not in coast_lists]
    coast_prices = [price for price in active if price.price_list_id in coast_lists]
    national_values = _values(national_prices)
    coast_values = _values(coast_prices)
    general = next(
        (
            price.tax_amount
            for price in national_prices
            if price.price_list_id == NATIONAL_REFERENCE and price.tax_amount is not None
        ),
        None,
    )
    national = _reference(national_values, general)
    coast = _reference(coast_values, None) or national
    flags: list[str] = []
    if (
        sum(1 for value in national_values if value.amount is not None) > 1
        or sum(1 for value in coast_values if value.amount is not None) > 1
    ):
        flags.append(SEVERAL_VALUES)
    # A $1 placeholder price is no real price: it does not count as missing its tax.
    if any(
        price.tax_amount is None and price.price is not None and price.price > 1 for price in active
    ):
        flags.append(MISSING_VALUE)
    master_national = master.liquor_tax if master else None
    master_coast = master.liquor_tax_coast if master else None
    if (master_national is not None and national is not None and master_national != national) or (
        master_coast is not None and coast is not None and master_coast != coast
    ):
        flags.append(DIFFERS_FROM_MASTER)
    return LiquorSummary(
        product=product,
        national=national,
        coast=coast,
        national_values=national_values,
        coast_values=coast_values,
        master_national=master_national,
        master_coast=master_coast,
        lists_with_price=len(active),
        flags=tuple(flags),
    )


def export_rows(
    products: Mapping[str, LiquorProduct],
    changes: Sequence[LiquorTaxChange],
    coast_lists: frozenset[str],
) -> list[dict[str, Any]]:
    """The price and the new tax of each changed SKU on every active list that prices it.

    The price goes as SIESA has it (the row's unit), so the ``0126`` record leaves it as it
    is; the ``0127`` carries the value of the list's regional.
    """

    rows: list[dict[str, Any]] = []
    for change in changes:
        product = products.get(change.sku)
        if product is None:
            continue
        for price in sorted(product.prices, key=lambda item: item.price_list_id):
            if not price.list_active or price.price is None or price.price <= 0 or not price.unit:
                continue
            amount = change.coast if price.price_list_id in coast_lists else change.national
            rows.append(
                {
                    "price_list_id": price.price_list_id,
                    "sku": change.sku,
                    "unit": price.unit,
                    "price": str(price.price),
                    "tax_key": change.tax_key,
                    "amount": str(amount),
                }
            )
    return rows


def to_export(rows: Sequence[Mapping[str, Any]], activation: date) -> PriceListExport:
    """The rows as the two pages of the SIESA files: prices, then each price's tax."""

    prices = tuple(
        ExportRow(row["price_list_id"], row["sku"], activation, row["unit"], Decimal(row["price"]))
        for row in rows
    )
    taxes = tuple(
        ExportTaxRow(
            row["price_list_id"],
            row["sku"],
            activation,
            row["unit"],
            row["tax_key"],
            Decimal(row["amount"]),
        )
        for row in rows
        if Decimal(row["amount"]) > 0
    )
    return PriceListExport(prices, taxes)


def list_count(rows: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """Lists each SKU of the rows reaches."""

    return Counter(row["sku"] for row in rows)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))
