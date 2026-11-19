"""Use cases of the quoter."""

import io
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.quotations.application.service import (
    LineChange,
    list_name,
    normalize_nit,
    parse_skus,
)
from app.modules.quotations.domain import rules
from app.modules.quotations.domain.entities import ItemInfo, ListPrice, ValueTaxKey
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, branch, cost, product, sale
from tests.modules.supplier_supports.conftest import FakeManagers, person


async def test_create_builds_every_sku_with_cost_support_and_target(
    service, advisor, warehouse, masters, supports
) -> None:
    warehouse.sales[NIT].append(sale("12014", "12141", supplier_code="359"))
    masters.catalog["12014"] = product("12014", description="Vino tetrapack")
    warehouse.prices["12014"] = ListPrice("12014", Decimal("12141"), Decimal("6911"))
    warehouse.taxes["12014"] = ValueTaxKey("12014", "0011", "LICORES IMPOR")
    supports.items.append(agreement(supplier_value=Decimal("0.05"), product_sku="2537"))

    detail = await service.create(advisor, "900.315.138-1", 12)

    quotation = detail.quotation
    assert quotation.customer_nit == NIT and quotation.status == "borrador"
    assert quotation.price_list_id == "001" and quotation.price_list_customers == 28113
    assert quotation.list_action == "actualizacion" and quotation.regional == "nacional"
    assert quotation.price_family == "generales"
    assert quotation.stock_source == "MDE1 / MDE3 (congelados y refrigerados)"
    assert "lista_compartida" in quotation.warnings
    assert quotation.cost_cut_date == date(2026, 11, 3) and quotation.line_count == 2
    first, second = detail.lines
    # The Generales factor: 3000 x 1.004.
    assert first.cost == Decimal("3012.0000") and first.cost_source == "inventario"
    assert first.sku == "2537" and first.supplier_name == "LOCERIA COLOMBIANA S.A."
    assert first.support is not None and first.support.amount == Decimal("150.6")
    assert second.value_tax_key == "0011" and second.value_tax_amount == Decimal("6911")
    assert "sin_costo" in second.problems
    assert ("last_sales", NIT, 12) in warehouse.calls
    plan = next(call[2] for call in warehouse.calls if call[0] == "costs")
    assert plan.warehouses == ("MDE1", "MDE3") and plan.lactalis_branch == "001"


async def test_a_customer_on_list_one_goes_by_creation(service, advisor, warehouse) -> None:
    warehouse.branch_rows[NIT] = [branch(price_list_id="1", price_list_name="GENERAL")]

    detail = await service.create(advisor, NIT, 12)

    assert detail.quotation.list_action == "creacion"
    assert "lista_general" in detail.quotation.warnings
    plan = next(call[2] for call in warehouse.calls if call[0] == "costs")
    assert plan.highest


async def test_create_checks_access_window_customer_and_sales(service, advisor, warehouse) -> None:
    with pytest.raises(ForbiddenError):
        await service.create(person("otro@jdh.com.co"), NIT, 12)
    with pytest.raises(InvalidInputError):
        await service.create(advisor, NIT, 3)
    with pytest.raises(NotFoundError):
        await service.create(advisor, "111", 6)
    warehouse.down = True
    with pytest.raises(DependencyUnavailableError):
        await service.create(advisor, NIT, 6)


async def test_prices_are_saved_marked_and_versioned(service, advisor) -> None:
    created = await service.create(advisor, NIT, 12)
    quotation = created.quotation

    priced, skipped = await service.update_lines(
        advisor, quotation.id, 1, [LineChange("2537", Decimal("3500"))]
    )

    line = priced.lines[0]
    assert skipped == ()
    assert line.new_price == Decimal("3500") and line.compliance == "bajo_minimo"
    # 3012 / 0.81 = 3718.5 up; 3012 / 0.8 = 3765.
    assert line.min_price == Decimal("3719") and line.optimal_price == Decimal("3765")
    assert priced.quotation.version == 2 and priced.quotation.below_rule_count == 1
    with pytest.raises(ConflictError):
        await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", None)])
    with pytest.raises(InvalidInputError, match="no está"):
        await service.update_lines(advisor, quotation.id, 2, [LineChange("1", Decimal(1))])
    with pytest.raises(InvalidInputError, match="pesos enteros"):
        await service.update_lines(advisor, quotation.id, 2, [LineChange("2537", Decimal("1.5"))])
    with pytest.raises(InvalidInputError, match="mayor que cero"):
        await service.update_lines(advisor, quotation.id, 2, [LineChange("2537", Decimal(0))])
    ok, _ = await service.update_lines(
        advisor, quotation.id, 2, [LineChange("2537", Decimal("4100.00000"))]
    )
    assert ok.lines[0].compliance == "cumple" and ok.quotation.below_rule_count == 0


