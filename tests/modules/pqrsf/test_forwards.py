import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
)
from app.modules.mail.layout import BrandedEmail, render_html
from app.modules.pqrsf.application.forwards import CustomerForwardService
from app.modules.pqrsf.application.notification_templates import AUTOMATIC_NOTICE
from app.modules.pqrsf.application.notifications import NotificationService
from app.modules.pqrsf.domain.email import CaseAttachment, EmailMessage
from app.modules.pqrsf.domain.notifications import CUSTOMER_FORWARD
from app.modules.pqrsf.domain.tracking import CaseEvent
from tests.modules.pqrsf.test_notifications import FakeHolidays, FakeOutbox, FakeTeam, OpenCases
from tests.modules.pqrsf.test_tracking import Setup

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
SUPPLIER = "calidad@proveedor.co"


class FakeEmails:
    def __init__(self) -> None:
        self.messages: dict[uuid.UUID, EmailMessage] = {}
        self.attachments: list[CaseAttachment] = []

    async def get_message_by_id(self, message_id):
        return self.messages.get(message_id)

    async def list_attachments(self, case_id):
        return [a for a in self.attachments if a.case_id == case_id]

    async def get_attachments(self, case_id, ids):
        return [a for a in self.attachments if a.case_id == case_id and a.id in ids]


class World:
    def __init__(self, noreply: str = "no-reply@jdh.com.co") -> None:
        self.s = Setup()
        self.s.service.customer_forwarding = bool(noreply)
        self.emails = FakeEmails()
        self.outbox = FakeOutbox()
        self.service = CustomerForwardService(
            tracking=self.s.service,
            emails=self.emails,
            notifications=self.outbox,
            noreply_sender=noreply,
            mailbox="pqrsf@jdh.com.co",
            clock=lambda: NOW,
        )

    async def case_with_answers(self, requester_email="cliente@x.co"):
        view = await self.s.service.create_case(self.s.command(), self.s.seller)
        case = self.s.cases.cases[view.case.id]
        case.requester_email, case.requester_name = requester_email, "Ana Cliente"
        # The case wrote to the supplier, so its answers read as the supplier's.
        self.s.cases.events.append(
            CaseEvent(
                uuid.uuid4(),
                case.id,
                "supplier_message",
                self.s.admin.user_id,
                None,
                None,
                "¿Qué pasó con el lote?",
                {"to": [SUPPLIER]},
                NOW,
            )
        )
        supplier = await self._received(case.id, SUPPLIER, "Revisamos el lote 123.\n\nProcede.")
        customer = await self._received(case.id, "cliente@x.co", "¿Alguna novedad?")
        return case, supplier, customer

    async def _received(self, case_id, sender, body):
        message = EmailMessage(
            id=uuid.uuid4(),
            internet_message_id=f"<{uuid.uuid4()}@x>",
            graph_id="g",
            conversation_id=None,
            mailbox="pqrsf@jdh.com.co",
            sender_address=sender,
            sender_name=None,
            subject="RE: caso",
            body_text=f"{body}\n\nEl lun, 9 nov 2026 escribió:\n> texto viejo",
            received_at=NOW,
            status="threaded",
            case_id=case_id,
        )
        self.emails.messages[message.id] = message
        self.emails.attachments.append(
            CaseAttachment(
                uuid.uuid4(),
                case_id,
                message.id,
                f"de-{sender}.pdf",
                "pdf",
                "application/pdf",
                10,
                "x",
                "k",
                NOW,
            )
        )
        await self.s.service.record_email(case_id, message.id, sender, body[:50], {})
        return next(
            e
            for e in reversed(self.s.cases.events)
            if e.event_type == "email_received" and e.data["message_id"] == str(message.id)
        )


def _events(detail):
    return {view.event.id: view for view in detail.events}


@pytest.mark.asyncio
async def test_the_timeline_says_who_wrote_and_what_can_go_to_the_customer() -> None:
    w = World()
    case, supplier, customer = await w.case_with_answers()

    views = _events(await w.s.service.get_case(case.id, w.s.admin))

    assert views[supplier.id].forward.sender_role == "supplier"
    assert views[supplier.id].forward.forwardable is True
    assert views[customer.id].forward.sender_role == "customer"
    assert views[customer.id].forward.forwardable is False
    # Who only follows the case cannot send it.
    seller_views = _events(await w.s.service.get_case(case.id, w.s.seller))
    assert seller_views[supplier.id].forward.forwardable is False


@pytest.mark.asyncio
async def test_forwarding_queues_the_supplier_answer_from_no_reply_and_records_it() -> None:
    w = World()
    case, supplier, _ = await w.case_with_answers()

    result = await w.service.forward(
        case.id, supplier.id, "Te contamos lo que nos dijo.", w.s.admin
    )

    (item,) = w.outbox.items
    assert item.kind == CUSTOMER_FORWARD and item.recipient_email == "cliente@x.co"
    assert item.subject == f"Novedad caso {case.code}"
    content = BrandedEmail.from_payload(item.payload["email"])
    assert content.notice == AUTOMATIC_NOTICE
    # The supplier's own words, without the quoted history.
    assert content.quote == "Revisamos el lote 123.\n\nProcede."
    assert "Te contamos lo que nos dijo." in content.paragraphs
    # Only the files of that email travel.
    assert [
        a.original_filename for a in w.emails.attachments if str(a.id) in item.payload["file_ids"]
    ] == [f"de-{SUPPLIER}.pdf"]
    assert result.event.event.event_type == "forwarded_to_customer"
    assert result.event.summary.title == f"Envió al cliente la respuesta de {SUPPLIER}"
    assert result.event.event.data["to"] == "cliente@x.co"

    after = _events(await w.s.service.get_case(case.id, w.s.admin))[supplier.id].forward
    assert after.forwardable is False
    assert after.forwarded_at == result.event.event.created_at
    assert after.forwarded_by == "calidad@jdh.com.co" and after.forwarded_to == "cliente@x.co"


