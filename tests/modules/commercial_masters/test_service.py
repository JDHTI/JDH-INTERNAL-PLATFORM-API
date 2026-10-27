"""Read validation and who may curate suppliers and products."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.application.service import CommercialMasterService
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    Page,
    Product,
    ProductMargins,
)
from app.modules.commercial_masters.domain.margins import MarginRow
from app.modules.commercial_masters.infrastructure.margins_file import SpreadsheetMarginReader
from app.modules.identity.domain.principal import Principal

D = Decimal


def principal(admin: bool = False) -> Principal:
    return Principal(uuid4(), "ana@jdh.com.co", "Ana", admin)


def product(sku: str = "7244", **values) -> Product:
    base = {
        "id": uuid4(),
        "sku": sku,
        "supplier_sku": None,
        "description": "Barquillo Jir Choc 24ux12.3g",
        "state": "Activo",
        "is_active": True,
        "commercial_supplier_code": "107",
        "manufacturer": None,
        "brand": "COLOMBINA",
        "category": "ALIMENTOS",
        "subcategory": None,
        "product_line": None,
        "pack_factor": D("12"),
        "inventory_unit": "UNID",
        "main_barcode": None,
    }
    return Product(**{**base, **values})


class Managers:
    async def is_manager(self, who: Principal) -> bool:
        return who.is_platform_admin


class Repo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.supplier = CommercialSupplier(uuid4(), "233", "FAMILIA SANCELA", True, 675, 241, None)
        self.products = {"7244": product("7244"), "1569": product("1569")}

    async def list_customers(self, *args):
        self.calls.append(("customers", *args))
        return Page([], 0, args[6], args[7])

    async def list_commercial_suppliers(self, query, is_active, page, size):
        self.calls.append(("suppliers", query, is_active, page, size))
        return Page([self.supplier], 1, page, size)

    async def list_legal_suppliers(self, query, nit, is_active, page, size):
        self.calls.append(("legal", query, nit, is_active, page, size))
        return Page([], 0, page, size)

    async def list_products(
        self, query, brand, category, supplier, is_active, page, size, has_margin=None
    ):
        self.calls.append(
            ("products", query, brand, category, supplier, is_active, page, size, has_margin)
        )
        return Page(list(self.products.values()), len(self.products), page, size)

    async def get_commercial_supplier(self, code):
        return self.supplier if code == "233" else None

    async def update_commercial_supplier(self, code, fields, nits, primary):
        self.calls.append(("update", code, dict(fields), nits, primary))
        return self.supplier if code == "233" else None

    async def get_product(self, sku):
        return self.products.get(sku)

    async def update_product(self, sku, changes, actor, now):
        self.calls.append(("update_product", sku, dict(changes), actor))
        current = self.products.get(sku)
        if current is None:
            return None
        margins = {
            name.removeprefix("margin_"): value
            for name, value in changes.items()
            if name.startswith("margin_") and name != "margin_notes"
        }
        updated = replace(
            current,
            margins=replace(current.margins, **margins, updated_by=actor, source="manual"),
        )
        self.products[sku] = updated
        return updated

    async def margins_by_sku(self, skus):
        return {sku: self.products[sku] for sku in skus if sku in self.products}

    async def bulk_update_products(self, rows, actor, now):
        self.calls.append(("bulk", [dict(row) for row in rows], actor))
        return len(rows)

    async def products_with_margins(self):
        return list(self.products.values())


@pytest.fixture
def repo() -> Repo:
    return Repo()


@pytest.fixture
def service(repo) -> CommercialMasterService:
    return CommercialMasterService(
        repo, Managers(), margin_reader=SpreadsheetMarginReader(), max_upload_bytes=1024 * 1024
    )


@pytest.mark.parametrize(("page", "size"), [(0, 50), (1, 0), (1, 201)])
async def test_paging_bounds_are_enforced(service, page, size) -> None:
    with pytest.raises(InvalidInputError):
        await service.list_customers(None, None, None, None, None, None, page, size)


async def test_filters_are_cleaned_before_reaching_the_repository(service, repo) -> None:
    await service.list_customers(
        " hoteles   y clubes ", "800.116.299-1", " ", None, None, True, 1, 50
    )

    assert repo.calls[0] == (
        "customers",
        "hoteles y clubes",
        "8001162991",
        None,
        None,
        None,
        True,
        1,
        50,
    )


async def test_active_and_margin_filters_reach_the_repository(service, repo) -> None:
    await service.list_legal_suppliers("x", None, False, 1, 50)
    await service.list_products(None, None, None, None, True, 1, 50, False)

    assert repo.calls[0] == ("legal", "x", None, False, 1, 50)
    assert repo.calls[1][5] is True and repo.calls[1][-1] is False


async def test_unknown_commercial_supplier_is_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        await service.get_commercial_supplier("0")


async def test_primary_nit_must_be_among_the_confirmed_ones(service, repo) -> None:
    with pytest.raises(ForbiddenError):
        await service.update_commercial_supplier(
            principal(), "233", fields=frozenset({"confirmed_nits"}), confirmed_nits=["890900161"]
        )
    with pytest.raises(InvalidInputError, match="principal"):
        await service.update_commercial_supplier(
            principal(admin=True),
            "233",
            fields=frozenset({"confirmed_nits", "primary_nit"}),
            confirmed_nits=["890900161"],
            primary_nit="860002518",
        )

    await service.update_commercial_supplier(
        principal(admin=True),
        "233",
        fields=frozenset({"confirmed_nits", "primary_nit"}),
        confirmed_nits=["890.900.161", "860002518"],
        primary_nit="860002518",
    )
    assert repo.calls[-1] == ("update", "233", {}, ["890900161", "860002518"], "860002518")


async def test_updating_an_unknown_supplier_is_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        await service.update_commercial_supplier(
            principal(admin=True), "0", fields=frozenset({"confirmed_nits"})
        )


# ---------------------------------------------------------------------- products


async def test_only_managers_edit_a_product_and_its_margins(service, repo) -> None:
    with pytest.raises(ForbiddenError):
        await service.update_product(principal(), "7244", {"margin_list": D("0.2")})

    updated = await service.update_product(
        principal(admin=True),
        "7244",
        {"margin_list": D("0.2"), "brand": "  COLOMBINA   SA ", "margin_notes": " Aseo 20% "},
    )

    assert repo.calls[-1] == (
        "update_product",
        "7244",
        {"margin_list": D("0.2"), "brand": "COLOMBINA SA", "margin_notes": "Aseo 20%"},
        "ana@jdh.com.co",
    )
    assert updated.margins.list == D("0.2") and updated.margins.source == "manual"


async def test_a_margin_can_be_cleared_but_must_be_a_fraction(service, repo) -> None:
    admin = principal(admin=True)

    await service.update_product(admin, "7244", {"margin_wholesale": None})
    assert repo.calls[-1][2] == {"margin_wholesale": None}
    for bad in (D("0"), D("1"), D("18"), D("-0.1")):
        with pytest.raises(InvalidInputError, match="entre 0 y 1"):
            await service.update_product(admin, "7244", {"margin_list": bad})


async def test_warehouse_fields_are_not_editable(service) -> None:
    with pytest.raises(InvalidInputError, match="is_active, state"):
        await service.update_product(
            principal(admin=True), "7244", {"state": "Activo", "is_active": True}
        )
    with pytest.raises(InvalidInputError, match="descripción"):
        await service.update_product(principal(admin=True), "7244", {"description": " "})
    with pytest.raises(NotFoundError):
        await service.update_product(principal(admin=True), "0", {"brand": "X"})
    with pytest.raises(NotFoundError):
        await service.get_product("0")


# ----------------------------------------------------------------- margin updater


def rows(*items: MarginRow) -> list[MarginRow]:
    return list(items)


async def test_margin_rows_report_changes_unknown_skus_and_unchanged(service, repo) -> None:
    repo.products["1569"] = product(
        "1569", margins=ProductMargins(list=D("0.18"), wholesale=D("0.13"))
    )
    report = await service.apply_margin_rows(
        rows(
            MarginRow(2, "7244", {"margin_list": D("0.2"), "margin_wholesale": None}),
            MarginRow(3, "1569", {"margin_list": D("0.180000"), "margin_wholesale": D("0.14")}),
            MarginRow(4, "0000", {"margin_list": D("0.2")}),
            MarginRow(5, "7244", {"margin_list": D("0.22")}, problems=()),
        ),
        "ana@jdh.com.co",
        apply=False,
    )

    # SKU 7244 repeats: the last row wins; the report does not write anything.
    assert (report.rows_read, report.updated, report.unchanged, report.duplicated) == (4, 2, 0, 1)
    assert report.unknown_skus == ("0000",) and report.applied is False
    assert not any(call[0] == "bulk" for call in repo.calls)
    by_sku = {change.sku: change for change in report.changes}
    assert by_sku["7244"].changes == {"margin_list": (None, D("0.22"))}
    assert by_sku["1569"].changes == {"margin_wholesale": (D("0.13"), D("0.14"))}


async def test_applying_writes_the_merged_margins_of_the_changed_products(service, repo) -> None:
    repo.products["1569"] = product(
        "1569", margins=ProductMargins(list=D("0.18"), wholesale=D("0.13"), notes="vieja")
    )

    report = await service.apply_margin_rows(
        rows(
            MarginRow(2, "1569", {"margin_wholesale": None}, "nueva", True),
            MarginRow(3, "7244", {"margin_list": D("0.2")}),
        ),
        "importacion",
        apply=True,
    )

    assert report.applied and report.updated == 2
    _, written, actor = repo.calls[-1]
    assert actor == "importacion"
    by_sku = {row["sku"]: row for row in written}
    # Margins the file did not mention keep their value; a 0 cleared the wholesale one.
    assert by_sku["1569"]["margin_list"] == D("0.18")
    assert by_sku["1569"]["margin_wholesale"] is None
    assert by_sku["1569"]["margin_notes"] == "nueva"
    assert by_sku["7244"]["margin_list"] == D("0.2") and by_sku["7244"]["margin_notes"] is None
    assert set(by_sku["7244"]) == set(by_sku["1569"])


async def test_rows_with_unreadable_cells_are_skipped_whole(service, repo) -> None:
    report = await service.apply_margin_rows(
        rows(MarginRow(2, "7244", {"margin_list": D("0.2")}, problems=("Margen inválido: 'x'",))),
        "ana@jdh.com.co",
        apply=True,
    )

    assert report.updated == 0 and report.issues[0].problem == "Margen inválido: 'x'"
    assert report.issues[0].row_number == 2 and not repo.calls


async def test_margin_file_limits_and_permissions(service) -> None:
    admin = principal(admin=True)

    with pytest.raises(ForbiddenError):
        await service.update_margins(principal(), "m.csv", b"x", apply=False)
    with pytest.raises(InvalidInputError, match="vacío"):
        await service.update_margins(admin, "m.csv", b"", apply=False)
    with pytest.raises(InvalidInputError, match="máximo de 1 MB"):
        await service.update_margins(admin, "m.csv", b"x" * (1024 * 1024 + 1), apply=False)
    with pytest.raises(InvalidInputError, match="Formato"):
        await service.update_margins(admin, "m.pdf", b"x", apply=False)
    with pytest.raises(InvalidInputError, match="encabezado"):
        await service.update_margins(admin, "m.csv", b"a;b\n1;2\n", apply=False)


async def test_a_csv_updates_margins_end_to_end(service, repo) -> None:
    csv_text = (
        b"Referencia;Margen Lista 1;Margen Mayorista;Observaciones\n"
        b"7244;20 %;0,13;Aseo 20%\n"
        b"1569;0;;0\n"
    )
    stamp = datetime(2026, 10, 13)
    assert stamp  # the service stamps its own time

    report = await service.update_margins(principal(admin=True), "m.csv", csv_text, apply=True)

    assert report.updated == 1 and report.unchanged == 1
    row = repo.calls[-1][1][0]
    assert row["sku"] == "7244" and row["margin_list"] == D("0.200000")
    assert row["margin_wholesale"] == D("0.130000") and row["margin_notes"] == "Aseo 20%"