async def test_a_selection_is_priced_at_one_margin(service, advisor, warehouse, masters) -> None:
    warehouse.sales[NIT].append(sale("1485", "2000"))
    masters.catalog["1485"] = product("1485")
    quotation = (await service.create(advisor, NIT, 12)).quotation

    priced, skipped = await service.update_lines(
        advisor,
        quotation.id,
        1,
        [
            LineChange("2537", margin_pct=Decimal("0.30")),
            LineChange("1485", margin_pct=Decimal("0.30")),
        ],
    )

    line = next(item for item in priced.lines if item.sku == "2537")
    # 3012 / 0.7 = 4302.86
    assert line.new_price == Decimal("4303") and line.compliance == "cumple"
    assert [(item.sku, item.reason) for item in skipped] == [
        ("1485", "Sin costo: no se puede calcular el precio")
    ]
    with pytest.raises(InvalidInputError, match="Ninguna"):
        await service.update_lines(
            advisor, quotation.id, 2, [LineChange("2537", margin_pct=Decimal("1.2"))]
        )


async def test_confirm_validate_upload_and_export_an_update(service, advisor, approver) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    def confirm(version, **values):
        options = {"list_action": None, "new_list_name": None, "activation_date": None}
        return service.confirm(advisor, quotation.id, version, **{**options, **values})

    with pytest.raises(InvalidInputError, match="No hay referencias activas con precio"):
        await confirm(1)
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("3000"))])
    with pytest.raises(InvalidInputError, match="anterior a hoy"):
        await confirm(2, activation_date=date(2020, 1, 1))

    # A price under the minimum no longer blocks: the validator sees it counted.
    pending = await confirm(2)

    assert pending.quotation.status == "pendiente_validacion"
    assert pending.quotation.below_rule_count == 1
    assert pending.quotation.export_price_list_id is None
    assert pending.quotation.confirmed_by == advisor.email
    assert pending.quotation.activation_date == date(2026, 10, 10)
    with pytest.raises(ConflictError, match="pendiente"):
        await service.update_lines(advisor, quotation.id, 3, [LineChange("2537", None)])
    with pytest.raises(ConflictError, match="validada"):
        await service.export(advisor, quotation.id, "txt")
    with pytest.raises(ForbiddenError):
        await service.validate(advisor, quotation.id, 3)
    with pytest.raises(InvalidInputError, match="lista del cliente"):
        await service.validate(approver, quotation.id, 3, price_list_id="1M")

    validated = await service.validate(approver, quotation.id, 3)

    assert validated.quotation.status == "pendiente_carga"
    assert validated.quotation.list_action == "actualizacion"
    assert validated.quotation.export_price_list_id == "001"
    assert validated.quotation.validated_by == approver.email
    flat = await service.export(approver, quotation.id, "txt")
    lines = flat.content.decode("ascii").split("\r\n")
    assert lines[1][7:11] == "0126" and lines[1][19:22] == "001" and lines[1][29:33] == "2537"
    assert lines[1][139:147] == "20261010" and lines[1][167:187] == "000000000003000.0000"
    assert flat.filename == "precios-lista001-900315138-20261010.txt"
    with pytest.raises(ForbiddenError):
        await service.mark_uploaded(advisor, quotation.id, 4)
    with pytest.raises(ConflictError):
        await service.mark_uploaded(approver, quotation.id, 3)

    uploaded = await service.mark_uploaded(approver, quotation.id, 4)

    assert uploaded.quotation.status == "cargada"
    assert uploaded.quotation.uploaded_by == approver.email and uploaded.quotation.uploaded_at
    workbook = await service.export(advisor, quotation.id, "xlsx")
    assert workbook.filename == "ACTUALIZACION LISTAS PRECIOS_asesor@jdh.com.co.xlsx"
    with pytest.raises(ConflictError, match="pendiente de carga"):
        await service.mark_uploaded(approver, quotation.id, 5)
    # Closed: its data never change any more, not even by a refresh or by BI.
    for actor in (advisor, approver):
        with pytest.raises(ConflictError, match="ya está cerrada"):
            await service.update_lines(actor, quotation.id, 5, [LineChange("2537", None)])
        with pytest.raises(ConflictError, match="ya está cerrada"):
            await service.refresh(actor, quotation.id, 5)
    with pytest.raises(ConflictError, match="ya está cerrada"):
        await service.confirm(
            advisor, quotation.id, 5, list_action=None, new_list_name=None, activation_date=None
        )
    # Sending the offer changes none of its data: allowed, and it stays loaded.
    sent = await service.send(advisor, quotation.id, 5)
    assert sent.quotation.status == "cargada" and sent.quotation.sent_by == advisor.email
    assert sent.quotation.validated_at is not None and sent.quotation.uploaded_at is not None


