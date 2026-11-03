"""Price list rules of the commercial area, as the project skill ``lista-precios`` sets them.

The skill (``.claude/skills/lista-precios/SKILL.md``) is how the price list analyst updates
the lists; the quoter applies the same rules so a quoted price matches the list it lands in:

- **Cost** = the highest of the supplier's quotation in force and the inventory average cost
  of the list's warehouse, rounded up, times the list family's factor (1,004 except the
  wholesale lists).
- **Warehouse** of a list: fixed by the area (sheet "Faltantes en Lista"); warehouse 2 for
  frozen and refrigerated lines. List 1 takes the highest cost among its 17 warehouses.
  A list outside the skill uses the warehouses of the customer's billing operation center.
- **Lactalis** quotes by branch: branch 003 for the Centro lists, 001 for the rest.
- **Price** = cost / (1 - margin), rounded to whole pesos (up for the salsamentarias lists).
- **Liquor tax**: the Coast (billing operation center 004) pays its own value.

Validated against the warehouse in ``docs/cotizador-dwh.md`` (second round).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.modules.quotations.domain import vocabulary

ONE = Decimal(1)
PESO = Decimal(1)

GENERAL = "generales"
WHOLESALE = "mayoristas"
SALSAMENTARIAS = "salsamentarias"
MARKET = "market"
FAMILY_LABELS = {
    GENERAL: "Generales",
    WHOLESALE: "Mayoristas",
    SALSAMENTARIAS: "Salsamentarias",
    MARKET: "Market",
}

# Lists of each family in the skill.
FAMILY_LISTS = {
    GENERAL: ("1", "001", "002", "003", "004", "005", "009"),
    WHOLESALE: ("1M", "10T", "10U", "11N", "1DR", "10N"),
    SALSAMENTARIAS: ("10X", "10R", "10S"),
    MARKET: ("35P",),
}
# Factor on the cost by family (the wholesale lists do not carry it).
COST_FACTOR = {
    GENERAL: Decimal("1.004"),
    WHOLESALE: ONE,
    SALSAMENTARIAS: Decimal("1.004"),
    MARKET: Decimal("1.004"),
}
# Rounding of the price by family: up for salsamentarias and Market, nearest for the rest.
PRICE_ROUNDING = {
    GENERAL: ROUND_HALF_UP,
    WHOLESALE: ROUND_HALF_UP,
    SALSAMENTARIAS: ROUND_CEILING,
    MARKET: ROUND_CEILING,
}

# Warehouse 1 and warehouse 2 (frozen and refrigerated lines) of each list.
LIST_WAREHOUSES: dict[str, tuple[str, str]] = {
    "001": ("MDE1", "MDE3"),
    "1M": ("MDE1", "MDE3"),
    "10X": ("MDE1", "MDE3"),
    "002": ("OCC1", "OCC16"),
    "10T": ("OCC1", "OCC16"),
    "10R": ("OCC1", "OCC16"),
    "003": ("BTA9", "BTA8"),
    "10U": ("BTA9", "BTA8"),
    "10S": ("BTA9", "BTA8"),
    "004": ("CTG1", "CTG15"),
    "11N": ("CTG1", "CTG15"),
    "005": ("BGA1", "BGA1"),
    "10N": ("BGA1", "BGA1"),
    "009": ("BAQ1", "BAQ2"),
    "1DR": ("BAQ1", "BAQ2"),
    "35P": ("URA6", "URA6"),
}
# The same warehouses by billing operation center, for lists outside the skill.
CENTER_WAREHOUSES: dict[str, tuple[str, str]] = {
    "001": ("MDE1", "MDE3"),
    "002": ("OCC1", "OCC16"),
    "003": ("BTA9", "BTA8"),
    "004": ("CTG1", "CTG15"),
    "005": ("BGA1", "BGA1"),
    "009": ("BAQ1", "BAQ2"),
}
# List 1 takes the highest cost among the main warehouses and cold rooms (no Urabá, no
# Valledupar), as the area set it on 2026-10-27.
GENERAL_WAREHOUSES = (
    "MDE1",
    "MDE3",
    "MDE4",
    "OCC1",
    "OCC16",
    "OCC14",
    "BTA9",
    "BTA8",
    "BTA20",
    "CTG1",
    "CTG15",
    "CTG14",
    "BGA1",
    "BGA11",
    "BAQ1",
    "BAQ2",
    "BAQ10",
)
# Product lines that go to warehouse 2.
COLD_LINES = ("CONGELADOS", "REFRIGERADOS")

LACTALIS_NIT = "800245795"
CENTRAL_LISTS = ("003", "10U", "10S")
CENTRAL_CENTER = "003"

COAST_CENTERS = ("004",)

# A quotation above this many times the median last cost is a capture error in SIESA (a
# case price keyed as a unit): it is not used and is shown to review.
QUOTE_OUTLIER_FACTOR = Decimal(3)
# Costs at or below one peso are placeholders (obsolete stock): never a cost.
MIN_COST = ONE


@dataclass(frozen=True)
class StockPlan:
    """Where the inventory cost of a quotation comes from.

    ``warehouses`` is ``(warehouse 1, warehouse 2)`` for one list or operation center; with
    ``highest`` the cost is the highest among ``GENERAL_WAREHOUSES`` (list 1).
    """

    warehouses: tuple[str, str] | None
    highest: bool
    source: str
    lactalis_branch: str

    @property
    def label(self) -> str:
        if self.highest:
            return "Mayor costo entre las bodegas de la lista 1"
        primary, cold = self.warehouses or ("", "")
        return primary if primary == cold else f"{primary} / {cold} (congelados y refrigerados)"


def family(price_list_id: str | None, margin_basis: str | None = None) -> str:
    """The list family: by the list when the skill names it, else by the channel's margin."""

    for name, lists in FAMILY_LISTS.items():
        if price_list_id in lists:
            return name
    if margin_basis == "mayoristas":
        return WHOLESALE
    if margin_basis == "salsamentarias":
        return SALSAMENTARIAS
    return GENERAL


