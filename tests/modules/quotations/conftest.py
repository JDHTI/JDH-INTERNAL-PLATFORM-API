"""In-memory ports for the quoter."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.errors import ConflictError, DependencyUnavailableError, NotFoundError
from app.modules.quotations.application.list_review import ListReviewService
from app.modules.quotations.application.service import QuotationService
from app.modules.quotations.domain.entities import (
    BillingCenter,
    Branch,
    CenterWarehouse,
    CostInfo,
    CustomerBranchOption,
    CustomerOption,
    CustomerPage,
    GroupInfo,
    GroupPage,
    ItemInfo,
    LastSale,
    ListItem,
    ListPrice,
    MonthlySale,
    PriceListInfo,
    PriceListLoad,
    PriceVersion,
    ProductMaster,
    QuotationDetail,
    QuotationPage,
    QuotationPolicy,
    Seller,
    SellerContact,
    StagedItem,
    StockRow,
    ValueTaxKey,
)
from app.modules.quotations.infrastructure.lines_workbook import QuotationLinesWorkbook
from app.modules.quotations.infrastructure.list_template_file import ListTemplateFile
from app.modules.quotations.infrastructure.offer_workbook import CustomerOfferWorkbook
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter
from app.modules.quotations.infrastructure.workbook import PriceListWorkbookWriter
from tests.modules.order_validation.conftest import FakeAgreementSource
from tests.modules.supplier_supports.conftest import FakeManagers, FixedCalendar, person

MANAGER = "asesor@jdh.com.co"
APPROVER = "bi@jdh.com.co"
# Manages list updates only (no quoter).
LIST_MANAGER = "listas@jdh.com.co"
# Edits the main lists (BI collaborator) and validates them (BI analyst).
MAIN_MANAGER = "colaborador.bi@jdh.com.co"
MAIN_APPROVER = "analista.bi@jdh.com.co"
NIT = "900315138"


def branch(**values) -> Branch:
    base = {
        "branch_id": "001",
        "legal_name": "HOTEL EL BOSQUE SAS",
        "channel_code": "0142",
        "channel": "HOTELES Y CLUBES",
        "price_list_id": "001",
        "price_list_name": "GENERAL ANTIOQUIA",
        "billing_center_code": "001",
        "salesperson_code": "0051",
        "salesperson_name": "PEREZ ANA",
        "is_active": True,
    }
    return Branch(**{**base, **values})


def sale(sku: str = "2537", price: str = "4000", **values) -> LastSale:
    base = {
        "sku": sku,
        "sale_date": date(2026, 10, 20),
        "document": "FEV-1390174",
        "sale_unit": "UNID",
        "quantity": Decimal(3),
        "gross_price": Decimal(price),
        "net_price": Decimal(price),
        "discount_pct": Decimal(0),
        "sales_in_window": 4,
        "description": "Abrasivo en polvo",
        "unit": "UNID",
        "vat_pct": Decimal(19),
        "supplier_code": "359",
        "category": "ASEO",
    }
    return LastSale(**{**base, **values})


def cost(sku: str = "2537", inventory: str | None = None, quote: str | None = None, **values):
    """Costs as the warehouse gives them: inventory of the list warehouse and quotation."""

    return CostInfo(
        sku,
        quote_cost=Decimal(quote) if quote else None,
        inventory_cost=Decimal(inventory) if inventory else None,
        **values,
    )


def product(sku: str = "2537", **values) -> ProductMaster:
    base = {
        "sku": sku,
        "description": "Abrasivo en Polvo Pino 1-A x700g",
        "unit": "UNID",
        "vat_pct": Decimal(19),
        "pack_factor": Decimal(18),
        "supplier_code": "359",
        "margins": {"margin_list": Decimal("0.20"), "margin_wholesale": Decimal("0.12")},
    }
    return ProductMaster(**{**base, **values})


class FakeWarehouse:
    """Branches, sales, costs, list prices, taxes and sellers in memory."""

    def __init__(self) -> None:
        self.branch_rows: dict[str, list[Branch]] = {NIT: [branch()]}
        self.sales: dict[str, list[LastSale]] = {NIT: [sale()]}
        self.cost_rows: dict[str, CostInfo] = {
            "2537": cost("2537", inventory="3000", snapshot_date=date(2026, 11, 3))
        }
        self.existing_lists: set[str] = {"001", "1", "C01"}
        # Stock by warehouse (the morning's photo): what the lines and the breakdown read.
        self.stock_rows: list[StockRow] = []
        self.prices: dict[str, ListPrice] = {"2537": ListPrice("2537", Decimal("4568"))}
        self.taxes: dict[str, ValueTaxKey] = {}
        self.seller_rows: dict[str, list[Seller]] = {
            MANAGER: [Seller("0051", "PEREZ ANA", MANAGER)]
        }
        self.item_rows: dict[str, ItemInfo] = {
            "26278": ItemInfo(
                "26278", "Ginebra Selva x700ml", "UNID", Decimal(5), "359", category="LICORES"
            ),
            "9999": ItemInfo("9999", "Inactiva", "UNID", Decimal(19), "359", None, False),
        }
        self.contacts: dict[str, SellerContact] = {
            "0051": SellerContact("0051", "PEREZ ANA", MANAGER, "3001234567")
        }
        self.centers: list[BillingCenter] = [
            BillingCenter("004", "COSTA", regional_code="04", regional_name="COSTA"),
            BillingCenter("001", "ANTIOQUIA", regional_code="01", regional_name="ANTIOQUIA"),
            BillingCenter("003", "CENTRO", regional_code="03", regional_name="CENTRO"),
        ]
        # The main (BP) and spot-request (BSP) warehouses of three centers.
        self.center_warehouse_rows: list[CenterWarehouse] = [
            CenterWarehouse("MDE1", "PRINCIPAL MEDELLIN", "001", "BP"),
            CenterWarehouse("MDE3", "CAVA MEDELLIN", "001", "BP"),
            CenterWarehouse("MDE4", "SOLICITUDES PUNTUALES", "001", "BSP"),
            CenterWarehouse("BTA8", "CAVA BOGOTA", "003", "BP"),
            CenterWarehouse("BTA9", "PRINCIPAL BOGOTA", "003", "BP"),
            CenterWarehouse("BTA20", "SOLICITUDES PUNTUALES", "003", "BSP"),
            CenterWarehouse("CTG1", "PRINCIPAL CARTAGENA", "004", "BP"),
            CenterWarehouse("CTG15", "CAVA CARTAGENA", "004", "BP"),
            CenterWarehouse("CTG14", "SOLICITUDES PUNTUALES", "004", "BSP"),
            CenterWarehouse("MDE27", "PROMOCIONES MEDELLIN FOOD", "001", "PROMO"),
            CenterWarehouse("MDE28", "PROMOCIONES MEDELLIN CAVA", "001", "PROMO"),
        ]
        self.warehouses_down = False
        self.list_customers = 28113
        self.down = False
        self.calls: list[tuple] = []
        # Costs of one plan, by its main warehouse (``MDE1``), its chosen warehouses joined
        # by commas (``MDE1,MDE4``) or its source (``nacional``), plus ``+`` and its
        # promotional warehouses (``MDE1+MDE27``); any other plan gets ``cost_rows``.
        self.plan_costs: dict[str, dict[str, CostInfo]] = {}
        # List updates: list 10U with two SKUs, served to two customers of Centro.
        self.lists: dict[str, PriceListInfo] = {
            "10U": PriceListInfo("10U", "MAYORISTAS CENTRO", True, 2),
            "001": PriceListInfo("001", "GENERAL ANTIOQUIA", True, 1),
        }
        self.list_branch_rows: dict[str, list[Branch]] = {
            "10U": [
                branch(
                    price_list_id="10U",
                    price_list_name="MAYORISTAS CENTRO",
                    channel_code="0150",
                    channel="MAYORISTAS Y DISTRIBUIDORES",
                    billing_center_code="003",
                    nit=NIT,
                    customer_key="1-900315138-001",
                ),
                branch(
                    branch_id="001",
                    legal_name="DISTRIBUIDORA CARPO",
                    price_list_id="10U",
                    price_list_name="MAYORISTAS CENTRO",
                    channel_code="0150",
                    channel="MAYORISTAS Y DISTRIBUIDORES",
                    billing_center_code="003",
                    nit="800111222",
                    customer_key="1-800111222-001",
                ),
            ],
            "001": [branch(nit=NIT, customer_key="1-900315138-001")],
        }
        self.list_item_rows: dict[str, list[ListItem]] = {
            "10U": [
                ListItem(ItemInfo("2537", "Abrasivo", "UNID", Decimal(19), "359"), Decimal("4568")),
                ListItem(
                    ItemInfo("26278", "Ginebra Selva x700ml", "UNID", Decimal(5), "359"),
                    None,
                    placeholder=True,
                ),
            ],
            "001": [
                ListItem(ItemInfo("2537", "Abrasivo", "UNID", Decimal(19), "359"), Decimal("4568"))
            ],
        }
        self.list_sales: dict[str, LastSale] = {
            "2537": sale("2537", "4400", unit_cost=Decimal("2950"), sales_in_window=0)
        }
        self.sales_down = False

    def _check(self) -> None:
        if self.down:
            raise DependencyUnavailableError("No se pudo consultar el datawarehouse")

    async def branches(self, nit):
        self._check()
        return list(self.branch_rows.get(nit, []))

    async def price_list_customers(self, price_list_id):
        self._check()
        return self.list_customers

    async def last_sales(self, nit, months, branch_id=None):
        self._check()
        if branch_id is None:
            self.calls.append(("last_sales", nit, months))
            return list(self.sales.get(nit, []))
        self.calls.append(("last_sales", nit, months, branch_id))
        return list(self.sales.get(f"{nit}/{branch_id}", []))

    async def group_branches(self, nits):
        self._check()
        return [replace(item, nit=nit) for nit in nits for item in self.branch_rows.get(nit, [])]

    async def group_last_sales(self, nits, months):
        self._check()
        self.calls.append(("group_last_sales", tuple(nits), months))
        latest: dict[str, LastSale] = {}
        for nit in nits:
            for item in self.sales.get(nit, []):
                if item.sku not in latest or item.net_price > latest[item.sku].net_price:
                    latest[item.sku] = item
        return list(latest.values())

    async def costs(self, skus, plan):
        self._check()
        self.calls.append(("costs", tuple(skus), plan))
        if plan.selected:
            key = ",".join(plan.selected)
        else:
            key = plan.warehouses[0] if plan.warehouses else plan.source
        if plan.promos:
            key += "+" + ",".join(plan.promos)
        rows = self.plan_costs.get(key, self.cost_rows)
        return {sku: rows[sku] for sku in skus if sku in rows}

    async def price_list_exists(self, price_list_id):
        self._check()
        return price_list_id in self.existing_lists

    async def price_list_ids(self):
        self._check()
        return sorted(self.existing_lists)

    async def stock(self, skus, warehouses, centers=()):
        self._check()
        self.calls.append(("stock", tuple(warehouses), tuple(centers)))
        return [
            row
            for row in self.stock_rows
            if row.sku in skus and (row.warehouse in warehouses or row.center_code in centers)
        ]

    async def list_prices(self, price_list_id, skus):
        self._check()
        self.calls.append(("list_prices", price_list_id))
        return {sku: self.prices[sku] for sku in skus if sku in self.prices}

    async def value_tax_keys(self, skus):
        self._check()
        return {sku: self.taxes[sku] for sku in skus if sku in self.taxes}

    async def sellers(self, email):
        self._check()
        return list(self.seller_rows.get(email, []))

    async def items(self, skus):
        self._check()
        return {sku: self.item_rows[sku] for sku in skus if sku in self.item_rows}

    async def seller_contact(self, code):
        self._check()
        return self.contacts.get(code)

    async def billing_centers(self):
        self._check()
        return list(self.centers)

    async def center_warehouses(self):
        self._check()
        if self.warehouses_down:
            raise DependencyUnavailableError("No se pudo consultar el datawarehouse")
        return list(self.center_warehouse_rows)

    # ------------------------------------------------------------ list updates

    async def price_lists(self, q, limit=50):
        self._check()
        term = q.strip().upper()
        return [
            info
            for info in self.lists.values()
            if info.code.upper() == term or term in (info.name or "").upper()
        ][:limit]

    async def price_list(self, code):
        self._check()
        return next(
            (info for info in self.lists.values() if info.code.upper() == code.strip().upper()),
            None,
        )

    async def list_branches(self, price_list_id):
        self._check()
        return list(self.list_branch_rows.get(price_list_id, []))

    async def list_items(self, price_list_id):
        self._check()
        return list(self.list_item_rows.get(price_list_id, []))

    async def list_last_sales(self, skus, months, customer_keys, timeout=None):
        self._check()
        self.calls.append(("list_last_sales", tuple(skus), customer_keys))
        if self.sales_down:
            raise DependencyUnavailableError("El datawarehouse tardó más de 30 s en responder")
        return {sku: self.list_sales[sku] for sku in skus if sku in self.list_sales}


class FakeMasters:
    def __init__(self) -> None:
        self.catalog: dict[str, ProductMaster] = {"2537": product()}
        self.suppliers = {"359": "LOCERIA COLOMBIANA S.A."}
        # New cost of a price increase SIESA does not have yet, by SKU.
        self.increases: dict[str, Decimal] = {}
        self.options = [
            CustomerOption(
                NIT, "HOTEL EL BOSQUE SAS", "0142", "HOTELES Y CLUBES", "0051", "PEREZ ANA", 2, 1
            ),
            CustomerOption("800125313", "SUMMAR", "0151", "EMPRESAS", "0099", "OTRO", 1, 1),
        ]
        # Business groups: name -> NITs, and each NIT's salesperson.
        self.groups: dict[str, list[str]] = {}
        self.sellers_of: dict[str, str] = {NIT: "0051", "800125313": "0099"}
        # A customer's branches in the local master (a quotation by branch).
        self.branches: dict[str, list[CustomerBranchOption]] = {}

    async def products(self, skus):
        return {sku: self.catalog[sku] for sku in skus if sku in self.catalog}

    async def supplier_names(self, codes):
        return {code: self.suppliers[code] for code in codes if code in self.suppliers}

    async def pending_increases(self, skus):
        return {sku: self.increases[sku] for sku in skus if sku in self.increases}

    async def increase_dates(self, skus):
        dates = getattr(self, "increase_on", {})
        return {sku: dates[sku] for sku in skus if sku in dates}

    async def customers(self, seller_codes, q, page, size):
        items = [
            item
            for item in self.options
            if (seller_codes is None or item.salesperson_code in seller_codes)
            and (not q or q.lower() in (item.name or "").lower() or item.nit.startswith(q))
        ]
        return CustomerPage(tuple(items[(page - 1) * size : page * size]), len(items), page, size)

    async def business_groups(self, seller_codes, q, page, size):
        names = sorted(
            {
                group
                for group, nits in self.groups.items()
                if seller_codes is None
                or any(self.sellers_of.get(nit) in seller_codes for nit in nits)
            }
        )
        items = [
            GroupInfo(name, len(self.groups[name]), len(self.groups[name]))
            for name in names
            if not q or q.lower() in name.lower()
        ]
        return GroupPage(tuple(items[(page - 1) * size : page * size]), len(items), page, size)

    async def group_nits(self, group):
        return list(self.groups.get(group, []))

    async def customer_branches(self, nit):
        return list(self.branches.get(nit, []))


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, QuotationDetail] = {}
        self.category_writes: list[dict[UUID, str]] = []
        self.loads_made: list[PriceListLoad] = []
        # Reads of a whole quotation with every line (``get``).
        self.full_reads = 0

    async def create(self, quotation, lines):
        detail = QuotationDetail(quotation, tuple(sorted(lines, key=lambda item: item.position)))
        self.items[quotation.id] = detail
        return detail

    async def get(self, quotation_id):
        self.full_reads += 1
        return self.items.get(quotation_id)

    async def get_lines(self, quotation_id, skus):
        detail = self.items.get(quotation_id)
        if detail is None:
            return None
        wanted = set(skus)
        return QuotationDetail(
            detail.quotation, tuple(line for line in detail.lines if line.sku in wanted)
        )

    async def set_categories(self, quotation_id, categories):
        self.category_writes.append(dict(categories))
        detail = self.items[quotation_id]
        lines = tuple(
            replace(line, category=categories[line.id]) if line.id in categories else line
            for line in detail.lines
        )
        self.items[quotation_id] = QuotationDetail(detail.quotation, lines)

    async def fill_last_sales(self, quotation_id, by_sku, warning) -> bool:
        detail = self.items.get(quotation_id)
        if detail is None or warning not in detail.quotation.warnings:
            return False
        lines = tuple(
            replace(line, **by_sku[line.sku]) if line.sku in by_sku else line
            for line in detail.lines
        )
        header = replace(
            detail.quotation,
            warnings=tuple(item for item in detail.quotation.warnings if item != warning),
        )
        self.items[quotation_id] = QuotationDetail(header, lines)
        return True

    async def has_warning(self, quotation_id, warning) -> bool:
        detail = self.items.get(quotation_id)
        return detail is not None and warning in detail.quotation.warnings

    async def list(
        self,
        *,
        created_by_id,
        status,
        customer_nit,
        q,
        page,
        size,
        visible_to=None,
        kind="cotizacion",
        created_by_ids=None,
    ):
        items = [
            detail.quotation
            for detail in self.items.values()
            if detail.quotation.kind == kind
            and (
                visible_to is None
                or detail.quotation.created_by_id == visible_to[0]
                or detail.quotation.salesperson_code in visible_to[1]
            )
            and (created_by_id is None or detail.quotation.created_by_id == created_by_id)
            and (created_by_ids is None or detail.quotation.created_by_id in created_by_ids)
            and (status is None or detail.quotation.status == status)
            and (customer_nit is None or detail.quotation.customer_nit == customer_nit)
        ]
        return QuotationPage(tuple(items[(page - 1) * size : page * size]), len(items), page, size)

    async def save(
        self,
        quotation,
        lines,
        expected_version,
        replace_lines=False,
        inserted=(),
        removed=(),
    ):
        current = self.items.get(quotation.id)
        if current is None:
            raise NotFoundError("Cotización no encontrada")
        if current.quotation.version != expected_version:
            raise ConflictError("La cotización cambió")
        if replace_lines:
            stored = tuple(lines)
        else:
            by_id = {line.id: line for line in lines}
            stored = tuple(
                by_id.get(line.id, line) for line in current.lines if line.id not in removed
            )
        stored = tuple(sorted((*stored, *inserted), key=lambda line: line.position))
        self.items[quotation.id] = QuotationDetail(quotation, stored)
        return self.items[quotation.id]

    async def write(
        self,
        quotation,
        lines,
        expected_version,
        replace_lines=False,
        inserted=(),
        removed=(),
    ):
        await self.save(quotation, lines, expected_version, replace_lines, inserted, removed)

    async def delete(self, quotation_id):
        return self.items.pop(quotation_id, None) is not None

    async def delete_stale_drafts(self, created_before):
        stale = [
            key
            for key, detail in self.items.items()
            if detail.quotation.kind == "cotizacion"
            and detail.quotation.status == "borrador"
            and detail.quotation.confirmed_at is None
            and detail.quotation.created_at < created_before
        ]
        for key in stale:
            del self.items[key]
        return stale

    async def reserved_list_ids(self):
        return sorted(
            {
                detail.quotation.export_price_list_id
                for detail in self.items.values()
                if detail.quotation.list_action == "creacion"
                and detail.quotation.status in ("pendiente_carga", "cargada")
                and detail.quotation.export_price_list_id
            }
        )

    # ------------------------------------------------------------ staging

    async def staged(self, kind, price_list_id=None):
        return [
            StagedItem(
                detail.quotation.id,
                detail.quotation.version,
                detail.quotation.customer_name,
                detail.quotation.price_list_id or "",
                detail.quotation.price_list_name,
                line,
            )
            for detail in self.items.values()
            if detail.quotation.kind == kind
            and (price_list_id is None or detail.quotation.price_list_id == price_list_id)
            for line in detail.lines
            if line.is_staged
        ]

    async def staged_elsewhere(self, price_list_id, skus, quotation_id):
        return {
            line.sku: detail.quotation.id
            for detail in self.items.values()
            if detail.quotation.price_list_id == price_list_id
            and detail.quotation.id != quotation_id
            for line in detail.lines
            if line.sku in skus and line.is_staged
        }

    async def create_load(self, load, line_ids):
        wanted = set(line_ids)
        found = 0
        for quotation_id, detail in list(self.items.items()):
            lines = []
            touched = False
            for line in detail.lines:
                if line.id in wanted and line.is_staged:
                    lines.append(replace(line, load_id=load.id))
                    found += 1
                    touched = True
                else:
                    lines.append(line)
            if touched:
                quotation = replace(detail.quotation, version=detail.quotation.version + 1)
                self.items[quotation_id] = QuotationDetail(quotation, tuple(lines))
        if found != len(wanted):
            raise ConflictError("El staging cambió")
        self.loads_made.append(load)
        return load

    async def loads(self, kind, limit=30):
        return [load for load in reversed(self.loads_made) if load.kind == kind][:limit]

    async def get_load(self, load_id):
        return next((load for load in self.loads_made if load.id == load_id), None)


class FakeInsights:
    """Stock, list price history and monthly sales in memory."""

    def __init__(self) -> None:
        self.stock_rows = [
            StockRow("2537", "BTA9", "BOGOTA PRINCIPAL", "003", Decimal(120), Decimal(100)),
            StockRow("2537", "BTA8", "BOGOTA FRIO", "003", Decimal(30), Decimal(30)),
            StockRow("2537", "MDE1", "MEDELLIN PRINCIPAL", "001", Decimal(80), Decimal(80)),
        ]
        self.history = [
            PriceVersion(date(2026, 5, 26), date(2026, 9, 1), Decimal(4200)),
            PriceVersion(date(2026, 9, 1), None, Decimal(4568)),
        ]
        self.sales = [MonthlySale(date(2026, 10, 1), Decimal(4400), Decimal(30))]
        self.sales_down = False
        self.calls: list[tuple] = []

    async def stock(self, skus, warehouses, centers=()):
        self.calls.append(("stock", tuple(skus), tuple(warehouses)))
        return [
            row
            for row in self.stock_rows
            if row.sku in skus and (row.warehouse in warehouses or row.center_code in centers)
        ]

    async def price_history(self, price_list_id, sku, months):
        self.calls.append(("history", price_list_id, sku, months))
        return list(self.history)

    async def monthly_sales(self, sku, months, customer_keys):
        self.calls.append(("sales", sku, months, customer_keys))
        if self.sales_down:
            raise DependencyUnavailableError("El datawarehouse tardó más de 15 s en responder")
        return list(self.sales)


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def masters() -> FakeMasters:
    return FakeMasters()


@pytest.fixture
def supports() -> FakeAgreementSource:
    return FakeAgreementSource()


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def service(repository, warehouse, masters, supports) -> QuotationService:
    return QuotationService(
        repository=repository,
        warehouse=warehouse,
        masters=masters,
        supports=supports,
        managers=FakeManagers([MANAGER]),
        approvers=FakeManagers([APPROVER]),
        calendar=FixedCalendar(),
        workbook=PriceListWorkbookWriter(),
        flat_file=SiesaPriceFileWriter(),
        policy=QuotationPolicy(Decimal("0.01")),
        max_lines=100,
        offer_writer=CustomerOfferWorkbook(),
        lines_writer=QuotationLinesWorkbook(),
        template_reader=ListTemplateFile(),
        list_managers=FakeManagers([MANAGER, LIST_MANAGER]),
        list_approvers=FakeManagers([APPROVER]),
        main_lists=("1", "001", "002", "003", "004", "005", "009"),
        main_managers=FakeManagers([MAIN_MANAGER]),
        main_approvers=FakeManagers([MAIN_APPROVER]),
    )


@pytest.fixture
def insights() -> FakeInsights:
    return FakeInsights()


@pytest.fixture
def review(service, repository, insights) -> ListReviewService:
    return ListReviewService(
        quotations=service,
        repository=repository,
        insights=insights,
        calendar=FixedCalendar(),
        workbook=PriceListWorkbookWriter(),
        flat_file=SiesaPriceFileWriter(),
    )


@pytest.fixture
def advisor():
    return person(MANAGER)


@pytest.fixture
def approver():
    return person(APPROVER)


__all__ = [
    "APPROVER",
    "LIST_MANAGER",
    "MAIN_APPROVER",
    "MAIN_MANAGER",
    "MANAGER",
    "NIT",
    "branch",
    "cost",
    "product",
    "replace",
    "sale",
]