async def test_the_offer_goes_to_the_customer_without_validation(
    service, advisor, approver
) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    with pytest.raises(InvalidInputError, match="no tiene referencias activas con precio"):
        await service.send(advisor, quotation.id, 1)
    # A price under the minimum does not block sending either.
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("3000"))])

    sent = await service.send(advisor, quotation.id, 2)

    # Sending is not a status (2026-11-19): it stays a draft, with who sent it and when.
    assert sent.quotation.status == "borrador" and sent.quotation.sent_by == advisor.email
    assert sent.quotation.sent_at is not None
    assert sent.quotation.below_rule_count == 1 and sent.quotation.confirmed_at is None
    # Still editable, and the edit keeps when it was sent.
    edited, _ = await service.update_lines(
        advisor, quotation.id, 3, [LineChange("2537", Decimal("3100"))]
    )
    assert edited.quotation.status == "borrador" and edited.quotation.sent_at is not None
    again = await service.send(advisor, quotation.id, 4)
    assert again.quotation.status == "borrador"
    # Then the price list goes to validation.
    pending = await service.confirm(
        advisor, quotation.id, 5, list_action=None, new_list_name=None, activation_date=None
    )
    assert pending.quotation.status == "pendiente_validacion"
    # Sending while it is validated changes nothing of it either.
    resent = await service.send(approver, quotation.id, 6)
    assert resent.quotation.status == "pendiente_validacion"
    assert resent.quotation.sent_by == approver.email
    # BI quotes too, and sends its own.
    own = (await service.create(approver, NIT, 12)).quotation
    await service.update_lines(approver, own.id, 1, [LineChange("2537", Decimal("4100"))])
    assert (await service.send(approver, own.id, 2)).quotation.status == "borrador"


async def test_list_update_validators_validate_customer_quotations(service, advisor) -> None:
    # BI, 2026-11-19: the customer quotations pending validation are validated from
    # Actualización de listas, by whoever validates list updates.
    validator = person("validador.listas@jdh.com.co")
    assert not await service.can_validate(validator)
    service.list_approvers = FakeManagers(["bi@jdh.com.co", validator.email])
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])
    await service.confirm(
        advisor, quotation.id, 2, list_action=None, new_list_name=None, activation_date=None
    )

    assert await service.can_read(validator) and await service.can_validate(validator)
    pending = await service.list(
        validator,
        mine=False,
        status="pendiente_validacion",
        customer_nit=None,
        q=None,
        page=1,
        size=20,
    )
    assert [item.id for item in pending.items] == [quotation.id]
    validated = await service.validate(validator, quotation.id, 3)
    assert validated.quotation.status == "pendiente_carga"
    assert validated.quotation.validated_by == validator.email


