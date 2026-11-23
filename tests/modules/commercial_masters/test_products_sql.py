"""The SQL of the product master reads compiles for PostgreSQL and says what it should (no
database: the statements are captured and canned rows come back)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.commercial_masters.domain.entities import ProductSupplierOption
from app.modules.commercial_masters.infrastructure.models import ProductModel
from app.modules.commercial_masters.infrastructure.repository import (
    SqlAlchemyCommercialMasterRepository,
)


class _Result:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def scalar_one(self):
        return len(self.rows)

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return _Result([row[0] for row in self.rows])


class _Session:
    def __init__(self, database: _Database) -> None:
        self.database = database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def execute(self, statement, params=None):
        self.database.statements.append(statement)
        return _Result(self.database.answers.pop(0) if self.database.answers else [])


class _Database:
    def __init__(self, *answers: list) -> None:
        self.statements: list = []
        self.answers = list(answers)

    def session_factory(self):
        return _Session(self)


def _sql(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _model(sku: str, **values) -> ProductModel:
    base = {
        "id": uuid4(),
        "producto_key": sku,
        "sku": sku,
        "is_active": True,
        "commercial_supplier_code": "233",
        "sales_vat_pct": Decimal("19.0000"),
    }
    return ProductModel(**{**base, **values})


async def test_the_consumption_tax_filter_reads_nacional_or_costa_above_zero() -> None:
    database = _Database()
    repo = SqlAlchemyCommercialMasterRepository(database)

    await repo.list_products(None, None, None, None, None, 1, 50, has_consumption_tax=True)
    await repo.list_products(None, None, None, None, None, 1, 50, has_consumption_tax=False)
    await repo.list_products(None, None, None, None, None, 1, 50)

    count_true, page_true, count_false, page_false, count_none, page_none = map(
        _sql, database.statements
    )
    pays = "coalesce(products.liquor_tax, 0) > 0 OR coalesce(products.liquor_tax_coast, 0) > 0"
    assert pays in count_true and pays in page_true
    assert f"NOT ({pays})" in count_false and f"NOT ({pays})" in page_false
    assert "liquor_tax" not in count_none and "coalesce" not in page_none


async def test_listed_products_carry_the_supplier_name_and_vat() -> None:
    database = _Database(
        [object()],
        [(_model("7244"), "FAMILIA SANCELA"), (_model("1569", sales_vat_pct=None), None)],
    )
    repo = SqlAlchemyCommercialMasterRepository(database)

    page = await repo.list_products(None, None, "ASEO", "233", True, 1, 50)

    query = _sql(database.statements[1])
    assert (
        "LEFT OUTER JOIN commercial_suppliers ON commercial_suppliers.code = "
        "products.commercial_supplier_code" in query
    )
    assert "products.category = 'ASEO'" in query
    assert "products.commercial_supplier_code = '233'" in query
    first, second = page.items
    assert first.commercial_supplier_name == "FAMILIA SANCELA"
    assert first.sales_vat_pct == Decimal("19")
    assert second.commercial_supplier_name is None and second.sales_vat_pct is None
    assert page.total == 1


async def test_one_product_carries_its_supplier_name() -> None:
    database = _Database([(_model("7244"), "FAMILIA SANCELA")], [])
    repo = SqlAlchemyCommercialMasterRepository(database)

    product = await repo.get_product("7244")

    assert product is not None and product.commercial_supplier_name == "FAMILIA SANCELA"
    assert "LEFT OUTER JOIN commercial_suppliers" in _sql(database.statements[0])
    assert await repo.get_product("0") is None


async def test_product_options_are_distinct_suppliers_by_name_and_sorted_categories() -> None:
    database = _Database(
        [("107", "COLOMBINA"), ("999", None), ("233", "FAMILIA SANCELA"), ("108", "colombina")],
        [("ALIMENTOS",), ("ASEO",)],
    )
    repo = SqlAlchemyCommercialMasterRepository(database)

    options = await repo.product_options()

    suppliers, categories = map(_sql, database.statements)
    assert suppliers.startswith("SELECT DISTINCT products.commercial_supplier_code")
    assert "LEFT OUTER JOIN commercial_suppliers" in suppliers
    assert "products.commercial_supplier_code IS NOT NULL" in suppliers
    assert "trim(products.commercial_supplier_code) != ''" in suppliers
    assert categories.startswith("SELECT DISTINCT products.category")
    assert "ORDER BY products.category" in categories
    # By name (case-insensitive), then code; a code with no name in the master goes last.
    assert options.suppliers == [
        ProductSupplierOption("107", "COLOMBINA"),
        ProductSupplierOption("108", "colombina"),
        ProductSupplierOption("233", "FAMILIA SANCELA"),
        ProductSupplierOption("999", None),
    ]
    assert options.categories == ["ALIMENTOS", "ASEO"]


def test_the_salsamentarias_margin_column_is_gone() -> None:
    assert "margin_salsamentarias" not in ProductModel.__table__.columns
