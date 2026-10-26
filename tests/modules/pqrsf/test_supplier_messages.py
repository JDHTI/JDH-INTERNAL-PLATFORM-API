from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.application.supplier_messages import SupplierMessageService
from app.modules.pqrsf.domain.entities import ClaimedProduct
from app.modules.pqrsf.domain.notifications import FAILED, PENDING, SUPPLIER_MESSAGE
from app.modules.pqrsf.domain.tracking import CaseEvent

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)
PRINCIPAL = SimpleNamespace(user_id=uuid4())


def _case(closed: bool = False):
    return SimpleNamespace(
        id=uuid4(),
        code="Q26AC0001",
        is_closed=closed,
        request_id=uuid4(),
        subject="Calidad del producto · Producto vencido",
        customer_name="Cliente SAS",
        site=SimpleNamespace(name="Medellín"),
        received_on=date(2026, 10, 26),
    )


class FakeTracking:
    def __init__(self, case) -> None:
        self.case = case
        self.events: list[CaseEvent] = []

    async def require_view(self, case_id, principal):
        return self.case

    async def require_work(self, case_id, principal):
        return self.case

    async def record_supplier_message(self, case_id, text, principal, data):
        event = CaseEvent(uuid4(), case_id, "supplier_message", None, None, None, text, data, NOW)
        self.events.append(event)
        return EventView(event, None)


class FakeFiles:
    def __init__(self) -> None:
        self.guard = SimpleNamespace(max_attachment_bytes=1000)

    async def accept_files(self, files):
        return [(f.filename, "pdf", "application/pdf", f.content) for f in files], []

    async def store_files(self, case_id, accepted):
        return [
            SimpleNamespace(
                id=uuid4(),
                original_filename=name,
                content_type=kind,
                size_bytes=len(data),
                created_at=NOW,
                comment_event_id=None,
                storage_key="k",
            )
            for name, _, kind, data in accepted
        ]

    async def discard_files(self, stored):
        pass


class FakeEmails:
    def __init__(self) -> None:
        self.saved = []

    async def add_attachments(self, attachments):
        self.saved += attachments


class FakeOutbox:
    def __init__(self) -> None:
        self.items = {}

    async def enqueue(self, notification):
        self.items[notification.id] = notification

    async def list_for_case(self, case_id, kind):
        return [n for n in self.items.values() if n.case_id == case_id and n.kind == kind]

    async def get_notification(self, notification_id):
        return self.items.get(notification_id)

    async def save_notification(self, notification):
        self.items[notification.id] = notification


class FakeRequests:
    def __init__(self, request_id) -> None:
        self.request = SimpleNamespace(
            id=request_id,
            attachments=[SimpleNamespace(id=uuid4(), size_bytes=500)],
            products=[
                ClaimedProduct("FE-1", "9834", "L-7", date(2026, 12, 1), 3, "Aceite", "P1", "TF")
            ],
        )

    async def get_request(self, request_id):
        return self.request if request_id == self.request.id else None


class FakeContacts:
    def __init__(self) -> None:
        self.remembered = []

    async def remember(self, contacts):
        self.remembered += contacts

    async def search(self, supplier_code, query, limit):
        return [c for c in self.remembered if not supplier_code or c.supplier_code == supplier_code]


def _service(closed: bool = False):
    case = _case(closed)
    tracking, outbox, contacts = FakeTracking(case), FakeOutbox(), FakeContacts()
    woken = []
    service = SupplierMessageService(
        tracking,
        FakeFiles(),
        FakeEmails(),
        outbox,
        FakeRequests(case.request_id),
        contacts,
        wake=lambda: woken.append(True),
        clock=lambda: NOW,
    )
    return service, case, tracking, outbox, contacts, woken


