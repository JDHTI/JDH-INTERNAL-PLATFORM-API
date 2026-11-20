"""Supplier supports use cases: validation, bindings to the masters, permissions, versions."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.supplier_supports.domain.entities import SupportData, Tier
from tests.modules.supplier_supports.conftest import FAMILIA_ID, SKU_1128, TODAY, person

D = Decimal


def tactic(**values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "costo_neto",
        "supplier_value": D("80"),
        "reference_cost": D("100"),
        "target_margin_pct": D("0.17"),
        "customer_nit": "800116299",
        "product_sku": "1128",
        "valid_from": date(2026, 7, 1),
        "valid_to": date(2027, 1, 30),
    }
    return SupportData(**{**base, **values})


async def test_create_binds_supplier_product_and_customer(service, manager) -> None:
    view = await service.create(manager, tactic(), supplier_code="233")

    data = view.support.data
    assert data.commercial_supplier_id == FAMILIA_ID
    assert data.product_id == SKU_1128.id
    assert data.brand == "FAMILIA"  # taken from the product when not given
    assert data.customer_name == "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"
    assert view.validity == "vigente"
    assert view.derived.pct_on_cost == D("0.2")
    assert view.support.created_by == manager.email and view.support.version == 1


async def test_create_normalizes_scope_and_nit(service, manager) -> None:
    data = tactic(
        customer_nit="800.116.299",
        regionals=("antioquia", " Antioquia ", "centro"),
        channels=("todos",),
        program="  PLAN   10X10 ",
    )

    created = (await service.create(manager, data, supplier_code="233")).support.data

    assert created.customer_nit == "800116299"
    assert created.regionals == ("ANTIOQUIA", "CENTRO")
    assert created.channels == ()  # TODOS means no restriction
    assert created.program == "PLAN 10X10"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"product_sku": "9999"}, "Referencia JDH desconocida"),
        ({"customer_nit": "111"}, "NIT de cliente desconocido"),
        ({"valid_to": date(2026, 1, 1)}, "anterior"),
        ({"calc_method": "pct_costo", "supplier_value": D("5")}, "entre 0 % y 100 %"),
        ({"supplier_value": None}, "necesita el «Valor apoyo»"),
        ({"calc_method": "precio_neto", "target_margin_pct": None}, "«Margen garantizado»"),
        ({"reference_cost": D("0")}, "mayor que cero"),
        ({"tiers": (Tier("unidades", D("10"), D("5"), D("1")),)}, "escala"),
    ],
)
async def test_create_rejects_invalid_data(service, manager, changes, message) -> None:
    with pytest.raises(InvalidInputError, match=message):
        await service.create(manager, tactic(**changes), supplier_code="233")


async def test_a_margin_exception_takes_its_margin_from_the_field_the_form_shows(
    service, manager
) -> None:
    exception = SupportData(
        kind="excepcion_margen",
        calc_method="margen_minimo",
        product_sku="1128",
        supported_margin_pct=D("0.10"),
    )

    created = (await service.create(manager, exception, supplier_code="233")).support

    # «Excepción margen mínimo autorizado JDH» is the exception's margin: both fields agree.
    saved = created.data
    assert (saved.min_margin_pct, saved.supported_margin_pct) == (D("0.10"), D("0.10"))
    edited = (
        await service.update(
            manager,
            created.id,
            expected_version=created.version,
            changes={"min_margin_pct": D("0.12")},
        )
    ).support.data
    assert (edited.min_margin_pct, edited.supported_margin_pct) == (D("0.12"), D("0.12"))
    # Without either, the error names the field the person sees.
    with pytest.raises(InvalidInputError) as caught:
        await service.create(
            manager, replace(exception, supported_margin_pct=None), supplier_code="233"
        )
    assert caught.value.field == "supported_margin_pct"
    assert "«Excepción margen mínimo autorizado JDH»" in caught.value.detail


async def test_unknown_supplier_and_missing_supplier_are_rejected(service, manager) -> None:
    with pytest.raises(InvalidInputError, match="Proveedor comercial desconocido"):
        await service.create(manager, tactic(), supplier_code="99999")
    with pytest.raises(InvalidInputError, match="requiere un proveedor"):
        await service.create(manager, tactic())


async def test_margin_exceptions_need_no_supplier(service, manager) -> None:
    data = SupportData(
        kind="excepcion_margen",
        calc_method="margen_minimo",
        min_margin_pct=D("0.13"),
        customer_nit="800116299",
        authorized_by="ISABEL QUIROGA",
    )

    view = await service.create(manager, data)

    assert view.support.data.commercial_supplier_id is None


async def test_only_managers_write(service) -> None:
    outsider = person("otro@jdh.com.co")

    with pytest.raises(ForbiddenError):
        await service.create(outsider, tactic(), supplier_code="233")
    assert await service.create(person(admin=True), tactic(), supplier_code="233")


async def test_update_changes_only_the_sent_fields_and_bumps_the_version(service, manager) -> None:
    created = await service.create(manager, tactic(notes="original"), supplier_code="233")

    updated = await service.update(
        manager,
        created.support.id,
        expected_version=1,
        changes={"supplier_value": D("75"), "valid_to": None},
    )

    data = updated.support.data
    assert data.supplier_value == D("75") and data.valid_to is None
    assert data.notes == "original" and data.customer_nit == "800116299"
    assert updated.support.version == 2
    assert updated.validity == "vigente"  # open-ended now


async def test_update_with_a_stale_version_is_a_conflict(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")
    await service.update(manager, created.support.id, expected_version=1, changes={"notes": "a"})

    with pytest.raises(ConflictError, match="cambió"):
        await service.update(
            manager, created.support.id, expected_version=1, changes={"notes": "b"}
        )


async def test_update_rejects_fields_that_are_not_editable(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")

    for field in ("created_by", "version", "commercial_supplier_id", "voided_at"):
        with pytest.raises(InvalidInputError, match="no editables"):
            await service.update(
                manager, created.support.id, expected_version=1, changes={field: "x"}
            )


async def test_update_unknown_support_is_not_found(service, manager) -> None:

    with pytest.raises(NotFoundError):
        await service.update(manager, uuid4(), expected_version=1, changes={"notes": "x"})


async def test_void_keeps_history_and_blocks_further_edits(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")

    voided = await service.void(
        manager, created.support.id, expected_version=1, reason="Cliente dejó de comprar"
    )

    assert voided.support.data.status == "anulado"
    assert voided.support.void_reason == "Cliente dejó de comprar"
    with pytest.raises(ConflictError, match="anulado"):
        await service.update(
            manager, created.support.id, expected_version=2, changes={"notes": "x"}
        )
    with pytest.raises(ConflictError, match="ya está anulado"):
        await service.void(manager, created.support.id, expected_version=2, reason="otra vez")
    with pytest.raises(InvalidInputError, match="motivo"):
        await service.void(manager, created.support.id, expected_version=2, reason="  ")


async def test_status_cannot_be_voided_through_update(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")

    with pytest.raises(InvalidInputError, match="anulación"):
        await service.update(
            manager, created.support.id, expected_version=1, changes={"status": "anulado"}
        )


async def test_batch_is_validated_before_anything_is_saved(service, manager, repo) -> None:
    items = [(tactic(), "233"), (tactic(product_sku="9999"), "233")]

    with pytest.raises(InvalidInputError):
        await service.create_many(manager, items)

    assert repo.items == {}
    created = await service.create_many(
        manager, [(tactic(), "233"), (tactic(product_sku="8749"), "233")]
    )
    assert len(created) == 2


async def test_import_never_loses_a_row_with_unknown_references(service, repo) -> None:
    data = tactic(customer_nit="999999999", product_sku="0000", source_key="Familia:12")

    outcome = await service.import_support(data, supplier_code="233")

    assert outcome.created
    saved = repo.items[outcome.support_id].data
    assert saved.status == "pendiente_revision"
    assert {issue.code for issue in saved.issues} == {"nit_sin_cruce", "sku_sin_cruce"}
    assert saved.customer_nit == "999999999" and saved.product_sku == "0000"
    assert repo.items[outcome.support_id].created_by == "importacion"


async def test_import_is_idempotent_by_source_key(service, repo) -> None:
    data = tactic(source_key="Familia:12")

    first = await service.import_support(data, supplier_code="233")
    second = await service.import_support(data, supplier_code="233")

    assert first.created and not second.created
    assert len(repo.items) == 1


async def test_import_with_an_unknown_supplier_becomes_a_reviewable_exception(
    service, repo
) -> None:
    outcome = await service.import_support(tactic(source_key="Kimberly:3"), supplier_code="424242")

    saved = repo.items[outcome.support_id].data
    assert saved.kind == "excepcion_margen" and saved.status == "pendiente_revision"
    assert "proveedor_sin_cruce" in {issue.code for issue in saved.issues}


async def test_editing_a_note_of_a_support_with_a_pending_sku_still_works(service, manager) -> None:
    outcome = await service.import_support(
        tactic(product_sku="0000", source_key="Familia:1"), supplier_code="233"
    )

    updated = await service.update(
        manager, outcome.support_id, expected_version=1, changes={"notes": "revisado"}
    )

    assert updated.support.data.notes == "revisado"
    assert updated.support.data.status == "pendiente_revision"


async def test_moving_to_activo_clears_the_issues(service, manager) -> None:
    outcome = await service.import_support(
        tactic(product_sku="0000", source_key="Familia:2"), supplier_code="233"
    )

    updated = await service.update(
        manager,
        outcome.support_id,
        expected_version=1,
        changes={"status": "activo", "product_sku": "1128"},
    )

    assert updated.support.data.issues == ()
    assert updated.support.data.product_id == SKU_1128.id


async def test_search_builds_a_validated_spec(service, repo) -> None:
    await service.search(
        match="any",
        conditions=[{"field": "supplier.code", "op": "eq", "value": "233"}],
        q="  sodexo ",
        validity="vencido",
        sort=["-valid_from"],
        page=2,
        size=10,
    )

    spec = repo.last_search
    assert spec.q == "sodexo" and spec.active_on == TODAY and spec.validity == "vencido"
    assert spec.group.match == "any" and spec.page == 2 and spec.sort[0].descending


@pytest.mark.parametrize(
    "arguments",
    [{"page": 0}, {"size": 0}, {"size": 500}, {"validity": "nunca"}, {"sort": ["nope"]}],
)
async def test_search_rejects_bad_arguments(service, arguments) -> None:
    with pytest.raises(InvalidInputError):
        await service.search(**arguments)


async def test_catalogs_expose_vocabularies_and_scope_values(service) -> None:
    catalogs = await service.catalogs()

    assert "pct_costo" in catalogs["calc_methods"]
    assert catalogs["regionals"] == ["ANTIOQUIA"]


async def test_the_state_people_see_is_the_validity_or_voided_and_review_is_a_flag(
    service, manager
) -> None:
    current = await service.create(manager, tactic(), supplier_code="233")
    expired = await service.create(
        manager,
        tactic(valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31)),
        supplier_code="233",
    )
    future = await service.create(
        manager, tactic(valid_from=date(2027, 2, 1), valid_to=None), supplier_code="233"
    )
    pending = await service.import_support(
        tactic(product_sku="0000", source_key="Familia:9"), supplier_code="233"
    )

    assert (current.state, current.needs_review) == ("vigente", False)
    assert (expired.state, expired.validity, expired.support.data.status) == (
        "vencido",
        "vencido",
        "activo",
    )  # the old "activo + vencido" contradiction is now one state: vencido
    assert future.state == "programado"
    review = await service.get(pending.support_id)
    assert (review.state, review.needs_review) == ("vigente", True)

    voided = await service.void(
        manager, expired.support.id, expected_version=1, reason="ya no aplica"
    )
    assert (voided.state, voided.needs_review) == ("anulado", False)


async def test_search_passes_state_and_review_filters_and_rejects_unknown_states(
    service, repo
) -> None:
    await service.search(state="anulado", needs_review=True)

    assert repo.last_search.state == "anulado" and repo.last_search.needs_review is True
    with pytest.raises(InvalidInputError, match="state"):
        await service.search(state="activo")  # the old status name is not a state


async def test_the_pack_factor_and_the_unit_of_the_cost_are_completed_from_the_masters(
    service, manager
) -> None:
    # Maizena: the sheet gives a cost of 13.783 and no pack factor or unit.
    view = await service.create(
        manager, tactic(product_sku="3474", reference_cost=D("13783")), supplier_code="233"
    )

    data = view.support.data
    assert data.pack_factor == D("24") and data.value_unit == "unidad"
    assert data.unit_basis_source == "inferido_dwh"
    assert view.derived.cost.unit == D("13783") and view.derived.cost.box == D("330792")


async def test_a_unit_declared_by_the_sheet_or_by_a_person_is_never_overridden(
    service, manager
) -> None:
    sheet = await service.import_support(
        tactic(
            product_sku="3474",
            reference_cost=D("330000"),
            value_unit="caja",
            unit_basis_source="hoja",
            source_key="Familia:1",
        ),
        supplier_code="233",
    )
    person_set = await service.create(
        manager,
        tactic(
            product_sku="3474",
            reference_cost=D("13783"),
            value_unit="caja",
            customer_nit="900430148",
        ),
        supplier_code="233",
    )

    assert (sheet.issues, person_set.support.data.value_unit) == ([], "caja")
    assert person_set.support.data.unit_basis_source == "manual"


async def test_a_cost_that_matches_neither_unit_nor_box_stays_without_unit(
    service, manager
) -> None:
    view = await service.create(
        manager, tactic(product_sku="3474", reference_cost=D("60000")), supplier_code="233"
    )

    assert view.support.data.value_unit is None
    assert view.support.data.unit_basis_source == "desconocido"
    # The figures read an undeclared unit as per unit.
    assert view.derived.net_cost_per.unit == view.derived.net_cost


async def test_changing_the_unit_by_hand_records_the_provenance(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")

    updated = await service.update(
        manager, created.support.id, expected_version=1, changes={"value_unit": "unidad"}
    )

    assert updated.support.data.unit_basis_source == "manual"


async def test_only_the_three_classes_and_their_types_are_created(service, manager) -> None:
    with pytest.raises(InvalidInputError, match="ya no se crea"):
        await service.create(manager, tactic(kind="lista_especial"), supplier_code="233")
    with pytest.raises(InvalidInputError, match="admite estos tipos"):
        await service.create(
            manager,
            tactic(calc_method="puntos_margen", supplier_value=D("0.02")),
            supplier_code="233",
        )
    with pytest.raises(InvalidInputError, match="admite estos tipos"):
        await service.create(
            manager, tactic(kind="actividad_temporal", customer_nit=None), supplier_code="233"
        )
    created = await service.create(
        manager,
        tactic(calc_method="valor_precio_lista", supplier_value=D("250")),
        supplier_code="233",
    )
    assert created.derived.supplier_amount == D("250")


async def test_a_legacy_support_keeps_its_class_when_other_fields_change(
    service, repo, manager
) -> None:
    legacy = await service.import_support(
        tactic(kind="lista_especial", source_key="legacy:1"), supplier_code="233"
    )

    updated = await service.update(
        manager, legacy.support_id, expected_version=1, changes={"notes": "revisado"}
    )

    assert updated.support.data.kind == "lista_especial"
    with pytest.raises(InvalidInputError, match="ya no se crea"):
        await service.update(
            manager, legacy.support_id, expected_version=2, changes={"kind": "rebate"}
        )


async def test_a_support_can_be_switched_off_and_on_without_voiding_it(service, manager) -> None:
    created = await service.create(manager, tactic(), supplier_code="233")
    assert created.state == "vigente"

    off = await service.update(
        manager, created.support.id, expected_version=1, changes={"is_enabled": False}
    )
    assert (off.state, off.validity, off.support.data.status) == ("inactivo", "vigente", "activo")

    on = await service.update(
        manager, off.support.id, expected_version=2, changes={"is_enabled": True}
    )
    assert on.state == "vigente"
    voided = await service.void(manager, on.support.id, expected_version=3, reason="duplicado")
    assert voided.state == "anulado"  # anulado wins over everything else


async def test_base_search_combines_client_supplier_and_reference_in_any_subset(
    service, repo
) -> None:
    await service.search(
        customer_nits=["800.116.299"], supplier_codes=["233"], product_skus=["1128"]
    )
    group = repo.last_search.group
    base, free = group.items[:-1], group.items[-1]
    assert {(item.field, item.op) for item in base} == {
        ("supplier.code", "in"),
        ("product_sku", "in"),
    }
    # The client goes apart: the repository adds its business groups' supports.
    assert repo.last_search.customer_nits == ("800116299",)
    assert group.match == "all" and free.items == ()

    await service.search(
        supplier_codes=["233", " 399 "],
        conditions=[{"field": "kind", "op": "eq", "value": "rebate"}],
    )
    group = repo.last_search.group
    assert [item.field for item in group.items[:-1]] == ["supplier.code"]
    assert group.items[0].value == ["233", "399"]
    assert group.items[-1].items[0].field == "kind"  # free conditions still apply on top

    await service.search()
    assert repo.last_search.group.items == ()  # no base filter: nothing forced


async def test_refresh_updates_untouched_imports_but_never_manual_edits(
    service, repo, manager
) -> None:
    await service.import_support(tactic(notes="v1", source_key="S:1"), supplier_code="233")
    await service.import_support(tactic(notes="v1", source_key="S:2"), supplier_code="233")
    edited = next(item for item in repo.items.values() if item.data.source_key == "S:2")
    await service.update(manager, edited.id, expected_version=1, changes={"notes": "a mano"})

    plain = await service.import_support(tactic(notes="v2", source_key="S:1"), supplier_code="233")
    again = await service.import_support(
        tactic(notes="v2", source_key="S:1"), supplier_code="233", refresh=True
    )
    manual = await service.import_support(
        tactic(notes="v2", source_key="S:2"), supplier_code="233", refresh=True
    )

    by_key = {item.data.source_key: item for item in repo.items.values()}
    assert (plain.created, plain.updated) == (False, False)  # without refresh: skipped
    assert again.updated and by_key["S:1"].data.notes == "v2"
    assert manual.skipped_manual and by_key["S:2"].data.notes == "a mano"


async def test_a_refresh_dry_run_writes_nothing(service, repo) -> None:
    await service.import_support(tactic(notes="v1", source_key="S:1"), supplier_code="233")

    outcome = await service.import_support(
        tactic(notes="v2", source_key="S:1"), supplier_code="233", refresh=True, dry_run=True
    )

    assert outcome.updated
    assert next(iter(repo.items.values())).data.notes == "v1"


async def test_search_accepts_the_inactive_state(service, repo) -> None:
    await service.search(state="inactivo")

    assert repo.last_search.state == "inactivo"
