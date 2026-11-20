"""«Por vencer» and the counts by state of the supports master (2026-11-20)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import InvalidInputError
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.validity import expiring_until, is_expiring

# TODAY in the fixtures is 9 October 2026: «por vencer» runs to 9 November.


def tactic(sku: str, **values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "supplier_value": Decimal("0.05"),
        "customer_nit": "800116299",
        "product_sku": sku,
        "valid_from": date(2026, 7, 1),
        "valid_to": date(2026, 12, 31),
    }
    return SupportData(**{**base, **values})


@pytest.mark.parametrize(
    ("day", "until"),
    [
        (date(2026, 11, 20), date(2026, 12, 20)),
        (date(2026, 12, 15), date(2027, 1, 15)),
        (date(2027, 1, 31), date(2027, 2, 28)),
        (date(2028, 1, 30), date(2028, 2, 29)),
    ],
)
def test_por_vencer_is_one_calendar_month_out(day, until) -> None:
    assert expiring_until(day) == until


def test_only_a_support_in_force_is_por_vencer() -> None:
    day = date(2026, 11, 20)

    assert is_expiring(None, date(2026, 12, 20), day)
    assert not is_expiring(None, date(2026, 12, 21), day)
    assert not is_expiring(None, None, day)  # open-ended
    assert not is_expiring(date(2026, 12, 1), date(2026, 12, 10), day)  # programado
    assert not is_expiring(None, date(2026, 11, 19), day)  # vencido


async def test_the_counts_follow_the_search_whatever_the_state(service, repo, manager) -> None:
    soon = await service.create(
        manager, tactic("1128", valid_to=date(2026, 11, 9)), supplier_code="233"
    )

    def copy(**values):
        made = replace(soon.support, id=uuid4(), data=replace(soon.support.data, **values))
        repo.items[made.id] = made
        return made

    copy(valid_to=date(2026, 11, 10))
    copy(valid_from=date(2026, 11, 1), valid_to=None)
    copy(valid_to=date(2026, 9, 30))
    off = copy(is_enabled=False)

    counts = await service.summary(manager, state="vencido", page=3, sort=["-valid_to"])

    assert counts == {
        "vigente": 2,
        "por_vencer": 1,
        "programado": 1,
        "vencido": 1,
        "inactivo": 1,
        "total": 5,
    }
    # The rows stay vigente and say they are about to end.
    found = await service.get(soon.support.id)
    assert found.state == "vigente" and found.expiring
    assert not (await service.get(off.id)).expiring
    await service.search(manager, validity="por_vencer")
    assert repo.last_search.validity == "por_vencer"
    await service.search(manager, state="por_vencer")
    assert repo.last_search.state == "por_vencer"
    with pytest.raises(InvalidInputError, match="por_vencer"):
        await service.search(manager, validity="pronto")