async def test_create_a_list_for_a_customer_on_list_one(
    service, advisor, approver, warehouse
) -> None:
    warehouse.branch_rows[NIT] = [branch(price_list_id="1", price_list_name="GENERAL")]
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])

    def confirm(version, **values):
        options = {"list_action": None, "new_list_name": None, "activation_date": None}
        return service.confirm(advisor, quotation.id, version, **{**options, **values})

    def validate(version, **values):
        return service.validate(approver, quotation.id, version, **values)

    with pytest.raises(InvalidInputError, match="lista 1"):
        await confirm(2, list_action="actualizacion")

    # The advisor no longer gives the code of the new list.
    pending = await confirm(2, new_list_name="Hotel El Bosque Ñandú")

    assert pending.quotation.export_price_list_id is None
    assert pending.quotation.new_list_name == "HOTEL EL BOSQUE NANDU"
    with pytest.raises(InvalidInputError, match="por qué"):
        await service.return_to_draft(approver, quotation.id, 3, None)
    returned = await service.return_to_draft(approver, quotation.id, 3, "Revisar la fecha")
    assert returned.quotation.status == "borrador"
    assert returned.quotation.review_note == "Revisar la fecha"
    await confirm(4)
    with pytest.raises(InvalidInputError, match="código de la lista nueva"):
        await validate(5)
    with pytest.raises(InvalidInputError, match="ya existe"):
        await validate(5, price_list_id="c01")
    with pytest.raises(InvalidInputError, match="1 a 3"):
        await validate(5, price_list_id="C-1")

    created = await validate(5, price_list_id="c02")

    assert created.quotation.status == "pendiente_carga"
    assert created.quotation.export_price_list_id == "C02"
    assert created.quotation.list_action == "creacion"
    lines = (await service.export(advisor, quotation.id, "txt")).content.split(b"\r\n")
    assert [len(item) for item in lines] == [18, 101, 254, 18, 0]
    assert lines[1][7:11] == b"0112" and lines[1][19:22] == b"C02"
    assert lines[1][22:62].rstrip() == b"COTIZADOR HOTEL EL BOSQUE SAS"
    assert lines[2][19:22] == b"C02"