@pytest.mark.asyncio
async def test_a_message_goes_to_each_supplier_address_and_into_the_timeline() -> None:
    service, case, tracking, outbox, contacts, woken = _service()

    result = await service.send(
        case.id,
        PRINCIPAL,
        kind="request",
        to=["Calidad@Proveedor.com", "calidad@proveedor.com", "jefe@proveedor.com"],
        body="Por favor envíen la ficha técnica del lote L-7.",
        supplier_code="P1",
        supplier_name="TEAM FOODS",
        include_evidence=True,
    )

    [event] = tracking.events
    assert event.data["to"] == ["calidad@proveedor.com", "jefe@proveedor.com"]
    assert event.data["kind"] == "request"
    assert len(event.data["evidence_ids"]) == 1
    assert [n.recipient_email for n in result.emails] == event.data["to"]
    first = result.emails[0]
    assert first.kind == SUPPLIER_MESSAGE
    assert first.subject.startswith("[Q26AC0001] Solicitud de información")
    assert "Lote L-7" in first.body and "Cliente: Cliente SAS" in first.body
    assert {c.email for c in contacts.remembered} == set(event.data["to"])
    assert woken == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"to": []}, "al menos un correo"),
        ({"to": ["no-es-correo"]}, "no es válido"),
        ({"to": [f"p{i}@x.com" for i in range(6)]}, "Máximo 5"),
        ({"kind": "otra"}, "tipo de mensaje"),
        ({"body": "   "}, "no puede estar vacío"),
    ],
)
async def test_a_message_is_validated(values, message) -> None:
    service, case, *_ = _service()
    base = {"kind": "notice", "to": ["a@proveedor.com"], "body": "Hola"}

    with pytest.raises(InvalidInputError, match=message):
        await service.send(case.id, PRINCIPAL, **{**base, **values})


@pytest.mark.asyncio
async def test_a_closed_case_does_not_write_to_suppliers() -> None:
    service, case, *_ = _service(closed=True)

    with pytest.raises(ConflictError):
        await service.send(case.id, PRINCIPAL, kind="notice", to=["a@x.com"], body="Hola")


@pytest.mark.asyncio
async def test_only_a_failed_email_is_retried() -> None:
    service, case, _, outbox, _, _ = _service()
    result = await service.send(case.id, PRINCIPAL, kind="notice", to=["a@x.com"], body="Hola")
    email = result.emails[0]

    with pytest.raises(ConflictError):
        await service.retry(case.id, email.id, PRINCIPAL)
    email.status, email.attempts = FAILED, 5
    retried = await service.retry(case.id, email.id, PRINCIPAL)
    assert (retried.status, retried.attempts) == (PENDING, 0)
    with pytest.raises(NotFoundError):
        await service.retry(case.id, uuid4(), PRINCIPAL)


class FilesMail:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, mailbox, to, subject, text, files=()):
        self.sent.append((to, subject, [f.name for f in files]))


class Bucket:
    async def get_object(self, key):
        return b"bytes of " + key.encode()


class StoredFiles:
    async def get_attachments(self, case_id, ids):
        return [
            SimpleNamespace(
                original_filename="ficha.pdf", content_type="application/pdf", storage_key="f"
            )
        ]


class Evidence:
    async def get_attachment(self, request_id, attachment_id):
        return SimpleNamespace(
            original_filename="caja.mp4", content_type="video/mp4", storage_key="v"
        )


@pytest.mark.asyncio
async def test_the_email_to_the_supplier_carries_its_files_and_the_client_evidence() -> None:
    from app.modules.pqrsf.application.notifications import NotificationService

    from tests.modules.pqrsf.test_notifications import FakeOutbox as Outbox

    outbox, mail = Outbox(), FilesMail()
    service, case, *_ = _service()
    service.notifications = outbox
    await service.send(
        case.id,
        PRINCIPAL,
        kind="improvement",
        to=["calidad@proveedor.com"],
        body="Escalamos la novedad",
        include_evidence=True,
    )
    sender = NotificationService(
        repository=outbox,
        cases=None,
        people=None,
        team=None,
        mail=mail,
        managers=None,
        holidays=None,
        mailbox="pqrsf@jdh.com.co",
        area_slug="calidad",
        emails=StoredFiles(),
        storage=Bucket(),
        requests=Evidence(),
        clock=lambda: NOW,
    )
    outbox.items[0].payload["file_ids"] = [str(uuid4())]

    assert await sender.dispatch() == 1
    [(to, subject, files)] = mail.sent
    assert to == "calidad@proveedor.com"
    assert subject.startswith("[Q26AC0001] Oportunidad de mejora")
    assert files == ["ficha.pdf", "caja.mp4"]
