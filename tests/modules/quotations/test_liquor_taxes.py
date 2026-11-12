"""The liquor tax of every list: the screen and a bulk change."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.quotations.application.liquor_taxes import ChangeInput, LiquorTaxService
from app.modules.quotations.domain import liquor
from app.modules.quotations.domain.liquor import LiquorListPrice, LiquorProduct, LiquorSuspect
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter
from app.modules.quotations.infrastructure.workbook import PriceListWorkbookWriter
from tests.modules.quotations.conftest import MAIN_APPROVER, MAIN_MANAGER, product
from tests.modules.supplier_supports.conftest import TODAY, FixedCalendar, person

COAST = ("004", "11N")


def _price(code: str, tax: str | None, price: str = "50000", active: bool = True):
    return LiquorListPrice(code, active, "UNID", Decimal(price), Decimal(tax) if tax else None)


class FakeLiquorSource:
    def __init__(self) -> None:
        self.products = [
            # Juniper: national 4,036, Coast 4,144; list 77X carries a stale value, 88Z none.
            LiquorProduct(
                "18973",
                "Juniper Cocktail",
                "0024",
                "IMPUESTO AL CONSUMO LICORES NAL",
                "0509",
                (
                    _price("1", "4036"),
                    _price("004", "4144"),
                    _price("11N", "4144"),
                    _price("77X", "3900"),
                    _price("88Z", None),
                    _price("OLD", "1000", active=False),
                ),
            ),
            LiquorProduct(
                "26278",
                "Ginebra Selva",
                "0024",
                None,
                "0509",
                (_price("1", "42451"), _price("004", "42451")),
            ),
        ]

    async def liquor_products(self, skus=None):
        return [item for item in self.products if skus is None or item.sku in skus]

    async def liquor_suspects(self):
        return [LiquorSuspect("16438", "Ron Medellín 375", "0507", "IVA 5% LICORES", 0)]


class FakeLiquorMasters:
    def __init__(self) -> None:
        self.catalog = {
            "18973": product("18973", liquor_tax=Decimal(4036), liquor_tax_coast=Decimal(4144)),
            "26278": product("26278", liquor_tax=Decimal(40000), liquor_tax_coast=Decimal(40000)),
        }
        self.written: list[dict] = []

    async def products(self, skus):
        return {sku: self.catalog[sku] for sku in skus if sku in self.catalog}

    async def set_liquor_taxes(self, values, actor):
        self.written.append(dict(values))
        return len(values)


class FakeLiquorRepository:
    def __init__(self) -> None:
        self.items = {}

    async def create(self, item):
        self.items[item.id] = item
        return item

    async def get(self, update_id):
        return self.items.get(update_id)

    async def list(self, limit=50):
        return sorted(self.items.values(), key=lambda item: item.created_at, reverse=True)

    async def save(self, item, expected_version):
        current = self.items[item.id]
        if current.version != expected_version:
            raise ConflictError("cambió")
        self.items[item.id] = item
        return item


@pytest.fixture
def masters_liquor():
    return FakeLiquorMasters()


@pytest.fixture
def taxes(service, masters_liquor) -> LiquorTaxService:
    return LiquorTaxService(
        quotations=service,
        source=FakeLiquorSource(),
        masters=masters_liquor,
        repository=FakeLiquorRepository(),
        calendar=FixedCalendar(),
        workbook=PriceListWorkbookWriter(),
        flat_file=SiesaPriceFileWriter(),
        coast_lists=COAST,
    )


async def test_the_screen_shows_each_regional_and_what_does_not_add_up(taxes) -> None:
    overview = await taxes.overview(person(MAIN_MANAGER))

    juniper, gin = overview.summaries
    assert juniper.national == Decimal(4036) and juniper.coast == Decimal(4144)
    assert [(value.amount, value.lists) for value in juniper.national_values] == [
        (Decimal(3900), ("77X",)),
        (Decimal(4036), ("1",)),
        (None, ("88Z",)),
    ]
    assert liquor.SEVERAL_VALUES in juniper.flags and liquor.MISSING_VALUE in juniper.flags
    # The inactive list does not count.
    assert juniper.lists_with_price == 5
    assert gin.national == Decimal(42451) and liquor.DIFFERS_FROM_MASTER in gin.flags
    assert overview.suspects[0].tax_group == "0507"
    assert overview.coast_lists == ("004", "11N")


async def test_a_bulk_change_writes_every_active_list_with_its_regional(
    taxes, masters_liquor
) -> None:
    collaborator = person(MAIN_MANAGER)
    analyst = person(MAIN_APPROVER)
    proposed = await taxes.create(
        collaborator, [ChangeInput("18973", "4200", "4300")], "Alza de licores 2027"
    )
    assert proposed.status == "pendiente_validacion"
    assert proposed.changes[0].previous_national == Decimal(4036)
    with pytest.raises(ForbiddenError):
        await taxes.validate(collaborator, proposed.id, proposed.version, None)

    validated = await taxes.validate(
        analyst, proposed.id, proposed.version, TODAY + timedelta(days=1)
    )

    rows = {row["price_list_id"]: row for row in validated.export_rows}
    # Every active list that prices it, the inactive one left out.
    assert set(rows) == {"1", "004", "11N", "77X", "88Z"}
    assert rows["004"]["amount"] == "4300.0000" and rows["1"]["amount"] == "4200.0000"
    assert rows["88Z"]["amount"] == "4200.0000" and rows["1"]["price"] == "50000"
    assert validated.changes[0].lists == 5
    flat = await taxes.file(analyst, proposed.id, "txt")
    text = flat.content.decode("ascii").splitlines()
    # Each price (0126) followed by its tax (0127): SIESA's two pages.
    kinds = [line[7:11] for line in text]
    assert kinds.count("0126") == 5 and kinds.count("0127") == 5
    assert kinds[1:3] == ["0126", "0127"]
    workbook = await taxes.file(analyst, proposed.id, "xlsx")
    assert workbook.content[:2] == b"PK"

    uploaded = await taxes.mark_uploaded(analyst, proposed.id, validated.version)

    assert uploaded.status == "cargada"
    assert masters_liquor.written == [{"18973": (Decimal(4200), Decimal(4300), "0509")}]


async def test_a_bulk_change_is_checked(taxes) -> None:
    collaborator = person(MAIN_MANAGER)

    with pytest.raises(InvalidInputError, match="no tiene llave"):
        await taxes.create(collaborator, [ChangeInput("2537", "1", "1")], None)
    with pytest.raises(InvalidInputError, match="repetidos"):
        await taxes.create(
            collaborator, [ChangeInput("18973", "1", "1"), ChangeInput("18973", "2", "2")], None
        )
    with pytest.raises(InvalidInputError, match="de 0 en adelante"):
        await taxes.create(collaborator, [ChangeInput("18973", "-1", "1")], None)
    with pytest.raises(ForbiddenError):
        await taxes.create(person("otro@jdh.com.co"), [ChangeInput("18973", "1", "1")], None)


async def test_a_bulk_change_can_be_turned_down(taxes) -> None:
    collaborator = person(MAIN_MANAGER)
    analyst = person(MAIN_APPROVER)
    proposed = await taxes.create(collaborator, [ChangeInput("26278", "42451", "42451")], None)

    with pytest.raises(InvalidInputError, match="por qué"):
        await taxes.return_(analyst, proposed.id, proposed.version, None)
    returned = await taxes.return_(analyst, proposed.id, proposed.version, "Valor sin soporte")

    assert returned.status == "devuelta" and returned.review_note == "Valor sin soporte"
    with pytest.raises(ConflictError):
        await taxes.validate(analyst, proposed.id, returned.version, None)


def test_a_zero_tax_writes_the_price_without_a_tax_record() -> None:
    rows = [
        {
            "price_list_id": "1",
            "sku": "18973",
            "unit": "UNID",
            "price": "50000",
            "tax_key": "0024",
            "amount": "0",
        }
    ]

    export = liquor.to_export(rows, TODAY)

    assert len(export.rows) == 1 and export.taxes == ()
    assert export.rows[0].price == Decimal(50000)