async def test_refresh_keeps_prices_and_reprices_with_the_new_cost(
    service, advisor, warehouse
) -> None:
    quotation = (await service.create(advisor, NIT, 6)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4000"))])
    warehouse.cost_rows["2537"] = cost(inventory="3400", quote="3500")

    refreshed = await service.refresh(advisor, quotation.id, 2)

    line = refreshed.lines[0]
    assert line.new_price == Decimal("4000") and line.cost == Decimal("3514.0000")
    assert line.cost_source == "cotizacion" and line.compliance == "bajo_minimo"
    assert refreshed.quotation.version == 3


async def test_refresh_sends_a_list_one_customer_to_creation(
    service, advisor, warehouse, repository
) -> None:
    warehouse.branch_rows[NIT] = [branch(price_list_id="1", price_list_name="GENERAL")]
    quotation = (await service.create(advisor, NIT, 6)).quotation
    # A quotation stored before list creation existed says it updates list 1.
    stored = repository.items[quotation.id]
    repository.items[quotation.id] = replace(
        stored, quotation=replace(stored.quotation, list_action="actualizacion")
    )

    refreshed = await service.refresh(advisor, quotation.id, 1)

    assert refreshed.quotation.list_action == "creacion"


async def test_added_skus_are_priced_kept_on_refresh_and_removable(
    service, advisor, warehouse, masters
) -> None:
    masters.catalog["26278"] = product("26278", liquor_tax=Decimal("42451"))
    warehouse.cost_rows["26278"] = cost("26278", inventory="60000")
    warehouse.taxes["26278"] = ValueTaxKey("26278", "0024", "LICORES NAL")
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(InvalidInputError, match="Ninguna referencia.*no existen: 404.*inactivas"):
        await service.add_lines(advisor, quotation.id, 1, ["404, 9999"])
    added, result = await service.add_lines(advisor, quotation.id, 1, [" 26278 ", "26278", "404"])

    line = added.lines[-1]
    assert result.added == ("26278",) and result.not_found == ("404",) and result.repeated == 1
    assert [item.sku for item in added.lines] == ["2537", "26278"]
    assert line.origin == "agregado" and line.position == 2 and line.last_price is None
    assert line.value_tax_key == "0024" and line.value_tax_amount == Decimal("42451")
    assert added.quotation.line_count == 2 and added.quotation.version == 2

    priced, _ = await service.update_lines(
        advisor, quotation.id, 2, [LineChange("26278", Decimal("80000"))]
    )
    # Pasting again revalidates with today's cost and keeps the advisor's price.
    warehouse.cost_rows["26278"] = cost("26278", inventory="70000")
    again, result = await service.add_lines(
        advisor, quotation.id, priced.quotation.version, ["26278\n2537"]
    )
    kept = next(item for item in again.lines if item.sku == "26278")
    assert result.revalidated == ("26278", "2537") and result.added == ()
    assert kept.cost == Decimal("70280.0000") and kept.new_price == Decimal("80000")
    assert kept.compliance == "bajo_minimo" and kept.id == line.id
    history = next(item for item in again.lines if item.sku == "2537")
    assert history.origin == "historico" and history.last_price == Decimal("4000")
    refreshed = await service.refresh(advisor, quotation.id, again.quotation.version)
    kept = next(item for item in refreshed.lines if item.sku == "26278")
    assert kept.origin == "agregado" and kept.new_price == Decimal("80000")

    with pytest.raises(InvalidInputError, match="Solo se quitan"):
        await service.remove_line(advisor, quotation.id, refreshed.quotation.version, "2537")
    removed = await service.remove_line(advisor, quotation.id, refreshed.quotation.version, "26278")
    assert [item.sku for item in removed.lines] == ["2537"]


async def test_inactive_history_skus_are_shown_skipped_and_removable(
    service, advisor, warehouse, masters
) -> None:
    warehouse.sales[NIT].append(sale("1485", "5000", is_active=False))
    masters.catalog["1485"] = product("1485")
    warehouse.cost_rows["1485"] = cost("1485", inventory="3000", is_active=False)
    quotation = (await service.create(advisor, NIT, 12)).quotation

    priced, _ = await service.update_lines(
        advisor,
        quotation.id,
        1,
        [LineChange("2537", Decimal("4100")), LineChange("1485", Decimal("10"))],
    )

    inactive = next(item for item in priced.lines if item.sku == "1485")
    assert not inactive.is_active and "referencia_inactiva" in inactive.problems
    # The inactive one is not counted and does not block the confirmation.
    assert priced.quotation.changed_count == 1 and priced.quotation.below_rule_count == 0
    removed = await service.remove_line(advisor, quotation.id, 2, "1485")
    assert [item.sku for item in removed.lines] == ["2537"]


async def test_every_line_carries_its_category(service, advisor, warehouse, masters) -> None:
    warehouse.sales[NIT].append(sale("1485", "5000", category=None))
    masters.catalog["1485"] = product("1485")
    quotation = (await service.create(advisor, NIT, 12)).quotation

    added, _ = await service.add_lines(advisor, quotation.id, 1, ["26278"])

    categories = {line.sku: line.category for line in added.lines}
    # 1485 has no product row: looked up once and stored as "no category" (shown as null).
    assert categories == {"2537": "ASEO", "1485": "", "26278": "LICORES"}
    # Revalidating a stored line keeps it; a refresh reads it again from the warehouse.
    again, _ = await service.add_lines(advisor, quotation.id, 2, ["26278"])
    assert next(line for line in again.lines if line.sku == "26278").category == "LICORES"
    warehouse.sales[NIT][0] = sale(category="LIMPIEZA")
    refreshed = await service.refresh(advisor, quotation.id, 3)
    assert {line.sku: line.category for line in refreshed.lines}["2537"] == "LIMPIEZA"


async def test_an_old_quotation_gets_its_categories_when_read(
    service, advisor, warehouse, repository
) -> None:
    warehouse.sales[NIT].append(sale("1485", "5000"))
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await add_local(service, advisor, quotation.id, 1)
    # Stored before 0050: no category on any line.
    stored = repository.items[quotation.id]
    repository.items[quotation.id] = replace(
        stored, lines=tuple(replace(line, category=None) for line in stored.lines)
    )
    warehouse.item_rows["2537"] = ItemInfo("2537", "Abrasivo", "UNID", category="ASEO")
    before = repository.items[quotation.id].quotation

    detail = await service.get(advisor, quotation.id)

    categories = {line.sku: line.category for line in detail.lines}
    # 1485 has no product row: stored as looked up; the local product is never looked up.
    assert categories == {"2537": "ASEO", "1485": "", "LOC-1": None}
    assert detail.quotation == before and detail.quotation.version == 2
    assert repository.items[quotation.id].lines[0].category == "ASEO"
    assert len(repository.category_writes) == 1
    await service.get(advisor, quotation.id)
    # Nothing left to look up: no second write.
    assert len(repository.category_writes) == 1


async def test_reading_survives_a_warehouse_outage_for_categories(
    service, advisor, warehouse, repository
) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    stored = repository.items[quotation.id]
    repository.items[quotation.id] = replace(
        stored, lines=tuple(replace(line, category=None) for line in stored.lines)
    )
    warehouse.down = True

    detail = await service.get(advisor, quotation.id)

    assert detail.lines[0].category is None and repository.category_writes == []


async def add_local(service, advisor, quotation_id, version, **values):
    fields = {
        "description": "Queso costeño",
        "supplier_name": None,
        "price": "12000",
        "vat_pct": 5,
    }
    return await service.add_local_line(advisor, quotation_id, version, **{**fields, **values})


async def test_a_local_product_is_quoted_outside_the_rule(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    detail = await add_local(
        service,
        advisor,
        quotation.id,
        1,
        description="  Queso   costeño  ",
        supplier_name=" Lácteos del Sur ",
        price=Decimal(12000),
    )

    line = detail.lines[-1]
    assert line.sku == "LOC-1" and line.origin == "local" and line.position == 2
    assert line.description == "Queso costeño" and line.supplier_name == "Lácteos del Sur"
    assert line.supplier_code is None and line.unit is None and line.category is None
    assert line.vat_pct == Decimal(5) and line.new_price == Decimal(12000)
    assert line.is_active and line.last_price is None and line.cost is None
    assert line.compliance is None and line.variation_pct is None
    assert line.problems == ("producto_local",) and line.support_candidates == ()
    assert line.min_price is None and line.optimal_price is None and not line.below_target
    assert rules.price_with_taxes(line) == Decimal(12000)
    # It is neither a changed price nor one below the rule.
    assert detail.quotation.version == 2 and detail.quotation.line_count == 2
    assert detail.quotation.changed_count == 0 and detail.quotation.below_rule_count == 0
    second = await add_local(service, advisor, quotation.id, 2, price=7000, vat_pct="19")
    assert [item.sku for item in second.lines] == ["2537", "LOC-1", "LOC-2"]
    removed = await service.remove_line(advisor, quotation.id, 3, "LOC-1")
    third = await add_local(service, advisor, quotation.id, 4, vat_pct=0)
    # Numbers are never reused, so a deleted SKU never comes back as another product.
    assert [item.sku for item in third.lines] == ["2537", "LOC-2", "LOC-3"]
    assert [item.sku for item in removed.lines] == ["2537", "LOC-2"]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"description": "   "}, "descripción"),
        ({"description": None}, "descripción"),
        ({"description": "x" * 121}, "máximo 120"),
        ({"supplier_name": "x" * 121}, "proveedor"),
        ({"price": None}, "Escribe el precio"),
        ({"price": "abc"}, "número"),
        ({"price": 0}, "mayor que cero"),
        ({"price": "1500.5"}, "pesos enteros"),
        ({"vat_pct": 16}, "IVA"),
        ({"vat_pct": None}, "IVA"),
        ({"vat_pct": "19.5"}, "IVA"),
    ],
)
async def test_a_local_product_checks_its_fields(service, advisor, values, message) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(InvalidInputError, match=message):
        await add_local(service, advisor, quotation.id, 1, **values)


