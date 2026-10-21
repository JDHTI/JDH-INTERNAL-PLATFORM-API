"""Read validation and who may curate suppliers."""

from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.application.service import CommercialMasterService
from app.modules.commercial_masters.domain.entities import CommercialSupplier, Page
from app.modules.identity.domain.principal import Principal


def principal(admin: bool = False) -> Principal:
    return Principal(uuid4(), "ana@jdh.com.co", "Ana", admin)


class Managers:
    async def is_manager(self, who: Principal) -> bool:
        return who.is_platform_admin


class Repo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.supplier = CommercialSupplier(
            uuid4(), "233", "FAMILIA SANCELA", True, 675, 241, None, None, None
        )

    async def list_customers(self, *args):
        self.calls.append(("customers", *args))
        return Page([], 0, args[-2], args[-1])

    async def list_commercial_suppliers(self, query, is_active, page, size):
        self.calls.append(("suppliers", query, is_active, page, size))
        return Page([self.supplier], 1, page, size)

    async def get_commercial_supplier(self, code):
        return self.supplier if code == "233" else None

    async def update_commercial_supplier(self, code, fields, nits, primary):
        self.calls.append(("update", code, dict(fields), nits, primary))
        return self.supplier if code == "233" else None


@pytest.fixture
def repo() -> Repo:
    return Repo()


@pytest.fixture
def service(repo) -> CommercialMasterService:
    return CommercialMasterService(repo, Managers())


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


async def test_unknown_commercial_supplier_is_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        await service.get_commercial_supplier("0")


async def test_only_managers_edit_the_list_margin(service, repo) -> None:
    with pytest.raises(ForbiddenError):
        await service.update_commercial_supplier(
            principal(),
            "233",
            fields=frozenset({"list_margin_pct"}),
            list_margin_pct=Decimal("0.18"),
        )

    await service.update_commercial_supplier(
        principal(admin=True),
        "233",
        fields=frozenset({"list_margin_pct", "list_margin_notes"}),
        list_margin_pct=Decimal("0.18"),
        list_margin_notes="  Aseo 20%  y Sal 18% ",
    )

    assert repo.calls[-1] == (
        "update",
        "233",
        {"list_margin_pct": Decimal("0.18"), "list_margin_notes": "Aseo 20% y Sal 18%"},
        None,
        None,
    )


async def test_omitted_fields_are_not_touched(service, repo) -> None:
    await service.update_commercial_supplier(
        principal(admin=True),
        "233",
        fields=frozenset({"list_margin_notes"}),
        list_margin_notes=None,
    )

    assert repo.calls[-1][2] == {"list_margin_notes": None}


async def test_list_margin_must_be_a_fraction(service) -> None:
    with pytest.raises(InvalidInputError, match="entre 0 y 1"):
        await service.update_commercial_supplier(
            principal(admin=True),
            "233",
            fields=frozenset({"list_margin_pct"}),
            list_margin_pct=Decimal("18"),
        )


async def test_primary_nit_must_be_among_the_confirmed_ones(service, repo) -> None:
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
    assert repo.calls[-1][3:] == (["890900161", "860002518"], "860002518")


async def test_updating_an_unknown_supplier_is_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        await service.update_commercial_supplier(
            principal(admin=True), "0", fields=frozenset({"list_margin_notes"})
        )
