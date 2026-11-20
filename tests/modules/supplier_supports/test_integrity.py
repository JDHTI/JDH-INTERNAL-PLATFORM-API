"""Rules v2 integrity: supplier SKU to JDH SKU, no crossing supports, mass void and expiry."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.overlap import dates_cross, same_scope

D = Decimal


def tactic(**values) -> SupportData:
    base = {
        "value_unit": "unidad",
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "supplier_value": D("0.05"),
        "customer_nit": "800116299",
        "product_sku": "1128",
        "valid_from": date(2026, 7, 1),
        "valid_to": date(2026, 12, 31),
    }
    return SupportData(**{**base, **values})


async def test_the_jdh_sku_comes_from_the_supplier_sku(service, manager) -> None:
    created = await service.create(
        manager, tactic(product_sku=None, supplier_sku="72163"), supplier_code="233"
    )

    assert created.support.data.product_sku == "1128"
    assert created.support.data.product_id is not None


@pytest.mark.parametrize(("code", "fragment"), [("00000", "no está"), ("99999", "varias")])
async def test_an_unknown_or_ambiguous_supplier_sku_is_refused(
    service, manager, code, fragment
) -> None:
    with pytest.raises(InvalidInputError, match=fragment):
        await service.create(
            manager, tactic(product_sku=None, supplier_sku=code), supplier_code="233"
        )


async def test_a_crossing_support_is_refused_with_the_list(service, manager) -> None:
    first = await service.create(manager, tactic(), supplier_code="233")

    with pytest.raises(ConflictError) as raised:
        await service.create(
            manager, tactic(valid_from=date(2026, 10, 1), valid_to=None), supplier_code="233"
        )

    listed = raised.value.extra["overlaps"]
    assert [item["id"] for item in listed] == [str(first.support.id)]
    # Another class, another customer or validities that do not touch never cross.
    await service.create(
        manager,
        tactic(kind="actividad_temporal", customer_nit=None),
        supplier_code="233",
    )
    await service.create(manager, tactic(customer_nit="900430148"), supplier_code="233")
    await service.create(
        manager, tactic(valid_from=date(2027, 1, 1), valid_to=None), supplier_code="233"
    )


async def test_expire_the_previous_ends_it_the_day_before(service, repo, manager) -> None:
    first = await service.create(manager, tactic(), supplier_code="233")

    await service.create(
        manager,
        tactic(valid_from=date(2026, 10, 1), valid_to=None),
        supplier_code="233",
        on_overlap="vencer_anterior",
        overlap_reason="Nueva negociación",
    )

    previous = repo.items[first.support.id]
    assert previous.data.valid_to == date(2026, 9, 30) and previous.data.status == "activo"
    assert repo.traces[first.support.id].origin == "vencimiento"


async def test_a_previous_that_starts_later_is_voided(service, repo, manager) -> None:
    later = await service.create(
        manager, tactic(valid_from=date(2026, 11, 1), valid_to=None), supplier_code="233"
    )

    await service.create(manager, tactic(), supplier_code="233", on_overlap="vencer_anterior")

    assert repo.items[later.support.id].data.status == "anulado"


async def test_editing_into_a_crossing_needs_a_decision(service, repo, manager) -> None:
    first = await service.create(manager, tactic(), supplier_code="233")
    second = await service.create(
        manager, tactic(valid_from=date(2027, 1, 1), valid_to=None), supplier_code="233"
    )
    # A note never checks crossings.
    await service.update(manager, second.support.id, expected_version=1, changes={"notes": "x"})

    with pytest.raises(ConflictError):
        await service.update(
            manager,
            second.support.id,
            expected_version=2,
            changes={"valid_from": date(2026, 12, 1)},
        )
    await service.update(
        manager,
        second.support.id,
        expected_version=2,
        changes={"valid_from": date(2026, 12, 1)},
        on_overlap="anular_anterior",
    )
    assert repo.items[first.support.id].data.status == "anulado"


async def test_two_rows_of_one_batch_that_cross_are_refused(service, manager) -> None:
    with pytest.raises(InvalidInputError, match="mismo envío"):
        await service.create_many(manager, [(tactic(), "233"), (tactic(), "233")])


async def test_mass_void_and_expiry_keep_the_observation(service, repo, manager) -> None:
    one = await service.create(manager, tactic(), supplier_code="233")
    two = await service.create(manager, tactic(product_sku="8749"), supplier_code="233")
    programmed = await service.create(
        manager,
        tactic(product_sku="3474", valid_from=date(2027, 1, 1), valid_to=None),
        supplier_code="233",
    )

    expired = await service.retire_many(
        manager,
        "vencer",
        "Fin de la negociación",
        items=[(one.support.id, 1), (programmed.support.id, None)],
    )
    voided = await service.retire_many(
        manager, "anular", "Duplicado", criteria={"product_skus": ["8749"]}
    )

    assert repo.items[one.support.id].data.valid_to == date(2026, 10, 8)  # yesterday
    assert repo.items[programmed.support.id].data.status == "anulado"  # it had not started
    assert {item.id for item in expired} == {one.support.id, programmed.support.id}
    assert [item.id for item in voided] == [two.support.id]
    assert repo.items[two.support.id].void_reason == "Duplicado"
    with pytest.raises(InvalidInputError, match="observación"):
        await service.retire_many(manager, "anular", "  ", items=[(one.support.id, None)])
    with pytest.raises(ConflictError):
        await service.retire_many(manager, "anular", "otra vez", items=[(two.support.id, None)])


async def test_a_business_group_becomes_one_support_per_customer(service, manager) -> None:
    created = await service.create_for_group(
        manager, tactic(customer_nit=None, customer_group="Grupo Goddard"), supplier_code="233"
    )

    assert {view.support.data.customer_nit for view in created} == {"900430148", "830012863"}
    assert {view.business_group for view in created} == {"Grupo Goddard"}
    assert all(view.support.data.group_wide for view in created)
    assert await service.business_groups("godd") == [("GRUPO GODDARD", 2)]
    with pytest.raises(InvalidInputError, match="no tiene clientes"):
        await service.create_for_group(
            manager, tactic(customer_nit=None, customer_group="Nadie"), supplier_code="233"
        )
    with pytest.raises(InvalidInputError, match="NIT vacío"):
        await service.create_for_group(
            manager, tactic(customer_group="Grupo Goddard"), supplier_code="233"
        )


def test_scope_and_dates_of_a_crossing() -> None:
    base = tactic()

    assert same_scope(base, tactic(valid_from=None))
    # Same customer and SKU: a support born from a business group still crosses.
    assert same_scope(base, tactic(customer_group="GRUPO GODDARD"))
    assert same_scope(base, tactic(supplier_sku="72163"))  # the JDH SKU decides
    group_only = tactic(customer_nit=None, customer_group="A")
    assert not same_scope(group_only, tactic(customer_nit=None, customer_group="B"))
    assert not same_scope(base, tactic(customer_branch_id="002"))
    assert not same_scope(base, tactic(regionals=("ANTIOQUIA",)))
    assert dates_cross(base, tactic(valid_from=date(2026, 12, 31), valid_to=None))
    assert not dates_cross(base, tactic(valid_from=date(2027, 1, 1), valid_to=None))