async def test_a_local_product_follows_the_version_and_the_state(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])

    with pytest.raises(ConflictError):
        await add_local(service, advisor, quotation.id, 1)
    with pytest.raises(ForbiddenError):
        await add_local(service, person("otro@jdh.com.co"), quotation.id, 2)
    await service.confirm(
        advisor, quotation.id, 2, list_action=None, new_list_name=None, activation_date=None
    )
    with pytest.raises(ConflictError, match="pendiente"):
        await add_local(service, advisor, quotation.id, 3)


async def test_a_local_product_never_reaches_the_files(service, advisor, approver) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await add_local(service, advisor, quotation.id, 1, price=99)
    priced, skipped = await service.update_lines(
        advisor,
        quotation.id,
        2,
        [
            LineChange("2537", Decimal("4100")),
            LineChange("LOC-1", margin_pct=Decimal("0.30")),
        ],
    )
    assert [(item.sku, item.reason) for item in skipped] == [
        ("LOC-1", "Producto solo para cotización: sin costo para calcular el precio")
    ]
    assert priced.quotation.changed_count == 1 and priced.quotation.below_rule_count == 0
    await service.confirm(
        advisor, quotation.id, 3, list_action=None, new_list_name=None, activation_date=None
    )
    await service.validate(approver, quotation.id, 4)
    flat = (await service.export(advisor, quotation.id, "txt")).content.split(b"\r\n")
    assert [len(item) for item in flat] == [18, 254, 18, 0] and b"LOC-" not in b"".join(flat)
    workbook = load_workbook(
        io.BytesIO((await service.export(advisor, quotation.id, "xlsx")).content)
    )
    assert [row[1] for row in workbook["Precios"].iter_rows(min_row=2, values_only=True)] == [2537]


