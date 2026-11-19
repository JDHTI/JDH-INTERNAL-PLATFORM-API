"""Internal support requests from a customer quotation, over in-memory ports."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.supplier_supports.application.internal_requests import (
    InternalRequestLinks,
    InternalRequestService,
)
from app.modules.supplier_supports.domain.internal_requests import (
    APPROVED,
    PENDING,
    REJECTED,
    InternalRequest,
    QuotedCustomer,
    QuotedLine,
    RequestRef,
)
from app.modules.supplier_supports.presentation.internal_requests_router import _response
from tests.modules.supplier_supports.conftest import (
    FAMILIA_ID,
    FakeLookup,
    FakeManagers,
    FixedCalendar,
    person,
)

D = Decimal
QUOTER = person("ventas@jdh.com.co")
REVIEWER = person("bi@jdh.com.co")
DESIGNATED = person("jefe@jdh.com.co")
QUOTATION_ID = uuid4()


def line(sku: str, supplier: str = "233") -> QuotedLine:
    return QuotedLine(
        sku=sku,
        description=f"Producto {sku}",
        unit="UND",
        supplier_code=supplier,
        supplier_name="FAMILIA SANCELA",
        cost=D("80"),
        quoted_price=D("95"),
        margin_pct=D("0.1579"),
        min_margin_pct=D("0.18"),
    )


class FakeAccess:
    def __init__(self) -> None:
        self.quoters = {QUOTER.email, REVIEWER.email}
        self.current = QuotedCustomer(
            id=QUOTATION_ID,
            kind="cotizacion",
            version=7,
            label="Cotización Limpieza · 2026-10-09",
            customer_nit="800116299",
            customer_name="LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS",
            lines={"1128": line("1128"), "8749": line("8749"), "1001": line("1001", "399")},
        )

    async def quotation(self, principal, quotation_id):
        if quotation_id != self.current.id:
            raise NotFoundError("Cotización no encontrada")
        return self.current

    async def can_quote(self, principal) -> bool:
        return principal.email in self.quoters


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, InternalRequest] = {}
        self.approvals: dict[UUID, object] = {}

    async def create(self, request, actor_id, actor, now):
        pending = {
            item.sku
            for stored in self.items.values()
            if stored.quotation_id == request.quotation_id and stored.status == PENDING
            for item in stored.items
        }
        if pending & {item.sku for item in request.items}:
            raise ConflictError("pendiente")
        stored = InternalRequest(
            id=uuid4(),
            number=len(self.items) + 1,
            status=PENDING,
            decision=None,
            quotation_id=request.quotation_id,
            quotation_label=request.quotation_label,
            customer_nit=request.customer_nit,
            customer_name=request.customer_name,
            commercial_supplier_id=request.commercial_supplier_id,
            supplier_code=request.supplier_code,
            supplier_name=request.supplier_name,
            requested_for=request.requested_for,
            agreed_with=request.agreed_with,
            reason=request.reason,
            requested_by_id=actor_id,
            requested_by=actor,
            created_at=now,
            version=1,
            valid_from=None,
            valid_to=None,
            decided_by=None,
            decided_at=None,
            decision_reason=None,
            items=request.items,
        )
        self.items[stored.id] = stored
        return stored

    async def get(self, request_id):
        return self.items.get(request_id)

    async def search(self, *, status, q, owner_id, page, size):
        found = [
            item
            for item in self.items.values()
            if (owner_id is None or item.requested_by_id == owner_id)
            and (status is None or item.status == status)
        ]
        return found, len(found)

    def _check(self, request_id, version):
        stored = self.items[request_id]
        if stored.version != version:
            raise ConflictError("versión")
        if stored.status != PENDING:
            raise ConflictError("resuelta")
        return stored

    async def approve(self, request_id, version, approval, actor, now):
        stored = self._check(request_id, version)
        self.approvals[request_id] = approval
        items = tuple(
            replace(
                item,
                approved_margin_pct=approval.margins.get(item.sku),
                support_id=uuid4() if item.sku in approval.margins else None,
            )
            for item in stored.items
        )
        saved = replace(
            stored,
            status=APPROVED,
            decision=approval.decision,
            valid_from=approval.valid_from,
            valid_to=approval.valid_to,
            decided_by=actor,
            decided_at=now,
            decision_reason=approval.note,
            version=stored.version + 1,
            items=items,
        )
        self.items[request_id] = saved
        return saved

    async def reject(self, request_id, version, reason, actor, now):
        stored = self._check(request_id, version)
        saved = replace(
            stored, status=REJECTED, decision_reason=reason, decided_by=actor, version=2
        )
        self.items[request_id] = saved
        return saved

    async def latest_for_quotation(self, quotation_id):
        refs: dict[str, RequestRef] = {}
        for stored in sorted(self.items.values(), key=lambda item: item.number):
            for item in stored.items:
                refs[item.sku] = RequestRef(
                    stored.id, stored.status, stored.decision, item.approved_margin_pct
                )
        return refs


@pytest.fixture
def repo() -> FakeRepository:  # noqa: F811 - overrides the supports repo fixture here
    return FakeRepository()


@pytest.fixture
def access() -> FakeAccess:
    return FakeAccess()


@pytest.fixture
def requests_service(repo, access) -> InternalRequestService:
    links = InternalRequestLinks(
        repo, FakeManagers([REVIEWER.email]), FakeManagers([DESIGNATED.email])
    )
    return InternalRequestService(repo, links, access, FakeLookup(), FixedCalendar())


async def ask(service, **changes):
    values = {
        "version": 7,
        "supplier_code": "233",
        "requested_for": None,
        "agreed_with": "Gerente Familia",
        "reason": "Cliente negoció el 16 %",
        "items": [("1128", D("0.16")), ("8749", D("0.15"))],
    }
    values.update(changes)
    return await service.create(QUOTER, QUOTATION_ID, **values)


async def test_who_asks_and_who_decides(requests_service, access) -> None:
    assert await requests_service.permissions(QUOTER) == {
        "can_review_internal": False,
        "can_request_internal": True,
    }
    assert (await requests_service.permissions(REVIEWER))["can_review_internal"]
    assert (await requests_service.permissions(DESIGNATED))["can_review_internal"]
    # An approver who quotes asks too (2026-11-19), but never decides their own request.
    own = await requests_service.create(
        REVIEWER,
        QUOTATION_ID,
        version=7,
        supplier_code="233",
        requested_for=None,
        agreed_with="x",
        reason="y",
        items=[("1128", D("0.1"))],
    )
    decision = {
        "version": 1,
        "decision": "persistente",
        "valid_from": None,
        "valid_to": None,
        "note": None,
        "items": [("1128", D("0.1"))],
    }
    with pytest.raises(ForbiddenError, match="tu propia solicitud"):
        await requests_service.approve(REVIEWER, own.id, **decision)
    assert (await requests_service.approve(DESIGNATED, own.id, **decision)).status == APPROVED
    with pytest.raises(ForbiddenError):  # does not quote
        await requests_service.create(
            person("otro@jdh.com.co"),
            QUOTATION_ID,
            version=7,
            supplier_code="233",
            requested_for=None,
            agreed_with="x",
            reason="y",
            items=[("1128", D("0.1"))],
        )


async def test_a_request_takes_the_quotation_line_and_the_order_date(requests_service) -> None:
    request = await ask(requests_service)

    assert request.status == PENDING and request.customer_nit == "800116299"
    assert request.commercial_supplier_id == FAMILIA_ID
    assert request.requested_for == date(2026, 10, 9)  # today (FixedCalendar)
    first = request.items[0]
    assert (first.sku, first.cost, first.quoted_price, first.min_margin_pct) == (
        "1128",
        D("80"),
        D("95"),
        D("0.18"),
    )
    assert first.requested_margin_pct == D("0.160000")
    # The UI hides «Aprobar» on one's own request by this id.
    assert _response(request).requested_by_id == QUOTER.user_id


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"version": 6}, ConflictError),
        ({"items": [("1001", D("0.1"))]}, InvalidInputError),  # another supplier
        ({"items": [("9999", D("0.1"))]}, InvalidInputError),  # not in the quotation
        ({"items": [("1128", D("1.2"))]}, InvalidInputError),  # not a fraction
        ({"items": [("1128", D("0.1")), ("1128", D("0.2"))]}, InvalidInputError),
        ({"reason": "  "}, InvalidInputError),
    ],
)
async def test_a_bad_request_is_refused(requests_service, changes, error) -> None:
    with pytest.raises(error):
        await ask(requests_service, **changes)


async def test_only_customer_quotations_and_one_pending_per_sku(requests_service, access) -> None:
    await ask(requests_service)
    with pytest.raises(ConflictError):
        await ask(requests_service, items=[("1128", D("0.10"))])

    access.current = replace(access.current, kind="lista")
    with pytest.raises(InvalidInputError, match="cotización de cliente"):
        await ask(requests_service, items=[("8749", D("0.1"))])


async def test_approve_for_good_creates_margin_exceptions_for_the_kept_skus(
    requests_service, repo
) -> None:
    request = await ask(requests_service)

    approved = await requests_service.approve(
        REVIEWER,
        request.id,
        version=1,
        decision="persistente",
        valid_from=None,
        valid_to=date(2027, 3, 31),
        note=None,
        items=[("1128", D("0.14"))],  # 8749 left out: not approved
    )

    assert approved.status == APPROVED and approved.decision == "persistente"
    assert approved.valid_from == date(2026, 10, 9) and approved.valid_to == date(2027, 3, 31)
    kept, left = approved.items
    assert kept.approved_margin_pct == D("0.140000") and kept.support_id is not None
    assert left.approved_margin_pct is None and left.support_id is None
    data = repo.approvals[request.id].supports["1128"]
    assert (data.kind, data.calc_method) == ("excepcion_margen", "margen_minimo")
    assert data.min_margin_pct == data.supported_margin_pct == D("0.140000")
    assert data.customer_nit == "800116299" and data.commercial_supplier_id == FAMILIA_ID
    assert data.program == f"Solicitud de apoyo interno #{request.number}"
    assert data.product_id is not None and data.authorized_by == REVIEWER.email


async def test_approve_once_is_valid_only_on_the_order_date(requests_service, repo) -> None:
    request = await ask(requests_service, requested_for=date(2026, 10, 8))

    with pytest.raises(InvalidInputError, match="nota"):
        await requests_service.approve(
            DESIGNATED,
            request.id,
            version=1,
            decision="unica_vez",
            valid_from=None,
            valid_to=None,
            note=None,
            items=[("1128", D("0.12"))],
        )
    approved = await requests_service.approve(
        DESIGNATED,
        request.id,
        version=1,
        decision="unica_vez",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        note="Pedido puntual",
        items=[("1128", D("0.12"))],
    )

    # The order's date, not the approval's: yesterday's order still passes.
    assert approved.valid_from == approved.valid_to == date(2026, 10, 8)
    data = repo.approvals[request.id].supports["1128"]
    assert data.valid_from == data.valid_to == date(2026, 10, 8)
    assert "Única vez" in (data.conditions or "")


async def test_reject_and_who_may_decide_or_read(requests_service) -> None:
    request = await ask(requests_service)

    with pytest.raises(ForbiddenError):
        await requests_service.reject(QUOTER, request.id, version=1, reason="No")
    with pytest.raises(InvalidInputError):
        await requests_service.reject(REVIEWER, request.id, version=1, reason=" ")
    rejected = await requests_service.reject(REVIEWER, request.id, version=1, reason="Sin margen")
    assert rejected.status == REJECTED

    with pytest.raises(ForbiddenError):  # every request: reviewers only
        await requests_service.search(QUOTER, mine=False)
    mine, total = await requests_service.search(QUOTER, mine=True)
    assert total == 1 and mine[0].id == request.id
    with pytest.raises(NotFoundError):  # someone else's
        await requests_service.get(person("otro@jdh.com.co"), request.id)
    with pytest.raises(ConflictError):  # already decided
        await requests_service.approve(
            REVIEWER,
            request.id,
            version=2,
            decision="persistente",
            valid_from=None,
            valid_to=None,
            note=None,
            items=[("1128", D("0.1"))],
        )


async def test_the_quotation_line_shows_the_latest_request(requests_service, repo) -> None:
    request = await ask(requests_service)
    await requests_service.approve(
        REVIEWER,
        request.id,
        version=1,
        decision="persistente",
        valid_from=None,
        valid_to=None,
        note=None,
        items=[("1128", D("0.14"))],
    )

    refs = await requests_service.links.latest_for(QUOTATION_ID)

    assert refs["1128"].status == APPROVED and refs["1128"].approved_margin_pct == D("0.140000")
    assert refs["8749"].approved_margin_pct is None


PANEL = person("comercial@jdh.com.co")


@pytest.fixture
def panel_service(repo, access) -> InternalRequestService:
    links = InternalRequestLinks(
        repo, FakeManagers([REVIEWER.email]), FakeManagers([DESIGNATED.email])
    )
    return InternalRequestService(
        repo, links, access, FakeLookup(), FixedCalendar(), FakeManagers([PANEL.email])
    )


async def test_a_request_from_a_quotation_asks_less_than_the_current_margin(
    requests_service,
) -> None:
    request = await ask(requests_service, items=[("1128", D("0.16"))])

    # The product's Food service margin for that customer, kept with the item.
    assert request.items[0].target_margin_pct == D("0.18")
    for same_or_more in (D("0.18"), D("0.19")):
        with pytest.raises(
            InvalidInputError, match=r"debe ser menor que su margen actual \(18,00 %"
        ):
            await ask(requests_service, items=[("8749", same_or_more)])


async def test_the_panel_asks_by_customer_and_sku(panel_service) -> None:
    nit, name, targets = await panel_service.targets(PANEL, "800116299-6", ["1128", "9999"])
    assert (nit, [item.sku for item in targets]) == ("800116299", ["1128"])
    assert targets[0].margin_pct == D("0.18") and name

    request = await panel_service.create_direct(
        PANEL,
        customer_nit="800116299",
        requested_for=None,
        agreed_with="Gerente Familia",
        reason="Licitación",
        items=[("1128", D("0.15"))],
    )

    assert request.quotation_id is None and request.status == PENDING
    assert (request.supplier_code, request.commercial_supplier_id) == ("233", FAMILIA_ID)
    assert request.items[0].target_margin_pct == D("0.18")
    assert request.items[0].description


@pytest.mark.parametrize(
    ("nit", "items", "message"),
    [
        ("800116299", [("1128", D("0.18"))], "debe ser menor"),  # same as the current one
        ("900430148", [("1128", D("0.12"))], "Margen Mayoristas"),  # a wholesale customer: 10 %
        ("800116299", [("1128", D("0.1")), ("8749", D("0.1"))], "mismo proveedor"),
        ("800116299", [("9999", D("0.1"))], "Referencia JDH desconocida"),
        ("800116299", [("1001", D("0.1"))], "no tiene Margen Food Service"),
        ("111", [("1128", D("0.1"))], "NIT de cliente desconocido"),
    ],
)
async def test_the_panel_refuses_a_margin_not_under_the_current_one(
    panel_service, nit, items, message
) -> None:
    with pytest.raises(InvalidInputError, match=message):
        await panel_service.create_direct(
            PANEL,
            customer_nit=nit,
            requested_for=None,
            agreed_with="x",
            reason="y",
            items=items,
        )


async def test_who_asks_from_the_panel(panel_service) -> None:
    assert await panel_service.can_request_direct(PANEL)
    assert await panel_service.can_request_direct(QUOTER)  # whoever quotes asks here too
    # An approver who quotes asks too (2026-11-19); they never decide their own request.
    assert await panel_service.can_request_direct(REVIEWER)
    # The panel's button follows the same rule: Comercial asks without using the quoter.
    assert (await panel_service.permissions(PANEL))["can_request_internal"]
    assert (await panel_service.permissions(QUOTER))["can_request_internal"]
    assert await panel_service.permissions(REVIEWER) == {
        "can_review_internal": True,
        "can_request_internal": True,
    }
    with pytest.raises(ForbiddenError):
        await panel_service.targets(person("otro@jdh.com.co"), "800116299", ["1128"])
