"""Warehouse rows become local master rows; the sync never wipes a master on an empty read."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.application import mapping
from app.modules.commercial_masters.application.sync import MasterSyncService, _primary_nits
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.commercial_masters.domain.margins import LIQUOR_TAX_FIELDS, MARGIN_FIELDS
from app.modules.commercial_masters.infrastructure.models import ProductModel
from app.modules.commercial_masters.infrastructure.repository import _SYNC_TABLES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("800116299", "800116299"),
        (800116299, "800116299"),
        (800116299.0, "800116299"),
        ("800.116.299", "800116299"),
        ("901505610\xa0", "901505610"),  # NBSP seen in the workbook
        ("  35518947 ", "35518947"),
        ("TODOS", None),
        ("", None),
        (None, None),
    ],
)
def test_clean_nit(raw, expected) -> None:
    assert clean_nit(raw) == expected


def test_supplier_row_uses_both_states_for_is_active() -> None:
    row = {
        "proveedor_key": "k1",
        "cia": 1,
        "nit": "890900099",
        "razon_social": "  INDUSTRIAS   ESTRA S.A. ",
        "id_sucursal": "001",
        "estado": "A",
        "estado_sucursal": "I",
        "fecha_ingreso": date(2020, 1, 1),
    }

    mapped = mapping.supplier_row(row)

    assert mapped["legal_name"] == "INDUSTRIAS ESTRA S.A."
    assert mapped["is_active"] is False  # tercero active but branch inactive
    assert mapped["entry_date"] == date(2020, 1, 1)
    assert "cuenta_bancaria" not in mapped


def test_rows_without_a_key_or_a_nit_are_skipped() -> None:
    assert mapping.supplier_row({"proveedor_key": "k", "nit": None}) is None
    assert mapping.customer_row({"cliente_key": None, "nit": "1"}) is None
    assert mapping.product_row({"producto_key": "p", "sku": " "}) is None


def test_the_unknown_client_placeholder_is_not_imported() -> None:
    placeholder = {"cliente_key": "x", "cia": -1, "nit": "0", "razon_social": "(desconocido)"}
    real = {"cliente_key": "y", "cia": 1, "nit": "800116299", "estado": "A", "estado_sucursal": "A"}

    assert mapping.customer_row(placeholder) is None
    assert mapping.customer_row(real)["is_active"] is True


def test_product_row_maps_the_catalog_columns() -> None:
    mapped = mapping.product_row(
        {
            "producto_key": "p1",
            "sku": "8749",
            "descripcion": "Arroz Diana x 1Kg",
            "estado": "A",
            "cod_proveedor": "546",
            "marca": "DIANA",
            "factor_empaque": "12",
            "iva_venta_pct": "0.0500",
        }
    )

    assert mapped["sku"] == "8749" and mapped["commercial_supplier_code"] == "546"
    assert mapped["brand"] == "DIANA" and str(mapped["pack_factor"]) == "12"
    assert mapped["is_active"] is True


def test_the_product_sync_never_carries_margins_or_liquor_taxes() -> None:
    """The margin master changes only by hand or by file.

    The upsert writes only the columns a row carries, and a product that leaves the DWH is
    deactivated, never deleted, so its margins survive every sync.
    """

    mapped = mapping.product_row(
        {"producto_key": "p1", "sku": "8749", "estado": "A", "margen": 0.5, "imptolic": 1}
    )
    owned_by_hand = {
        *MARGIN_FIELDS,
        *LIQUOR_TAX_FIELDS,
        "margin_notes",
        "margin_source",
        "margin_updated_by",
        "margin_updated_at",
    }

    assert owned_by_hand <= {column.name for column in ProductModel.__table__.columns}
    assert not owned_by_hand & mapped.keys()
    assert _SYNC_TABLES["products"] == (ProductModel, "update")


def test_commercial_supplier_is_active_while_it_has_active_products() -> None:
    active = mapping.commercial_supplier_row(
        {
            "cod_proveedor": "233",
            "proveedor": "FAMILIA SANCELA",
            "productos": 675,
            "productos_activos": 241,
        }
    )
    dormant = mapping.commercial_supplier_row(
        {
            "cod_proveedor": "585",
            "proveedor": "TEAM PANADERIA",
            "productos": 106,
            "productos_activos": 0,
        }
    )

    assert active["is_active"] is True and active["product_count"] == 675
    assert dormant["is_active"] is False


def test_primary_nit_is_the_one_with_most_linked_products() -> None:
    rows = [
        {"cod_proveedor": "323", "nit": "901093590", "productos": 324},
        {"cod_proveedor": "323", "nit": "830100530", "productos": 137},
        {"cod_proveedor": "295", "nit": "890900099", "productos": 1279},
        {"cod_proveedor": "295", "nit": "800.1", "productos": 10},
        {"cod_proveedor": "999", "nit": None, "productos": 5},
    ]

    primary = {(row["code"], row["nit"]): row["is_primary"] for row in _primary_nits(rows)}

    assert primary == {
        ("323", "901093590"): True,
        ("323", "830100530"): False,
        ("295", "890900099"): True,
        ("295", "8001"): False,
    }


class FakeSource:
    def __init__(self, **pages) -> None:
        self.pages = pages

    async def _iter(self, name):
        for page in self.pages.get(name, []):
            yield page

    def suppliers(self, batch_size):
        return self._iter("suppliers")

    def customers(self, batch_size):
        return self._iter("customers")

    def products(self, batch_size):
        return self._iter("products")

    def product_suppliers(self, batch_size):
        return self._iter("product_suppliers")

    async def unit_costs(self):
        return self.pages.get("unit_costs", [])

    async def commercial_suppliers(self):
        return self.pages.get("commercial", [])

    async def commercial_supplier_nits(self, per_supplier):
        return self.pages.get("nits", [])


class FakeSyncRepository:
    def __init__(self) -> None:
        self.rows: dict[str, list] = {}
        self.deactivated: list[str] = []
        self.touched: list[str] = []

    async def _save(self, name: str, rows: Sequence) -> int:
        self.rows.setdefault(name, []).extend(rows)
        return len(rows)

    async def upsert_suppliers(self, rows, synced_at):
        return await self._save("suppliers", rows)

    async def upsert_customers(self, rows, synced_at):
        return await self._save("customers", rows)

    async def upsert_products(self, rows, synced_at):
        return await self._save("products", rows)

    async def upsert_product_suppliers(self, rows, synced_at):
        return await self._save("product_suppliers", rows)

    async def upsert_commercial_suppliers(self, rows, synced_at):
        return await self._save("commercial_suppliers", rows)

    async def update_unit_costs(self, rows):
        return await self._save("unit_costs", rows)

    async def replace_commercial_supplier_nits(self, rows):
        return await self._save("nits", rows)

    async def deactivate_unseen(self, table, synced_at):
        self.deactivated.append(table)
        return 3

    async def touch_master_definition(self, slug, synced_at):
        self.touched.append(slug)


def customers_page(count: int, start: int = 0) -> list[dict]:
    return [
        {"cliente_key": f"c{start + i}", "cia": 1, "nit": str(800000000 + start + i)}
        for i in range(count)
    ]


async def test_dry_run_reads_and_counts_but_writes_nothing() -> None:
    source = FakeSource(customers=[customers_page(3), customers_page(2, 3)])
    repository = FakeSyncRepository()

    (report,) = await MasterSyncService(source, repository).run(["customers"], apply=False)

    assert (report.read, report.upserted, report.deactivated) == (5, 5, 0)
    assert repository.rows == {} and repository.deactivated == []


async def test_apply_upserts_every_page_then_deactivates_unseen_rows() -> None:
    source = FakeSource(
        customers=[customers_page(3), [{"cliente_key": "placeholder", "cia": -1, "nit": "0"}]]
    )
    repository = FakeSyncRepository()

    (report,) = await MasterSyncService(source, repository).run(["customers"], apply=True)

    assert report.read == 4 and report.upserted == 3  # the placeholder is skipped
    assert len(repository.rows["customers"]) == 3
    assert repository.deactivated == ["customers"] and repository.touched == ["clientes"]
    assert report.deactivated == 3


async def test_an_empty_warehouse_answer_never_deactivates_the_local_master() -> None:
    repository = FakeSyncRepository()

    (report,) = await MasterSyncService(FakeSource(), repository).run(["products"], apply=True)

    assert report.read == 0
    assert repository.deactivated == []


async def test_masters_run_in_dependency_order_and_commercial_suppliers_load_nits() -> None:
    source = FakeSource(
        commercial=[
            {"cod_proveedor": "233", "proveedor": "FAMILIA", "productos": 2, "productos_activos": 1}
        ],
        nits=[{"cod_proveedor": "233", "nit": "890900161", "productos": 607}],
        products=[[{"producto_key": "p", "sku": "1128", "estado": "A"}]],
    )
    repository = FakeSyncRepository()

    reports = await MasterSyncService(source, repository).run(apply=True)

    assert [report.master for report in reports] == [
        "commercial_suppliers",
        "suppliers",
        "customers",
        "products",
        "unit_costs",
        "product_suppliers",
    ]
    assert reports[0].extra == {"nits": 1}
    assert repository.rows["nits"][0]["is_primary"] is True


async def test_unknown_master_names_are_rejected() -> None:
    with pytest.raises(InvalidInputError, match="desconocidos"):
        await MasterSyncService(FakeSource(), FakeSyncRepository()).run(["nope"])


def test_unit_cost_rows_keep_only_positive_costs() -> None:
    good = mapping.unit_cost_row(
        {"sku": "3474", "avg_unit_cost": "13782.70123", "cut_date": date(2026, 10, 9)}
    )

    assert good == {
        "sku": "3474",
        "avg_unit_cost": Decimal("13782.7012"),
        "avg_unit_cost_date": date(2026, 10, 9),
    }
    assert mapping.unit_cost_row({"sku": "1", "avg_unit_cost": 0}) is None
    assert mapping.unit_cost_row({"sku": " ", "avg_unit_cost": 5}) is None
    assert mapping.unit_cost_row({"sku": "1", "avg_unit_cost": None}) is None


async def test_unit_costs_are_loaded_and_never_deactivate_anything() -> None:
    source = FakeSource(
        unit_costs=[{"sku": "3474", "avg_unit_cost": 13782.7, "cut_date": date(2026, 10, 9)}]
    )
    repository = FakeSyncRepository()

    (report,) = await MasterSyncService(source, repository).run(["unit_costs"], apply=True)

    assert (report.read, report.upserted, report.deactivated) == (1, 1, 0)
    assert repository.rows["unit_costs"][0]["sku"] == "3474" and repository.deactivated == []
