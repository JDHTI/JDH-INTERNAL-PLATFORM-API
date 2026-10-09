"""Certificates sent automatically: the panel, the split of a request and its emails."""

import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.mail.domain import OutgoingFile
from app.modules.pqrsf.application.certificates import CertificateService
from app.modules.pqrsf.application.document_delivery import DocumentDeliveryService
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.certificates import (
    Certificate,
    CertificateEntry,
    DeliveryFilters,
    ManagerDocument,
)
from app.modules.pqrsf.domain.entities import DocumentCategory, DocumentOption
from app.modules.pqrsf.domain.notifications import DOCUMENT_DELIVERY, FAILED, PENDING
from app.modules.sites.application.service import SiteService

from tests.modules.fakes import FakeDirectory
from tests.modules.pqrsf.test_feedback import FakeOutbox
from tests.modules.pqrsf.test_notifications import Env
from tests.modules.pqrsf.test_service import (
    FakePqrsfRepository,
    FakeSiteRepository,
    _create,
    _payload,
    _type,
)
from tests.modules.pqrsf.test_tracking import FakeManagers

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)
MB = 1024 * 1024


def _document(name: str, area: str = "calidad", size: int = 100_000) -> ManagerDocument:
    return ManagerDocument(
        id=uuid.uuid4(),
        name=name,
        filename=f"{name}.pdf",
        content_type="application/pdf",
        size_bytes=size,
        folder_id=uuid.uuid4(),
        folder_name="Certificados",
        area_slug=area,
        area_name=area.title(),
    )


class FakeCertificates:
    """The catalog of certificates over a few document types and files of the manager."""

    def __init__(self) -> None:
        self.names: dict[uuid.UUID, str] = {}
        self.categories: dict[uuid.UUID, str] = {}
        self.documents: dict[uuid.UUID, ManagerDocument] = {}
        self.certificates: dict[uuid.UUID, Certificate] = {}

    def add_type(self, name: str, category: str = "legal") -> uuid.UUID:
        type_id = uuid.uuid4()
        self.names[type_id], self.categories[type_id] = name, category
        return type_id

    def add_document(self, document: ManagerDocument) -> ManagerDocument:
        self.documents[document.id] = document
        return document

    async def list_entries(self):
        return [await self.get_entry(type_id) for type_id in self.names]

    async def get_entry(self, document_type_id):
        if document_type_id not in self.names or self.categories[document_type_id] == "otro":
            return None
        return CertificateEntry(
            document_type_id,
            self.names[document_type_id],
            self.categories[document_type_id],
            self.categories[document_type_id].title(),
            self.certificates.get(document_type_id),
        )

    async def type_names(self, document_type_ids):
        return {i: self.names[i] for i in document_type_ids if i in self.names}

    async def automatic_for(self, document_type_ids):
        return {
            i: c for i, c in self.certificates.items() if i in document_type_ids and c.automatic
        }

    async def save(self, document_type_id, document_id, is_active, user_id):
        certificate = Certificate(
            uuid.uuid4(), document_type_id, self.documents.get(document_id), is_active, NOW, user_id
        )
        self.certificates[document_type_id] = certificate
        return certificate

    async def delete(self, document_type_id):
        self.certificates.pop(document_type_id, None)

    async def list_documents(self, area_slug, query, folder_id, limit):
        return [
            d
            for d in self.documents.values()
            if d.area_slug == area_slug and (not query or query.lower() in d.name.lower())
        ][:limit]

    async def get_document(self, document_id):
        return self.documents.get(document_id)