@pytest.mark.asyncio
async def test_an_answer_goes_to_the_customer_only_once() -> None:
    w = World()
    case, supplier, _ = await w.case_with_answers()
    await w.service.forward(case.id, supplier.id, None, w.s.admin)

    with pytest.raises(ConflictError):
        await w.service.forward(case.id, supplier.id, None, w.s.admin)
    assert len(w.outbox.items) == 1


@pytest.mark.asyncio
async def test_what_cannot_be_forwarded() -> None:
    w = World()
    case, supplier, customer = await w.case_with_answers()

    with pytest.raises(InvalidInputError):
        await w.service.forward(case.id, customer.id, None, w.s.admin)
    with pytest.raises(ForbiddenError):
        await w.service.forward(case.id, supplier.id, None, w.s.seller)

    w.s.cases.cases[case.id].requester_email = None
    with pytest.raises(InvalidInputError):
        await w.service.forward(case.id, supplier.id, None, w.s.admin)

    w.s.cases.cases[case.id].requester_email = "cliente@x.co"
    w.s.cases.cases[case.id].status = w.s.finalizada
    with pytest.raises(ConflictError):
        await w.service.forward(case.id, supplier.id, None, w.s.admin)
    assert w.outbox.items == []


@pytest.mark.asyncio
async def test_without_a_no_reply_mailbox_nothing_is_forwardable() -> None:
    w = World(noreply="")
    case, supplier, _ = await w.case_with_answers()

    views = _events(await w.s.service.get_case(case.id, w.s.admin))
    assert views[supplier.id].forward.forwardable is False
    with pytest.raises(DependencyUnavailableError):
        await w.service.forward(case.id, supplier.id, None, w.s.admin)


@pytest.mark.asyncio
async def test_the_preview_shows_the_email_and_sends_nothing() -> None:
    w = World()
    case, supplier, _ = await w.case_with_answers()

    preview = await w.service.preview(case.id, supplier.id, None, w.s.admin)

    assert preview.to == "cliente@x.co" and preview.sender == "no-reply@jdh.com.co"
    assert preview.subject == f"Novedad caso {case.code}"
    assert "no lo respondas" in preview.html and "no se leen ni se procesan" in preview.html
    assert preview.text.startswith("IMPORTANTE: Este es un correo automático")
    assert "Revisamos el lote 123." in preview.html
    assert preview.attachments == [f"de-{SUPPLIER}.pdf"]
    assert w.outbox.items == []


class _Mail:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, mailbox, to, subject, text, files=(), cc=(), html=False):
        self.sent.append(
            {
                "from": mailbox,
                "to": to,
                "subject": subject,
                "text": text,
                "files": files,
                "html": html,
            }
        )


class _Storage:
    async def get_object(self, key):
        return b"%PDF"


@pytest.mark.asyncio
async def test_the_forward_leaves_from_no_reply_in_the_jdh_layout() -> None:
    w = World()
    case, supplier, _ = await w.case_with_answers()
    await w.service.forward(case.id, supplier.id, None, w.s.admin)
    mail = _Mail()
    sender = NotificationService(
        repository=w.outbox,
        cases=OpenCases(w.s.cases),
        people=w.s.directory,
        team=FakeTeam([]),
        mail=mail,
        managers=w.s.managers,
        holidays=FakeHolidays(),
        mailbox="pqrsf@jdh.com.co",
        area_slug="calidad",
        emails=w.emails,
        storage=_Storage(),
        clock=lambda: NOW,
        noreply_sender="no-reply@jdh.com.co",
    )

    assert await sender.dispatch() == 1

    (sent,) = mail.sent
    assert sent["from"] == "no-reply@jdh.com.co" and sent["to"] == "cliente@x.co"
    assert sent["html"] is True and AUTOMATIC_NOTICE.split(":")[0] in sent["text"]
    assert [f.name for f in sent["files"]] == [f"de-{SUPPLIER}.pdf", "jdh-logo.png"]
    assert (
        render_html(BrandedEmail.from_payload(w.outbox.items[0].payload["email"])) == sent["text"]
    )


@pytest.mark.asyncio
async def test_a_forward_without_no_reply_mailbox_is_not_sent_from_the_pqrsf_mailbox() -> None:
    w = World()
    case, supplier, _ = await w.case_with_answers()
    await w.service.forward(case.id, supplier.id, None, w.s.admin)
    mail = _Mail()
    sender = NotificationService(
        repository=w.outbox,
        cases=OpenCases(w.s.cases),
        people=w.s.directory,
        team=FakeTeam([]),
        mail=mail,
        managers=w.s.managers,
        holidays=FakeHolidays(),
        mailbox="pqrsf@jdh.com.co",
        area_slug="calidad",
        emails=w.emails,
        storage=_Storage(),
        clock=lambda: NOW,
    )

    assert await sender.dispatch() == 0
    assert mail.sent == [] and w.outbox.items[0].status == "pending"
