"""The quoter's side of the internal support requests: who may ask, and each line's request."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from app.modules.quotations.presentation.router import _detail
from tests.modules.quotations.conftest import APPROVER, NIT


@dataclass(frozen=True)
class Ref:
    id: UUID
    status: str
    decision: str | None
    approved_margin_pct: Decimal | None


class FakeLinks:
    def __init__(self, refs) -> None:
        self.refs = refs
        self.asked: list[UUID] = []

    async def is_reviewer(self, principal) -> bool:
        return principal.email == APPROVER

    async def latest_for(self, quotation_id):
        self.asked.append(quotation_id)
        return self.refs


async def test_whoever_quotes_may_ask(service, advisor, approver) -> None:
    assert not (await service.permissions(advisor)).can_request_support  # nothing wired
    service.support_requests = FakeLinks({})

    assert (await service.permissions(advisor)).can_request_support
    # BI asks too (2026-11-19); it never approves its own request.
    assert (await service.permissions(approver)).can_request_support
    assert not (await service.permissions(advisor, "lista")).can_request_support


async def test_each_line_shows_its_latest_request(service, advisor) -> None:
    detail = await service.create(advisor, NIT, 6)
    sku = detail.lines[0].sku
    ref = Ref(uuid4(), "aprobada", "unica_vez", Decimal("0.14"))
    links = FakeLinks({sku: ref})
    service.support_requests = links

    response = _detail(detail, await service.support_requests_for(detail))

    line = next(item for item in response.lines if item.sku == sku)
    assert line.support_request is not None
    assert (line.support_request.status, line.support_request.decision) == ("aprobada", "unica_vez")
    assert line.support_request.approved_margin_pct == Decimal("0.14")
    assert links.asked == [detail.quotation.id]
    # A broken supports side never breaks the quotation.
    links.latest_for = None  # type: ignore[assignment]
    assert await service.support_requests_for(detail) == {}