class FakeDeliveries:
    def __init__(self, outbox: FakeOutbox) -> None:
        self.rows: dict[uuid.UUID, object] = {}
        self.outbox = outbox

    async def add(self, delivery):
        for row in self.rows.values():
            if row.request_id == delivery.request_id:
                return self._read(row), False
        self.rows[delivery.id] = copy.deepcopy(delivery)
        return self._read(delivery), True

    def _read(self, row):
        found = copy.deepcopy(row)
        emails = [n for n in self.outbox.items if n.id in found.notification_ids]
        found.status = (
            "failed"
            if any(n.status == "failed" for n in emails)
            else "sent"
            if emails and all(n.status == "sent" for n in emails)
            else "pending"
        )
        return found

    async def get(self, delivery_id):
        row = self.rows.get(delivery_id)
        return self._read(row) if row else None

    async def get_by_request(self, request_id):
        row = next((r for r in self.rows.values() if r.request_id == request_id), None)
        return self._read(row) if row else None

    async def list_deliveries(self, filters, page, size):
        raise NotImplementedError

    async def summarize(self):
        raise NotImplementedError

    async def mark_read(self, delivery_id, user_id, at):
        row = self.rows[delivery_id]
        row.read_at, row.read_by_user_id = at, user_id

    async def set_notifications(self, delivery_id, notification_ids):
        self.rows[delivery_id].notification_ids = list(notification_ids)

    async def set_case(self, delivery_id, case_code):
        self.rows[delivery_id].case_code = case_code


class FakeCases:
    """Opens a case per request and remembers which documents it was opened with."""

    def __init__(self) -> None:
        self.opened: dict[uuid.UUID, tuple[str, list]] = {}
        self.fail = False

    async def open_for_request(self, request):
        if self.fail:
            raise RuntimeError("db caída")
        code, _ = self.opened.setdefault(
            request.id, (f"P26MD{len(self.opened) + 1:04d}", list(request.documents))
        )
        return type("Case", (), {"code": code})()

    async def code_for_request(self, request_id):
        return self.opened[request_id][0] if request_id in self.opened else None


class Rig:
    """A document request flowing through the public service, with every collaborator a fake."""

    def __init__(self, **overrides) -> None:
        self.category_id = uuid.uuid4()
        self.certificates = FakeCertificates()
        self.outbox = FakeOutbox()
        self.deliveries_repo = FakeDeliveries(self.outbox)
        self.cases = FakeCases()
        self.wakes: list[int] = []
        self.directory = FakeDirectory()
        self.managers = FakeManagers()
        values = {
            "deliveries": self.deliveries_repo,
            "certificates": self.certificates,
            "notifications": self.outbox,
            "managers": self.managers,
            "people": self.directory,
            "wake": lambda: self.wakes.append(1),
            "clock": lambda: NOW,
        }
        values.update(overrides)
        self.deliveries = DocumentDeliveryService(**values)
        self.request_type = _type()
        from app.modules.sites.domain.entities import Site

        self.site = Site(uuid.uuid4(), "bogota", "Bogotá", True, NOW, NOW, "BG")
        self.service = PqrsfService(
            FakePqrsfRepository(self.request_type),
            SiteService(FakeSiteRepository(self.site)),
            cases=self.cases,
            deliveries=self.deliveries,
        )
        self.rut = self.certificates.add_type("RUT JDH")
        self.camara = self.certificates.add_type("Cámara de Comercio JDH")
        self.coa = self.certificates.add_type("Certificado Análisis (COA)", "producto")
        self.rut_file = self.certificates.add_document(_document("RUT JDH"))
        self.camara_file = self.certificates.add_document(_document("Cámara de Comercio JDH"))

    async def configure(self, type_id, document, active=True) -> None:
        await self.certificates.save(type_id, document.id, active, uuid.uuid4())

    async def ask(self, type_ids, other: str | None = None, key: str | None = None):
        values = {
            **_payload(self.request_type, self.site, key or str(uuid.uuid4())),
            "document_request": True,
            "document_category_id": self.category_id,
            "document_type_ids": type_ids,
            "document_other": other,
        }
        # The fake repository does not validate the catalog: the documents asked are exact.
        self.service._requested_documents = _documents(type_ids, other)
        return await _create(self.service, self.request_type, self.site, values)


def _documents(type_ids, other):
    from app.modules.pqrsf.domain.entities import RequestedDocument

    async def fake(*args, **kwargs):
        found = [RequestedDocument(i) for i in type_ids]
        return found + ([RequestedDocument(None, other)] if other else [])

    return fake


