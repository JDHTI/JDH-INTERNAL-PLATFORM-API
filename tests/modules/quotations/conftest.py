"""In-memory ports for the quoter."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.errors import ConflictError, DependencyUnavailableError, NotFoundError
from app.modules.quotations.application.service import QuotationService
from app.modules.quotations.domain.entities import (
    Branch,
    CostInfo,
    CustomerOption,
    CustomerPage,
    ItemInfo,
    LastSale,
    ListPrice,
    ProductMaster,
    QuotationDetail,
    QuotationPage,
    QuotationPolicy,
    Seller,
    ValueTaxKey,
)
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter
from app.modules.quotations.infrastructure.template import QuotationTemplateFile
from app.modules.quotations.infrastructure.workbook import PriceListWorkbookWriter
from tests.modules.order_validation.conftest import FakeAgreementSource
from tests.modules.supplier_supports.conftest import FakeManagers, FixedCalendar, person

MANAGER = "asesor@jdh.com.co"
APPROVER = "bi@jdh.com.co"
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
        self.prices: dict[str, ListPrice] = {"2537": ListPrice("2537", Decimal("4568"))}
        self.taxes: dict[str, ValueTaxKey] = {}
        self.seller_rows: dict[str, list[Seller]] = {
            MANAGER: [Seller("0051", "PEREZ ANA", MANAGER)]
        }
        self.item_rows: dict[str, ItemInfo] = {
            "26278": ItemInfo("26278", "Ginebra Selva x700ml", "UNID", Decimal(5), "359"),
            "9999": ItemInfo("9999", "Inactiva", "UNID", Decimal(19), "359", None, False),
        }
        self.list_customers = 28113
        self.down = False
        self.calls: list[tuple] = []

    def _check(self) -> None:
        if self.down:
            raise DependencyUnavailableError("No se pudo consultar el datawarehouse")

    async def branches(self, nit):
        self._check()
        return list(self.branch_rows.get(nit, []))

    async def price_list_customers(self, price_list_id):
        self._check()
        return self.list_customers

    async def last_sales(self, nit, months):
        self._check()
        self.calls.append(("last_sales", nit, months))
        return list(self.sales.get(nit, []))

    async def costs(self, skus, plan):
        self._check()
        self.calls.append(("costs", tuple(skus), plan))
        return {sku: self.cost_rows[sku] for sku in skus if sku in self.cost_rows}

    async def price_list_exists(self, price_list_id):
        self._check()
        return price_list_id in self.existing_lists

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


class FakeMasters:
    def __init__(self) -> None:
        self.catalog: dict[str, ProductMaster] = {"2537": product()}
        self.suppliers = {"359": "LOCERIA COLOMBIANA S.A."}
        self.options = [
            CustomerOption(
                NIT, "HOTEL EL BOSQUE SAS", "0142", "HOTELES Y CLUBES", "0051", "PEREZ ANA", 2, 1
            ),
            CustomerOption("800125313", "SUMMAR", "0151", "EMPRESAS", "0099", "OTRO", 1, 1),
        ]

    async def products(self, skus):
        return {sku: self.catalog[sku] for sku in skus if sku in self.catalog}

    async def supplier_names(self, codes):
        return {code: self.suppliers[code] for code in codes if code in self.suppliers}

    async def customers(self, seller_codes, q, page, size):
        items = [
            item
            for item in self.options
            if (seller_codes is None or item.salesperson_code in seller_codes)
            and (not q or q.lower() in (item.name or "").lower() or item.nit.startswith(q))
        ]
        return CustomerPage(tuple(items[(page - 1) * size : page * size]), len(items), page, size)


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, QuotationDetail] = {}

    async def create(self, quotation, lines):
        detail = QuotationDetail(quotation, tuple(sorted(lines, key=lambda item: item.position)))
        self.items[quotation.id] = detail
        return detail

    async def get(self, quotation_id):
        return self.items.get(quotation_id)

    async def list(self, *, created_by_id, status, customer_nit, q, page, size, visible_to=None):
        items = [
            detail.quotation
            for detail in self.items.values()
            if (
                visible_to is None
                or detail.quotation.created_by_id == visible_to[0]
                or detail.quotation.salesperson_code in visible_to[1]
            )
            and (created_by_id is None or detail.quotation.created_by_id == created_by_id)
            and (status is None or detail.quotation.status == status)
            and (customer_nit is None or detail.quotation.customer_nit == customer_nit)
        ]
        return QuotationPage(tuple(items[(page - 1) * size : page * size]), len(items), page, size)

    async def save(
        self, quotation, lines, expected_version, replace_lines=False, inserted=(), removed=()
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

    async def delete(self, quotation_id):
        return self.items.pop(quotation_id, None) is not None


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
        template=QuotationTemplateFile(),
        calendar=FixedCalendar(),
        workbook=PriceListWorkbookWriter(),
        flat_file=SiesaPriceFileWriter(),
        policy=QuotationPolicy(Decimal("0.01")),
        max_lines=100,
    )


@pytest.fixture
def advisor():
    return person(MANAGER)


@pytest.fixture
def approver():
    return person(APPROVER)


__all__ = ["APPROVER", "MANAGER", "NIT", "branch", "cost", "product", "replace", "sale"]
