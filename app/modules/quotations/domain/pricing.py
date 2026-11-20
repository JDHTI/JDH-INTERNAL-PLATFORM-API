"""Price list rules of the commercial area, as the project skill ``lista-precios`` sets them.

The skill (``.claude/skills/lista-precios/SKILL.md``) is how the price list analyst updates
the lists; the quoter applies the same rules so a quoted price matches the list it lands in:

- **Cost** = the highest of the supplier's quotation in force and the inventory average cost
  of the list's warehouse, rounded up, times the list family's factor (1,004 except the
  wholesale lists). A product whose latest price increase SIESA does not have yet (no system
  update date in the product master's history) compares its new cost too. Only warehouses
  with stock give the inventory cost (2026-11-19); when none has any, their last average
  cost stays so the line is not left without cost.
- **Warehouse** of a list: fixed by the area (sheet "Faltantes en Lista"); warehouse 2 for
  frozen and refrigerated lines. List 1 takes the highest cost among its 17 warehouses.
  A list outside the skill uses the warehouses of the operation centers the quotation is
  costed from: *Nacional* (the highest among those 17 warehouses, SKU by SKU), one center,
  or several (each SKU takes the costliest of them).
- **Lactalis** quotes by branch: branch 003 for the Centro lists, 001 for the rest.
- **Price** = cost / (1 - margin), rounded to whole pesos (up for the salsamentarias lists).
- **Liquor tax**: the Coast (billing operation center 004) pays its own value.

Validated against the warehouse in ``docs/cotizador-dwh.md`` (second round).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.modules.quotations.domain import vocabulary
from app.modules.quotations.domain.entities import CostInfo

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
# The sellable warehouses of each operation center whose stock a quotation or a list update
# shows (2026-11-19): the same 17 of list 1, by center. A center without a row (Urabá,
# Valledupar, Yumbo…) is read by the warehouses SIESA places in it (``co_bodega``).
CENTER_STOCK_WAREHOUSES: dict[str, tuple[str, ...]] = {
    "001": ("MDE1", "MDE3", "MDE4"),
    "002": ("OCC1", "OCC16", "OCC14"),
    "003": ("BTA9", "BTA8", "BTA20"),
    "004": ("CTG1", "CTG15", "CTG14"),
    "005": ("BGA1", "BGA11"),
    "009": ("BAQ1", "BAQ2", "BAQ10"),
}
# Product lines that go to warehouse 2.
COLD_LINES = ("CONGELADOS", "REFRIGERADOS")

LACTALIS_NIT = "800245795"
CENTRAL_LISTS = ("003", "10U", "10S")
CENTRAL_CENTER = "003"

COAST_CENTERS = ("004",)
# Source of the plan of a quotation costed *Nacional*: no center, every warehouse.
NATIONAL_SOURCE = "nacional"
# Source of the plan of a center whose warehouses were chosen (2026-11-20).
WAREHOUSES_SOURCE = "bodegas"
# SIESA's warehouse groups for the platform (``consumo.bodegas.clasificacion_bodega``,
# 2026-11-19): the only warehouses that can be chosen inside a center. Filtered by the code:
# other SIESA groups are also named "BODEGAS PRINCIPALES".
MAIN_WAREHOUSES = "BP"
SPOT_WAREHOUSES = "BSP"
SITE_CLASSES = (MAIN_WAREHOUSES, SPOT_WAREHOUSES)
# The promotional warehouses (``consumo.bodegas.promocional = 'Si'``, SIESA group 23) can be
# added to a center too (2026-11-20): read for every line, on top of the center's own.
PROMO_WAREHOUSES = "PROMO"
WAREHOUSE_CLASSES = {
    MAIN_WAREHOUSES: "Bodegas principales",
    SPOT_WAREHOUSES: "Bodegas solicitudes puntuales",
    PROMO_WAREHOUSES: "Bodegas promocionales",
}

# A quotation above this many times the median last cost is a capture error in SIESA (a
# case price keyed as a unit): it is not used and is shown to review.
QUOTE_OUTLIER_FACTOR = Decimal(3)
# Costs at or below one peso are placeholders (obsolete stock): never a cost.
MIN_COST = ONE


@dataclass(frozen=True)
class StockPlan:
    """Where the inventory cost of a quotation comes from.

    ``warehouses`` is ``(warehouse 1, warehouse 2)`` for one list or operation center; with
    ``highest`` the cost is the highest among ``GENERAL_WAREHOUSES`` (list 1). ``selected``
    are the warehouses chosen inside a center (2026-11-20) and ``cold_room`` the center's
    cold room, if it is one of them. ``promos`` are the promotional warehouses added to the
    center, read for every line.
    """

    warehouses: tuple[str, str] | None
    highest: bool
    source: str
    lactalis_branch: str
    selected: tuple[str, ...] = ()
    cold_room: str | None = None
    promos: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        promos = f" + promocionales {', '.join(self.promos)}" if self.promos else ""
        return self._base_label + promos

    @property
    def _base_label(self) -> str:
        if self.source == NATIONAL_SOURCE:
            return "Nacional: mayor costo entre todas las bodegas"
        if self.selected:
            return "Bodegas " + ", ".join(self.selected)
        if self.highest:
            return "Mayor costo entre las bodegas de la lista 1"
        primary, cold = self.warehouses or ("", "")
        return primary if primary == cold else f"{primary} / {cold} (congelados y refrigerados)"

    def reads(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The warehouses whose inventory cost a line reads: ``(other lines, frozen and
        refrigerated lines)``; among several, the highest with stock.

        - ``highest``: every list 1 warehouse, for every line.
        - Chosen warehouses: frozen and refrigerated lines read the cold room when it was
          chosen, the rest the other chosen ones; a line with none of its kind chosen reads
          every chosen one.
        - Otherwise warehouse 1 and warehouse 2.
        - The promotional warehouses added to the center, for every line: when they and the
          center's both have stock, the highest cost wins.
        """

        dry, cold = self._base_reads()
        extra = tuple(code for code in self.promos if code not in dry)
        extra_cold = tuple(code for code in self.promos if code not in cold)
        return dry + extra, cold + extra_cold

    def _base_reads(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if self.selected:
            cold = tuple(code for code in self.selected if code == self.cold_room)
            dry = tuple(code for code in self.selected if code != self.cold_room)
            return dry or self.selected, cold or self.selected
        if self.highest:
            return GENERAL_WAREHOUSES, GENERAL_WAREHOUSES
        primary, cold = self.warehouses or ("", "")
        return (primary,), (cold,)


def family(price_list_id: str | None, margin_basis: str | None = None) -> str:
    """The list family: by the list when the skill names it, else by the channel's margin."""

    for name, lists in FAMILY_LISTS.items():
        if price_list_id in lists:
            return name
    if margin_basis == "mayoristas":
        return WHOLESALE
    if margin_basis == "salsamentarias":
        return SALSAMENTARIAS
    if margin_basis == "market":
        return MARKET
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


def centers_set_cost(price_list_id: str | None) -> bool:
    """Whether the operation centers decide the warehouses: a list outside the skill.

    List 1 and the skill's lists have their own warehouses, whatever the center.
    """

    return price_list_id != vocabulary.GENERAL_PRICE_LIST and price_list_id not in LIST_WAREHOUSES


def warehouse_plan(center: str, warehouses: Sequence[str]) -> StockPlan:
    """The plan of a center whose warehouses were chosen (2026-11-20): the highest cost
    with stock among them, the cold room for frozen and refrigerated lines when chosen
    (:meth:`StockPlan.reads`)."""

    pair = CENTER_WAREHOUSES.get(center)
    cold = pair[1] if pair is not None and pair[0] != pair[1] else None
    branch = CENTRAL_CENTER if center == CENTRAL_CENTER else "001"
    return StockPlan(
        None,
        False,
        WAREHOUSES_SOURCE,
        branch,
        selected=tuple(dict.fromkeys(warehouses)),
        cold_room=cold if cold in warehouses else None,
    )


def chosen_warehouses(
    centers: Sequence[str], warehouses: Mapping[str, Sequence[str]] | None
) -> dict[str, tuple[str, ...]]:
    """The chosen warehouses of the chosen centers only, without empty entries."""

    return {code: tuple(found) for code in centers if (found := (warehouses or {}).get(code))}


def stock_plans(
    price_list_id: str | None,
    centers: Sequence[str],
    warehouses: Mapping[str, Sequence[str]] | None = None,
    promos: Mapping[str, Sequence[str]] | None = None,
) -> dict[str | None, StockPlan]:
    """The plans whose costs a quotation compares, by the center each one stands for.

    - Warehouses chosen inside some center, or promotional ones added to it (2026-11-20):
      one plan per center, the chosen warehouses for those centers (:func:`warehouse_plan`,
      whatever the list) and the plan of today for the rest, each plus its promotional
      warehouses (read for every line).
    - A list with its own warehouses: one plan, whatever the centers (key ``None``).
    - No centers (*Nacional*): one plan, the highest among every warehouse (key ``None``).
    - One or more centers: one plan per center; each SKU takes the costliest of them.
    """

    chosen = chosen_warehouses(centers, warehouses)
    added = chosen_warehouses(centers, promos)
    if chosen or added:
        return {
            code: replace(
                (
                    warehouse_plan(code, chosen[code])
                    if code in chosen
                    else stock_plan(price_list_id, code)
                ),
                promos=added.get(code, ()),
            )
            for code in centers
        }
    if not centers_set_cost(price_list_id):
        return {None: stock_plan(price_list_id, centers[0] if centers else None)}
    if not centers:
        return {None: StockPlan(None, True, NATIONAL_SOURCE, "001")}
    return {code: stock_plan(price_list_id, code) for code in centers}


@dataclass(frozen=True)
class StockScope:
    """Where the stock of a quotation or a list update is read (2026-11-19).

    ``warehouses`` are read by code; ``centers`` are operation centers without a row in
    :data:`CENTER_STOCK_WAREHOUSES`, read by every warehouse SIESA places in them.
    """

    warehouses: tuple[str, ...]
    centers: tuple[str, ...] = ()
    national: bool = False

    @property
    def label(self) -> str:
        if self.national:
            return "Nacional: todas las bodegas"
        parts = list(self.warehouses) + [f"C.O. {code}" for code in self.centers]
        return ", ".join(parts)


def stock_scope(
    price_list_id: str | None,
    centers: Sequence[str],
    warehouses: Mapping[str, Sequence[str]] | None = None,
    promos: Mapping[str, Sequence[str]] | None = None,
) -> StockScope:
    """The warehouses whose stock is shown, consolidated: those of the chosen operation
    centers (2026-11-19).

    - One or more centers (the quotation's filter): the sellable warehouses of each, summed;
      a center without a row is read by its own warehouses in SIESA. A center whose
      warehouses were chosen (2026-11-20) counts only those, plus the promotional ones
      added to it.
    - No center: *Nacional*, the 17 main warehouses of every center.

    The filter alone decides, whatever the list (a list's own warehouses still decide its
    cost, not its stock). Obsolete, transit or returns warehouses never count: the DWH
    cannot tell them apart (``bodegas.facturable`` is 'Si' for all but one), so the mapped
    centers use the list of sellable ones.
    """

    del price_list_id  # the list no longer decides the stock (2026-11-19)
    codes = tuple(dict.fromkeys(code for code in centers if code))
    if codes:
        chosen = chosen_warehouses(codes, warehouses)
        added = chosen_warehouses(codes, promos)
        own = [
            code
            for center in codes
            for code in (
                *chosen.get(center, CENTER_STOCK_WAREHOUSES.get(center, ())),
                *added.get(center, ()),
            )
        ]
        others = tuple(
            code for code in codes if code not in CENTER_STOCK_WAREHOUSES and code not in chosen
        )
        return StockScope(tuple(dict.fromkeys(own)), others)
    return StockScope(GENERAL_WAREHOUSES, national=True)


def stock_warehouses(
    price_list_id: str | None,
    centers: Sequence[str],
    warehouses: Mapping[str, Sequence[str]] | None = None,
    promos: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """The warehouses of :func:`stock_scope` read by code (the main lists' board)."""

    return stock_scope(price_list_id, centers, warehouses, promos).warehouses


def stock_label(plans: Mapping[str | None, StockPlan]) -> str:
    """Where the inventory cost comes from, in words, for the header."""

    if len(plans) == 1:
        return next(iter(plans.values())).label
    if any(plan.selected or plan.promos for plan in plans.values()):
        return "Mayor costo entre " + "; ".join(
            f"C.O. {code}: {plan.label}" for code, plan in plans.items() if code
        )
    return "Mayor costo entre los C.O. " + ", ".join(code for code in plans if code)


def regional(billing_center: str | None) -> str:
    return vocabulary.COAST if billing_center in COAST_CENTERS else vocabulary.NATIONAL


def regional_of(centers: Sequence[str], billing_center: str | None) -> str:
    """The liquor tax regional: the Coast when any chosen center is a Coast one (it pays
    more). *Nacional* (no centers) keeps the regional of the customer's billing center."""

    if not centers:
        return regional(billing_center)
    return (
        vocabulary.COAST if any(code in COAST_CENTERS for code in centers) else vocabulary.NATIONAL
    )


def prefer_stocked[K](costs: Mapping[K, Mapping[str, CostInfo]]) -> dict[K, dict[str, CostInfo]]:
    """Per SKU, drop the inventory cost of the plans without stock when another plan has
    some (2026-11-19): a warehouse at zero never sets the cost.

    The quotation cost stays: it is the supplier's, not a warehouse's. A plan whose
    warehouses all lack stock keeps their last average cost only when no plan has stock
    (``CostInfo.inventory_stocked``).
    """

    stocked = {
        sku for rows in costs.values() for sku, info in rows.items() if info.inventory_stocked
    }
    return {
        key: {
            sku: (
                replace(info, inventory_cost=None)
                if sku in stocked and info.inventory_stocked is False
                else info
            )
            for sku, info in rows.items()
        }
        for key, rows in costs.items()
    }


def costliest_center(costs: Mapping[str | None, Mapping[str, CostInfo]]) -> str | None:
    """The center whose references cost the most: the highest sum of unit costs over the
    SKUs every center has a cost for. ``None`` when nothing compares or the top ties.
    Only warehouses with stock count (:func:`prefer_stocked`).
    """

    costs = prefer_stocked(costs)
    unit = {
        code: {
            sku: value
            for sku, info in rows.items()
            if (value := effective_cost(info.quote_cost, info.inventory_cost, ONE)[0]) is not None
        }
        for code, rows in costs.items()
    }
    if len(unit) < 2:
        return None
    common = set.intersection(*(set(values) for values in unit.values()))
    if not common:
        return None
    totals = {code: sum(values[sku] for sku in common) for code, values in unit.items()}
    top = max(totals.values())
    leaders = [code for code, total in totals.items() if total == top]
    return leaders[0] if len(leaders) == 1 else None


def highest_cost(costs: Mapping[str | None, Mapping[str, CostInfo]]) -> dict[str, CostInfo]:
    """Per SKU, the costs of the center whose cost is highest, marked with that center.

    The cost compared is the one the rule uses (:func:`effective_cost`; the list factor is
    the same for every center, so it does not change the order). A tie goes to the first
    center in ``costs``. A SKU without a cost anywhere keeps the first center's row,
    unmarked: no center set its cost. A center without stock of the SKU does not compete
    with its inventory cost when another has stock (:func:`prefer_stocked`).
    """

    costs = prefer_stocked(costs)
    result: dict[str, CostInfo] = {}
    best: dict[str, Decimal] = {}
    for code, rows in costs.items():
        for sku, info in rows.items():
            value, _ = effective_cost(info.quote_cost, info.inventory_cost, ONE)
            if sku not in result:
                result[sku] = replace(info, center_code=code if value is not None else None)
                if value is not None:
                    best[sku] = value
            elif value is not None and (sku not in best or value > best[sku]):
                result[sku] = replace(info, center_code=code)
                best[sku] = value
    return result


# On a tie the quotation wins (what the supplier charges today), then the inventory: a
# pending increase is taken only when it is strictly higher.
_SOURCE_PRIORITY = {
    vocabulary.QUOTE_COST: 2,
    vocabulary.INVENTORY_COST: 1,
    vocabulary.INCREASE_COST: 0,
}


def effective_cost(
    quote: Decimal | None,
    inventory: Decimal | None,
    factor: Decimal,
    increase: Decimal | None = None,
) -> tuple[Decimal | None, str | None]:
    """The highest of quotation, inventory and a pending price increase, rounded up to
    pesos, times the factor.

    ``increase`` is the new cost of the product's latest price increase when SIESA does not
    have it yet (no system update date in the product master's history).

    Returns:
        tuple[Decimal | None, str | None]: The cost (4 decimals) and which source won.
    """

    usable = {
        source: value
        for source, value in (
            (vocabulary.QUOTE_COST, quote),
            (vocabulary.INVENTORY_COST, inventory),
            (vocabulary.INCREASE_COST, increase),
        )
        if value is not None and value > MIN_COST
    }
    if not usable:
        return None, None
    source = max(usable, key=lambda key: (usable[key], _SOURCE_PRIORITY[key]))
    cost = usable[source].to_integral_value(rounding=ROUND_CEILING) * factor
    return cost.quantize(Decimal("0.0001")), source


def round_price(value: Decimal, family_name: str) -> Decimal:
    """A price in whole pesos, rounded as the family's lists are."""

    return value.quantize(PESO, rounding=PRICE_ROUNDING.get(family_name, ROUND_HALF_UP))


__all__ = [
    "COST_FACTOR",
    "FAMILY_LABELS",
    "MAIN_WAREHOUSES",
    "NATIONAL_SOURCE",
    "PROMO_WAREHOUSES",
    "SITE_CLASSES",
    "SPOT_WAREHOUSES",
    "WAREHOUSES_SOURCE",
    "WAREHOUSE_CLASSES",
    "StockPlan",
    "StockScope",
    "stock_scope",
    "centers_set_cost",
    "chosen_warehouses",
    "costliest_center",
    "effective_cost",
    "family",
    "highest_cost",
    "prefer_stocked",
    "regional",
    "regional_of",
    "round_price",
    "stock_label",
    "stock_plan",
    "stock_plans",
    "stock_warehouses",
    "warehouse_plan",
]