# --- A request answered with certificates ---


@pytest.mark.asyncio
async def test_a_request_for_configured_certificates_is_answered_with_no_case() -> None:
    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file)
    await rig.configure(rig.camara, rig.camara_file)

    result = await rig.ask([rig.rut, rig.camara])

    assert (result.handled_as, result.tracking_code) == ("documents", None)
    assert result.delivered == ["RUT JDH", "Cámara de Comercio JDH"]
    assert rig.cases.opened == {}
    (email,) = rig.outbox.items
    assert email.kind == DOCUMENT_DELIVERY and email.recipient_email == "ana@example.com"
    assert email.subject == "Documentos solicitados a JDH"
    assert email.payload["document_ids"] == [str(rig.rut_file.id), str(rig.camara_file.id)]
    assert "- RUT JDH" in email.body and email.body.rstrip().endswith("PQRSF JDH")
    assert rig.wakes == [1]
    (logged,) = rig.deliveries_repo.rows.values()
    assert logged.request_id == result.request.id
    assert [i.name for i in logged.items] == ["RUT JDH", "Cámara de Comercio JDH"]
    assert logged.notification_ids == [email.id]


@pytest.mark.asyncio
async def test_what_has_no_certificate_or_is_other_still_opens_a_case_with_only_that() -> None:
    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file)

    result = await rig.ask([rig.rut, rig.coa], other="Constancia especial")

    assert result.handled_as == "case" and result.tracking_code == "P26MD0001"
    assert result.delivered == ["RUT JDH"]
    _, opened_with = rig.cases.opened[result.request.id]
    assert [(d.document_type_id, d.description) for d in opened_with] == [
        (rig.coa, None),
        (None, "Constancia especial"),
    ]
    (email,) = rig.outbox.items
    # The email says what was left for a person and under which consecutive.
    assert "P26MD0001" in email.body
    assert "Certificado Análisis (COA)" in email.body and "Constancia especial" in email.body
    (logged,) = rig.deliveries_repo.rows.values()
    assert logged.case_code == "P26MD0001"
    assert logged.manual_items == ["Certificado Análisis (COA)", "Constancia especial"]


@pytest.mark.asyncio
async def test_without_a_configured_certificate_nothing_changes() -> None:
    rig = Rig()

    result = await rig.ask([rig.rut], other="Algo")

    assert result.handled_as == "case" and result.delivered == []
    assert rig.outbox.items == [] and rig.deliveries_repo.rows == {}
    assert len(rig.cases.opened[result.request.id][1]) == 2


@pytest.mark.asyncio
async def test_a_certificate_that_is_off_or_lost_its_file_is_not_sent() -> None:
    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file, active=False)
    lost = Certificate(uuid.uuid4(), rig.camara, None, True, NOW)  # the file left the manager
    rig.certificates.certificates[rig.camara] = lost

    result = await rig.ask([rig.rut, rig.camara])

    assert result.delivered == [] and len(rig.cases.opened[result.request.id][1]) == 2
    assert rig.outbox.items == []


@pytest.mark.asyncio
async def test_resubmitting_the_same_request_sends_and_opens_nothing_twice() -> None:
    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file)
    key = str(uuid.uuid4())

    first = await rig.ask([rig.rut, rig.coa], key=key)
    # The certificate is edited between the two submissions: the first split stands.
    await rig.configure(rig.coa, rig.camara_file)
    again = await rig.ask([rig.rut, rig.coa], key=key)

    assert again.replayed is True and again.tracking_code == first.tracking_code
    assert len(rig.outbox.items) == 1 and len(rig.cases.opened) == 1
    assert len(rig.deliveries_repo.rows) == 1


@pytest.mark.asyncio
async def test_an_address_over_its_daily_share_gets_a_case_instead_of_emails() -> None:
    rig = Rig(max_emails_per_address=1)
    await rig.configure(rig.rut, rig.rut_file)
    first = await rig.ask([rig.rut])
    second = await rig.ask([rig.rut])

    assert first.handled_as == "documents"
    assert second.handled_as == "case" and second.delivered == []
    assert len(rig.outbox.items) == 1  # the public form cannot be used to flood a mailbox