def stock_plan(price_list_id: str | None, billing_center: str | None) -> StockPlan:
    """Warehouses whose cost a quotation uses, and the Lactalis branch it quotes from."""

    if price_list_id == vocabulary.GENERAL_PRICE_LIST:
        return StockPlan(None, True, "lista", "001")
    if price_list_id in LIST_WAREHOUSES:
        branch = CENTRAL_CENTER if price_list_id in CENTRAL_LISTS else "001"
        return StockPlan(LIST_WAREHOUSES[price_list_id], False, "lista", branch)
    branch = CENTRAL_CENTER if billing_center == CENTRAL_CENTER else "001"
    if billing_center in CENTER_WAREHOUSES:
        return StockPlan(CENTER_WAREHOUSES[billing_center], False, "co", branch)
    # No rule for the operation center (Urabá, Valledupar, Yumbo, Quindío, Market, none).
    return StockPlan(None, True, "general", branch)


def regional(billing_center: str | None) -> str:
    return vocabulary.COAST if billing_center in COAST_CENTERS else vocabulary.NATIONAL


def effective_cost(
    quote: Decimal | None, inventory: Decimal | None, factor: Decimal
) -> tuple[Decimal | None, str | None]:
    """The highest of quotation and inventory, rounded up to pesos, times the factor.

    Returns:
        tuple[Decimal | None, str | None]: The cost (4 decimals) and which source won.
    """

    usable = {
        source: value
        for source, value in (
            (vocabulary.QUOTE_COST, quote),
            (vocabulary.INVENTORY_COST, inventory),
        )
        if value is not None and value > MIN_COST
    }
    if not usable:
        return None, None
    # A tie goes to the quotation: it is what the supplier charges today.
    source = max(usable, key=lambda key: (usable[key], key == vocabulary.QUOTE_COST))
    cost = usable[source].to_integral_value(rounding=ROUND_CEILING) * factor
    return cost.quantize(Decimal("0.0001")), source


def round_price(value: Decimal, family_name: str) -> Decimal:
    """A price in whole pesos, rounded as the family's lists are."""

    return value.quantize(PESO, rounding=PRICE_ROUNDING.get(family_name, ROUND_HALF_UP))


__all__ = [
    "COST_FACTOR",
    "FAMILY_LABELS",
    "StockPlan",
    "effective_cost",
    "family",
    "regional",
    "round_price",
    "stock_plan",
]