async def test_a_local_product_is_priced_kept_and_left_alone(service, advisor, warehouse) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    created = await add_local(service, advisor, quotation.id, 1)
    local_id = created.lines[-1].id

    priced, _ = await service.update_lines(
        advisor, quotation.id, 2, [LineChange("LOC-1", Decimal("15000"))]
    )
    assert priced.lines[-1].new_price == Decimal(15000) and priced.lines[-1].compliance is None
    cleared, _ = await service.update_lines(advisor, quotation.id, 3, [LineChange("LOC-1", None)])
    assert cleared.lines[-1].new_price is None
    await service.update_lines(advisor, quotation.id, 4, [LineChange("LOC-1", Decimal("15000"))])

    # The warehouse now has two sales: the local product goes after them, untouched.
    warehouse.sales[NIT].append(sale("1485", "2000"))
    refreshed = await service.refresh(advisor, quotation.id, 5)
    local = refreshed.lines[-1]
    assert [line.sku for line in refreshed.lines] == ["2537", "1485", "LOC-1"]
    assert local.id == local_id and local.position == 3 and local.new_price == Decimal(15000)
    assert local.origin == "local" and local.problems == ("producto_local",)

    # Pasting its SKU never looks it up in SIESA nor reprices it.
    pasted, result = await service.add_lines(advisor, quotation.id, 6, ["LOC-1", "2537"])
    assert result.not_found == ("LOC-1",) and result.revalidated == ("2537",)
    assert pasted.lines[-1] == local
    assert all("LOC-1" not in call[1] for call in warehouse.calls if call[0] == "costs")


async def test_customer_without_sales_starts_empty(service, advisor, warehouse) -> None:
    warehouse.sales[NIT] = []

    detail = await service.create(advisor, NIT, 6)

    assert detail.lines == () and "sin_ventas" in detail.quotation.warnings
    added, _ = await service.add_lines(advisor, detail.quotation.id, 1, ["26278"])
    assert added.lines[0].position == 1