@pytest.mark.asyncio
async def test_while_sending_is_off_everything_opens_a_case() -> None:
    rig = Rig(enabled=False)
    await rig.configure(rig.rut, rig.rut_file)

    result = await rig.ask([rig.rut])

    assert result.handled_as == "case" and rig.outbox.items == []
    assert await rig.deliveries.automatic_documents([rig.rut]) == set()


@pytest.mark.asyncio
async def test_a_big_request_is_split_in_emails_under_the_size_cap() -> None:
    rig = Rig(max_email_bytes=15 * MB)
    ids = []
    for n in range(3):
        type_id = rig.certificates.add_type(f"Documento {n}")
        document = rig.certificates.add_document(_document(f"Documento {n}", size=8 * MB))
        await rig.configure(type_id, document)
        ids.append(type_id)

    await rig.ask(ids)

    assert [e.subject for e in rig.outbox.items] == [
        "Documentos solicitados a JDH (1 de 3)",
        "Documentos solicitados a JDH (2 de 3)",
        "Documentos solicitados a JDH (3 de 3)",
    ]
    assert [len(e.payload["document_ids"]) for e in rig.outbox.items] == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_failing_delivery_falls_back_to_the_case() -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("db caída")

    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file)
    rig.deliveries_repo.add = broken

    result = await rig.ask([rig.rut])

    assert result.handled_as == "case" and result.tracking_code == "P26MD0001"
    assert rig.outbox.items == []


@pytest.mark.asyncio
async def test_the_public_catalog_marks_the_documents_answered_at_once() -> None:
    rig = Rig()
    await rig.configure(rig.rut, rig.rut_file)
    category = DocumentCategory(
        uuid.uuid4(),
        "legal",
        "Legal",
        None,
        [DocumentOption(rig.rut, "RUT JDH"), DocumentOption(rig.coa, "COA")],
    )

    marked = await rig.service._with_automatic([category])

    assert [(o.name, o.automatic) for o in marked[0].documents] == [
        ("RUT JDH", True),
        ("COA", False),
    ]


# --- The panel ---


def _panel():
    directory = FakeDirectory()
    admin = directory.sign_in("calidad@jdh.com.co", admin=True)
    stranger = directory.sign_in("otro@jdh.com.co")
    certificates = FakeCertificates()
    service = CertificateService(certificates, FakeManagers(), "calidad")
    return service, certificates, admin, stranger


@pytest.mark.asyncio
async def test_only_managers_use_the_panel() -> None:
    service, certificates, admin, stranger = _panel()
    type_id = certificates.add_type("RUT JDH")
    document = certificates.add_document(_document("RUT JDH"))

    for call in (
        service.list_entries(stranger),
        service.save(type_id, document.id, True, stranger),
        service.remove(type_id, stranger),
        service.search_documents(stranger),
    ):
        with pytest.raises(ForbiddenError):
            await call
    assert [e.name for e in await service.list_entries(admin)] == ["RUT JDH"]


@pytest.mark.asyncio
async def test_a_certificate_is_a_file_of_the_quality_area_only() -> None:
    service, certificates, admin, _ = _panel()
    type_id = certificates.add_type("RUT JDH")
    quality = certificates.add_document(_document("RUT JDH"))
    private = certificates.add_document(_document("Nómina", area="gerencia"))

    entry = await service.save(type_id, quality.id, True, admin)
    assert entry.certificate.automatic and entry.certificate.document.path == (
        "Calidad / Certificados / RUT JDH"
    )
    with pytest.raises(Exception, match="Calidad"):
        await service.save(type_id, private.id, True, admin)
    with pytest.raises(NotFoundError):
        await service.save(type_id, uuid.uuid4(), True, admin)
    # The files offered to choose from are those of the quality area, nothing else.
    assert [d.name for d in await service.search_documents(admin)] == ["RUT JDH"]


