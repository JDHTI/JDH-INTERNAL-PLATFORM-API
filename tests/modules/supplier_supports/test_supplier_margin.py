"""% Margen Garantizado with the supplier's minimum margin, and the supplier margin request
asked from a quotation line (2026-11-23)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.supplier_supports.application.internal_requests import (
    InternalRequestLinks,
    InternalRequestService,
)
from app.modules.supplier_supports.application.service import FieldError
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.presentation.internal_requests_router import _response
from tests.modules.supplier_supports.conftest import FakeLookup, FakeManagers, FixedCalendar
from tests.modules.supplier_supports.test_internal_requests import (
    QUOTATION_ID,
    QUOTER,
    REVIEWER,
    FakeAccess,
    FakeRepository,
    line,
)

D = Decimal


def guarantee(**values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "precio_neto",
        "value_unit": "unidad",
        "target_margin_pct": D("0.18"),
        "supplier_min_margin_pct": D("0.13"),
        "product_sku": "1128",
        "customer_nit": "800116299",
        "valid_from": None,
        "valid_to": None,
    }
    return SupportData(**{**base, **values})


# ------------------------------------------------------------------ the master


async def test_a_guaranteed_margin_is_created_with_the_supplier_minimum(service, manager) -> None:
    view = await service.create(manager, guarantee(), supplier_code="233")

    assert view.support.data.supplier_min_margin_pct == D("0.13")
    with pytest.raises(FieldError, match="Margen mínimo garantizado proveedor") as raised:
        await service.create(
            manager, guarantee(supplier_min_margin_pct=None, customer_nit=None), supplier_code="233"
        )
    assert raised.value.field == "supplier_min_margin_pct"


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (D("0.18"), "menor que el «Margen garantizado»"),
        (D("0.25"), "menor que el «Margen garantizado»"),
        (D("-1"), "entre -100 % y 100 %"),
    ],
)
async def test_the_supplier_minimum_stays_under_the_guarantee(
    service, manager, value, fragment
) -> None:
    with pytest.raises(FieldError, match=fragment):
        await service.create(manager, guarantee(supplier_min_margin_pct=value), supplier_code="233")


async def test_the_supplier_minimum_may_be_zero_or_negative(service, manager) -> None:
    zero = await service.create(
        manager, guarantee(supplier_min_margin_pct=D("0")), supplier_code="233"
    )
    negative = await service.create(
        manager,
        guarantee(supplier_min_margin_pct=D("-0.05"), customer_nit="900430148"),
        supplier_code="233",
    )

    assert zero.support.data.supplier_min_margin_pct == D("0")
    assert negative.support.data.supplier_min_margin_pct == D("-0.05")


# ------------------------------------------------------------------ the supplier request


@pytest.fixture
async def base(service, manager):
    """The general guarantee of SKU 1128 (no customer): 18 % from a 13 % minimum."""

    return (
        await service.create(manager, guarantee(customer_nit=None), supplier_code="233")
    ).support


@pytest.fixture
def access(base) -> FakeAccess:
    covered = FakeAccess()
    guaranteed = replace(
        line("1128"),
        support_id=base.id,
        support_calc_method="precio_neto",
        supplier_min_margin_pct=D("0.13"),
    )
    covered.current = replace(covered.current, lines={**covered.current.lines, "1128": guaranteed})
    return covered


@pytest.fixture
def requests_repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def supplier_requests(requests_repo, access, repo) -> InternalRequestService:
    links = InternalRequestLinks(requests_repo, FakeManagers([REVIEWER.email]), FakeManagers([]))
    return InternalRequestService(
        requests_repo, links, access, FakeLookup(), FixedCalendar(), supports=repo
    )


async def ask(service, margin: str, sku: str = "1128"):
    return await service.create(
        QUOTER,
        QUOTATION_ID,
        kind="proveedor",
        version=7,
        supplier_code="233",
        requested_for=None,
        agreed_with="Gerente Familia",
        reason="Cotización puntual del vendedor",
        items=[(sku, D(margin))],
    )


async def approve(service, request, margin: str, decision: str = "unica_vez"):
    return await service.approve(
        REVIEWER,
        request.id,
        version=request.version,
        decision=decision,
        valid_from=None,
        valid_to=None,
        note="Autorizado por el proveedor",
        items=[("1128", D(margin))],
    )


async def test_a_supplier_request_asks_a_lower_minimum_from_the_line(
    supplier_requests, base
) -> None:
    request = await ask(supplier_requests, "-0.05")

    assert request.kind == "proveedor"
    item = request.items[0]
    # The minimum in force when it was asked, and the guarantee it lowers.
    assert item.target_margin_pct == D("0.13") and item.base_support_id == base.id
    assert item.requested_margin_pct == D("-0.050000")
    shown = _response(request)
    assert shown.kind == "proveedor"
    assert shown.kind_label == "Solicitud de margen proveedor FAMILIA SANCELA"


@pytest.mark.parametrize(
    ("margin", "sku", "fragment"),
    [
        ("0.13", "1128", "menor que el margen mínimo del proveedor"),
        ("0.20", "1128", "menor que el margen mínimo del proveedor"),
        ("0.10", "8749", "no tiene un % Margen Garantizado"),
    ],
)
async def test_a_supplier_request_needs_a_guarantee_and_a_lower_margin(
    supplier_requests, margin, sku, fragment
) -> None:
    with pytest.raises(InvalidInputError, match=fragment):
        await ask(supplier_requests, margin, sku)


async def test_approving_copies_the_guarantee_for_the_customer_once(
    supplier_requests, requests_repo, base, repo
) -> None:
    request = await ask(supplier_requests, "0.07")

    approved = await approve(supplier_requests, request, "0.07")

    assert approved.status == "aprobada" and approved.decision == "unica_vez"
    copy = requests_repo.approvals[request.id].supports["1128"]
    # The same guarantee, for this customer, from the approved minimum, on the order's day.
    assert copy.calc_method == "precio_neto" and copy.target_margin_pct == D("0.18")
    assert copy.supplier_min_margin_pct == D("0.070000")
    assert copy.customer_nit == "800116299" and copy.product_sku == "1128"
    assert copy.valid_from == copy.valid_to == request.requested_for
    assert copy.program.startswith("Solicitud de margen proveedor #")
    # The base guarantee keeps its 13 % for everyone else.
    assert (await repo.get(base.id)).data.supplier_min_margin_pct == D("0.13")


async def test_the_approved_minimum_stays_under_the_one_asked_against(
    supplier_requests, requests_repo, base, service, manager
) -> None:
    request = await ask(supplier_requests, "0.07")

    with pytest.raises(InvalidInputError, match="entre -100 % y el mínimo del proveedor"):
        await approve(supplier_requests, request, "0.13")
    # A base guarantee voided meanwhile cannot be copied.
    await service.void(manager, base.id, expected_version=base.version, reason="Ya no aplica")
    with pytest.raises(ConflictError, match="ya no existe o cambió"):
        await approve(supplier_requests, request, "0.05")
