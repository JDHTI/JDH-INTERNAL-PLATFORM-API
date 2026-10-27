"""Answer a document requested in a case with a file of the manager or a one-off upload."""

import uuid
from datetime import date

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.pqrsf.application.attachment_guard import AttachmentGuard
from app.modules.pqrsf.application.item_delivery import ItemDeliveryService
from app.modules.pqrsf.domain.notifications import DOCUMENT_DELIVERY
from app.modules.pqrsf.domain.tracking import ItemInput
from tests.modules.pqrsf.test_certificates import FakeCertificates, _document
from tests.modules.pqrsf.test_email_intake import FakeImages, FakeScanner, FakeStorage
from tests.modules.pqrsf.test_feedback import FakeOutbox
from tests.modules.pqrsf.test_tracking import Setup

PDF = b"%PDF-1.7 certificado"


class FakeEmails:
    def __init__(self) -> None:
        self.attachments: list = []

    async def add_attachments(self, attachments):
        self.attachments.extend(attachments)


class FakeItemDeliveries:
    def __init__(self) -> None:
        self.rows: list = []

    async def add(self, delivery):
        self.rows.append(delivery)

    async def list_for_case(self, case_id):
        return [r for r in self.rows if r.case_id == case_id]


class Rig:
    """A case with documents requested, and the service that answers them."""

    def __init__(self) -> None:
        self.s = Setup()
        self.certificates = FakeCertificates()
        self.outbox = FakeOutbox()
        self.storage = FakeStorage()
        self.emails = FakeEmails()
        self.deliveries = FakeItemDeliveries()
        self.wakes: list[int] = []
        self.service = ItemDeliveryService(
            tracking=self.s.service,
            deliveries=self.deliveries,
            certificates=self.certificates,
            emails=self.emails,
            notifications=self.outbox,
            storage=self.storage,
            guard=AttachmentGuard(FakeImages(), FakeScanner(), ["pdf", "png"], 10, 1000, 5000),
            area_slug="calidad",
            wake=lambda: self.wakes.append(1),
        )
        self.s.managers.managers.add(self.s.responsible.user_id)
        self.quality_file = self.certificates.add_document(_document("RUT JDH"))

    async def case(self, *items: ItemInput, email: str | None = "cliente@empresa.com", **kw):
        view = await self.s.service.create_case(
            self.s.command(
                items=items or (ItemInput(self.s.certificado.id),),
                requester_email=email,
                requester_name="Ana Pérez",
                **kw,
            ),
            self.s.admin,
        )
        return view.case

    def item_of(self, case, index: int = 0):
        return self.s.cases.cases[case.id].items[index]


@pytest.mark.asyncio
async def test_linking_a_file_of_the_manager_answers_the_document_and_emails_it() -> None:
    r = Rig()
    case = await r.case()
    item = case.items[0]

    result = await r.service.deliver(
        case.id,
        item.id,
        r.s.responsible,
        source="vault",
        document_id=r.quality_file.id,
    )

    assert result.view.case.items[0].responded_on is not None
    assert result.view.case.status.code == "en_curso"  # the first answer starts the work
    (email,) = r.outbox.items
    assert email.kind == DOCUMENT_DELIVERY and email.recipient_email == "cliente@empresa.com"
    assert case.code in email.subject and "Certificado Calidad" in email.body
    assert email.body.rstrip().endswith("PQRSF JDH") and "Hola Ana," in email.body
    assert email.payload == {"document_ids": [str(r.quality_file.id)], "file_ids": []}
    assert email.case_id == case.id and r.wakes == [1]
    (stored,) = r.deliveries.rows
    assert (stored.source, stored.document_id, stored.notification_id) == (
        "vault",
        r.quality_file.id,
        email.id,
    )
    assert stored.document_path == "Calidad / Certificados / RUT JDH"
    assert r.emails.attachments == []  # a linked file is not copied into the case


@pytest.mark.asyncio
async def test_only_files_of_the_quality_area_can_be_linked() -> None:
    r = Rig()
    case = await r.case()
    private = r.certificates.add_document(_document("Nómina", area="gerencia"))

    for document_id, error in ((private.id, InvalidInputError), (uuid.uuid4(), NotFoundError)):
        with pytest.raises(error):
            await r.service.deliver(
                case.id,
                case.items[0].id,
                r.s.responsible,
                source="vault",
                document_id=document_id,
            )
    with pytest.raises(InvalidInputError):
        await r.service.deliver(
            case.id, case.items[0].id, r.s.responsible, source="vault", document_id=None
        )
    assert r.item_of(case).responded_on is None and r.outbox.items == []


@pytest.mark.asyncio
async def test_a_one_off_upload_stays_with_the_case_and_never_enters_the_manager() -> None:
    r = Rig()
    r.s.certificado.allows_direct_upload = True
    case = await r.case()

    result = await r.service.deliver(
        case.id,
        case.items[0].id,
        r.s.responsible,
        source="upload",
        filename="cedula.pdf",
        content=PDF,
    )

    (attachment,) = r.emails.attachments
    assert attachment.case_id == case.id and attachment.original_filename == "cedula.pdf"
    assert r.storage.objects[attachment.storage_key] == PDF
    assert attachment.storage_key.startswith(f"pqrsf/cases/{case.id}/")
    (email,) = r.outbox.items
    assert email.payload == {"document_ids": [], "file_ids": [str(attachment.id)]}
    assert result.delivery.attachment_id == attachment.id
    assert result.view.case.items[0].responded_on is not None