@pytest.mark.asyncio
async def test_a_certificate_can_be_switched_off_or_removed_but_other_has_none() -> None:
    service, certificates, admin, _ = _panel()
    other = certificates.add_type("Información", category="otro")
    type_id = certificates.add_type("RUT JDH")
    document = certificates.add_document(_document("RUT JDH"))

    with pytest.raises(NotFoundError):
        await service.save(other, document.id, True, admin)

    await service.save(type_id, document.id, True, admin)
    off = await service.save(type_id, document.id, False, admin)
    assert off.certificate.is_active is False and not off.certificate.automatic
    await service.remove(type_id, admin)
    with pytest.raises(NotFoundError):
        await service.remove(type_id, admin)


# --- The log and the emails ---


@pytest.mark.asyncio
async def test_a_failed_delivery_can_be_retried_only_by_a_manager_and_only_when_failed() -> None:
    rig = Rig()
    admin = rig.directory.sign_in("calidad@jdh.com.co", admin=True)
    stranger = rig.directory.sign_in("otro@jdh.com.co")
    await rig.configure(rig.rut, rig.rut_file)
    await rig.ask([rig.rut])
    (delivery,) = rig.deliveries_repo.rows.values()
    (email,) = rig.outbox.items

    with pytest.raises(ForbiddenError):
        await rig.deliveries.retry(delivery.id, stranger)
    with pytest.raises(ConflictError):
        await rig.deliveries.retry(delivery.id, admin)  # nothing failed yet

    email.status, email.attempts, email.error = FAILED, 5, "graph caído"
    again = await rig.deliveries.retry(delivery.id, admin)

    assert (email.status, email.attempts, email.error) == (PENDING, 0, None)
    assert again.status == "pending" and again.read_at == NOW
    with pytest.raises(NotFoundError):
        await rig.deliveries.open(uuid.uuid4(), admin)
    with pytest.raises(ForbiddenError):
        await rig.deliveries.list_deliveries(stranger, DeliveryFilters(), 1, 10)


@pytest.mark.asyncio
async def test_the_email_carries_the_files_as_they_are_in_the_manager_when_it_leaves() -> None:
    env = Env()
    sent: list[tuple] = []

    async def send(mailbox, to, subject, text, files=()):
        sent.append((to, subject, [f.name for f in files]))

    class Files:
        def __init__(self) -> None:
            self.gone: set[uuid.UUID] = set()

        async def get_file(self, document_id):
            if document_id in self.gone:
                return None
            return OutgoingFile(f"{document_id}.pdf", "application/pdf", b"%PDF")

    files = Files()
    env.mail.send = send
    env.service.documents = files
    keep, lost = uuid.uuid4(), uuid.uuid4()
    rig = Rig()
    rig.outbox = env.outbox
    await env.outbox.enqueue(
        replace(
            _delivery_email(),
            payload={"document_ids": [str(keep)]},
            next_attempt_at=env.clock(),
        )
    )

    assert await env.service.dispatch() == 1
    assert sent == [("ana@example.com", "Documentos solicitados a JDH", [f"{keep}.pdf"])]

    # A file removed from the manager before sending fails the email: it is retried, never
    # sent without its attachment.
    files.gone.add(lost)
    await env.outbox.enqueue(
        replace(
            _delivery_email(),
            dedupe_key="otro",
            payload={"document_ids": [str(lost)]},
            next_attempt_at=env.clock(),
        )
    )
    assert await env.service.dispatch() == 0
    assert len(sent) == 1 and env.outbox.items[1].attempts == 1


def _delivery_email():
    from app.modules.pqrsf.domain.notifications import Notification

    return Notification(
        id=uuid.uuid4(),
        kind=DOCUMENT_DELIVERY,
        recipient_email="ana@example.com",
        recipient_user_id=None,
        subject="Documentos solicitados a JDH",
        body="Hola",
        case_id=None,
        dedupe_key="primero",
    )
