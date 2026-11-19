"""Free codes for a new price list (BI, 2026-11-19)."""

from decimal import Decimal

import pytest

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain.list_codes import TOTAL, free_codes
from tests.modules.quotations.conftest import NIT, branch
from tests.modules.supplier_supports.conftest import person


def test_the_free_codes_go_in_order_and_skip_the_used_ones() -> None:
    page = free_codes(["000", " 001", "00a", "1", "C01", None], None, 5)

    # Digits before letters; codes of 1 or 2 characters are not in the range.
    assert page.items == ("002", "003", "004", "005", "006")
    assert page.used == 4 and page.free == TOTAL - 4 == 46_652
    assert page.next_after == "006"
    after = free_codes(["00B"], "00a", 3)
    assert after.items == ("00C", "00D", "00E")
    last = free_codes([], "ZZX", 5)
    assert last.items == ("ZZY", "ZZZ") and last.next_after is None


async def test_validated_codes_are_taken_before_siesa_has_them(
    service, advisor, approver, warehouse
) -> None:
    warehouse.existing_lists = {"000", "001", "1"}
    page = await service.available_list_codes(approver, None, 3)
    assert page.items == ("002", "003", "004") and page.used == 2

    warehouse.branch_rows[NIT] = [branch(price_list_id="1", price_list_name="GENERAL")]
    quotation = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, quotation.id, 1, [LineChange("2537", Decimal("4100"))])
    await service.confirm(
        advisor, quotation.id, 2, list_action=None, new_list_name=None, activation_date=None
    )
    await service.validate(approver, quotation.id, 3, price_list_id="002")

    page = await service.available_list_codes(advisor, None, 3)
    assert page.items == ("003", "004", "005") and page.used == 3
    # Another creation cannot take it while SIESA does not have it yet.
    other = (await service.create(advisor, NIT, 12)).quotation
    await service.update_lines(advisor, other.id, 1, [LineChange("2537", Decimal("4100"))])
    await service.confirm(
        advisor, other.id, 2, list_action=None, new_list_name=None, activation_date=None
    )
    with pytest.raises(InvalidInputError, match="otra lista validada"):
        await service.validate(approver, other.id, 3, price_list_id="002")
    with pytest.raises(ForbiddenError):
        await service.available_list_codes(person("nadie@jdh.com.co"), None, 3)
    with pytest.raises(InvalidInputError):
        await service.available_list_codes(approver, None, 500)