@pytest.mark.asyncio
async def test_a_document_that_must_go_through_the_manager_refuses_a_one_off_upload() -> None:
    r = Rig()
    case = await r.case()  # Certificado Calidad does not allow it

    with pytest.raises(InvalidInputError, match="gestor documental"):
        await r.service.deliver(
            case.id,
            case.items[0].id,
            r.s.responsible,
            source="upload",
            filename="x.pdf",
            content=PDF,
        )

    assert r.storage.objects == {} and r.item_of(case).responded_on is None


@pytest.mark.asyncio
async def test_a_free_text_document_accepts_a_one_off_upload() -> None:
    r = Rig()
    case = await r.case(ItemInput(None, "Constancia especial"))

    await r.service.deliver(
        case.id, case.items[0].id, r.s.responsible, source="upload", filename="c.pdf", content=PDF
    )

    assert len(r.emails.attachments) == 1 and len(r.outbox.items) == 1


@pytest.mark.asyncio
async def test_the_uploaded_file_follows_the_rules_of_every_other_file() -> None:
    r = Rig()
    r.s.certificado.allows_direct_upload = True
    case = await r.case()
    item_id = case.items[0].id

    for name, content in (("virus.exe", PDF), ("grande.pdf", b"x" * 2000), ("vacio.pdf", b"")):
        with pytest.raises(InvalidInputError):
            await r.service.deliver(
                case.id, item_id, r.s.responsible, source="upload", filename=name, content=content
            )
    r.service.guard.malware_scanner.infected = True
    with pytest.raises(InvalidInputError, match="antivirus"):
        await r.service.deliver(
            case.id, item_id, r.s.responsible, source="upload", filename="a.pdf", content=PDF
        )

    assert r.item_of(case).responded_on is None and r.outbox.items == []


@pytest.mark.asyncio
async def test_an_answer_can_be_registered_without_emailing_the_client() -> None:
    r = Rig()
    case = await r.case(email=None)

    result = await r.service.deliver(
        case.id,
        case.items[0].id,
        r.s.responsible,
        source="vault",
        document_id=r.quality_file.id,
        send_to_client=False,
    )

    assert r.outbox.items == [] and result.delivery.notification_id is None
    assert result.view.case.items[0].responded_on is not None


@pytest.mark.asyncio
async def test_emailing_needs_the_email_of_the_requester_and_registers_nothing_without_it() -> None:
    r = Rig()
    case = await r.case(email=None)

    with pytest.raises(InvalidInputError, match="correo"):
        await r.service.deliver(
            case.id,
            case.items[0].id,
            r.s.responsible,
            source="vault",
            document_id=r.quality_file.id,
        )

    assert r.item_of(case).responded_on is None


@pytest.mark.asyncio
async def test_a_late_answer_without_its_cause_stores_and_sends_nothing() -> None:
    r = Rig()
    r.s.ficha.allows_direct_upload = True
    # Received 14 Sep with a 5 day time: on the 28th it is late and needs its cause.
    case = await r.case(ItemInput(r.s.ficha.id), received_on=date(2026, 9, 14))

    with pytest.raises(InvalidInputError, match="causal"):
        await r.service.deliver(
            case.id,
            case.items[0].id,
            r.s.responsible,
            source="upload",
            filename="a.pdf",
            content=PDF,
        )
    assert r.storage.objects == {} and r.outbox.items == [] and r.emails.attachments == []

    await r.service.deliver(
        case.id,
        case.items[0].id,
        r.s.responsible,
        source="upload",
        filename="a.pdf",
        content=PDF,
        delay_cause="El proveedor no respondió",
    )
    assert r.item_of(case).delay_cause == "El proveedor no respondió"
    assert len(r.outbox.items) == 1


@pytest.mark.asyncio
async def test_an_answered_document_and_a_closed_case_cannot_be_answered_again() -> None:
    r = Rig()
    case = await r.case(ItemInput(r.s.certificado.id), ItemInput(r.s.ficha.id))
    await r.service.deliver(
        case.id, case.items[0].id, r.s.responsible, source="vault", document_id=r.quality_file.id
    )

    with pytest.raises(ConflictError, match="ya tiene"):
        await r.service.deliver(
            case.id,
            case.items[0].id,
            r.s.responsible,
            source="vault",
            document_id=r.quality_file.id,
        )

    r.s.cases.cases[case.id].status = r.s.finalizada
    with pytest.raises(ConflictError, match="finalizado"):
        await r.service.deliver(
            case.id,
            case.items[1].id,
            r.s.responsible,
            source="vault",
            document_id=r.quality_file.id,
        )
    assert len(r.outbox.items) == 1


@pytest.mark.asyncio
async def test_unknown_item_and_unknown_source() -> None:
    r = Rig()
    case = await r.case()

    with pytest.raises(NotFoundError):
        await r.service.deliver(
            case.id, uuid.uuid4(), r.s.responsible, source="vault", document_id=r.quality_file.id
        )
    with pytest.raises(InvalidInputError, match="fuente"):
        await r.service.deliver(case.id, case.items[0].id, r.s.responsible, source="correo")


@pytest.mark.asyncio
async def test_only_whoever_works_the_case_can_answer_search_and_list() -> None:
    r = Rig()
    case = await r.case()

    for call in (
        r.service.deliver(
            case.id, case.items[0].id, r.s.stranger, source="vault", document_id=r.quality_file.id
        ),
        r.service.search_documents(case.id, r.s.stranger),
        r.service.list_for_case(case.id, r.s.stranger),
    ):
        with pytest.raises((ForbiddenError, NotFoundError)):
            await call

    found = await r.service.search_documents(case.id, r.s.responsible, "rut")
    assert [d.name for d in found] == ["RUT JDH"]