def test_pasted_lists_from_excel_or_commas() -> None:
    column = "26278\r\n12014\r\n\r\n2537.0\r\n26278\r\n"
    assert parse_skus([column]) == (["26278", "12014", "2537"], 1)
    assert parse_skus(["26278, 12014;2537\t1485 'A-01'"]) == (
        ["26278", "12014", "2537", "1485", "A-01"],
        0,
    )
    assert parse_skus(["", " , ;"]) == ([], 0)


async def test_customers_mine_uses_the_seller_linked_by_email(
    service, advisor, approver, warehouse
) -> None:
    mine, linked = await service.customers(advisor, "mine", None, 1, 20)
    everyone, _ = await service.customers(advisor, "all", None, 1, 20)

    assert linked and [item.nit for item in mine.items] == [NIT]
    assert everyone.total == 2
    unlinked = person("nuevo@jdh.com.co", admin=True)
    empty, linked = await service.customers(unlinked, "mine", None, 1, 20)
    assert empty.total == 0 and not linked
    validator = await service.permissions(approver)
    # BI validates and also quotes.
    assert validator.can_manage and validator.can_validate
    warehouse.down = True
    everyone, linked = await service.customers(advisor, "all", None, 1, 20)
    assert everyone.total == 2 and not linked
    permissions = await service.permissions(advisor)
    assert permissions.can_manage and permissions.seller is None and not permissions.can_validate


async def test_list_and_delete(service, advisor, approver) -> None:
    quotation = (await service.create(advisor, NIT, 6)).quotation
    other = person("otro@jdh.com.co", admin=True)

    assert (
        await service.list(
            advisor, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
        )
    ).total == 1
    assert (
        await service.list(
            other, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
        )
    ).total == 0
    # The approver reads every quotation to validate it.
    assert (
        await service.list(
            approver, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
        )
    ).total == 1
    with pytest.raises(ForbiddenError):
        await service.delete(person(advisor.email), quotation.id)
    await service.delete(other, quotation.id)
    with pytest.raises(NotFoundError):
        await service.get(advisor, quotation.id)


async def test_bi_has_the_whole_quoter(service, advisor, approver, warehouse, masters) -> None:
    masters.catalog["26278"] = product("26278")
    warehouse.cost_rows["26278"] = cost("26278", inventory="60000")
    permissions = await service.permissions(approver)
    assert permissions.can_manage and permissions.can_validate

    own = (await service.create(approver, NIT, 6)).quotation
    assert own.created_by == approver.email
    advisors = (await service.create(advisor, NIT, 6)).quotation

    # BI edits the advisor's quotation too: refresh, add products, delete.
    refreshed = await service.refresh(approver, advisors.id, advisors.version)
    added, _ = await service.add_lines(
        approver, advisors.id, refreshed.quotation.version, ["26278"]
    )
    assert added.quotation.version > advisors.version
    # Another advisor (Comercial, no validation) neither opens nor edits it.
    colleague = person(advisor.email)
    with pytest.raises(ForbiddenError):
        await service.refresh(colleague, advisors.id, added.quotation.version)
    # The advisor never reaches the one BI made.
    with pytest.raises(ForbiddenError):
        await service.get(advisor, own.id)
    mine = await service.list(
        approver, mine=True, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert [item.id for item in mine.items] == [own.id]
    await service.delete(approver, advisors.id)
    with pytest.raises(NotFoundError):
        await service.get(approver, advisors.id)


async def test_a_moderator_reads_every_quotation(service, advisor) -> None:
    created = (await service.create(advisor, NIT, 6)).quotation
    moderator = replace(person("mod@jdh.com.co"), role="moderator")

    page = await service.list(
        moderator, mine=False, status=None, customer_nit=None, q=None, page=1, size=20
    )
    assert [item.id for item in page.items] == [created.id]


def test_normalize_nit_and_list_name() -> None:
    assert normalize_nit(" 900.315.138-1 ") == "900315138"
    with pytest.raises(InvalidInputError):
        normalize_nit("--")
    assert list_name("Panadería  Ñapa — Sur") == "PANADERIA NAPA SUR"
    assert len(list_name("X" * 60)) == 40
