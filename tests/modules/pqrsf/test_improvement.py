"""Improvement opportunity (OM, P.06): template, consecutive, Word/PDF, sending, outcome."""

import io
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from docx import Document
from PIL import Image

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
)
from app.modules.pqrsf.application.improvement_opportunities import (
    ImprovementOpportunityService,
    OmCommand,
)
from app.modules.pqrsf.application.supplier_messages import SupplierMessageService
from app.modules.pqrsf.domain.email import CaseAttachment
from app.modules.pqrsf.domain.entities import ClaimedProduct, PqrsfAttachment
from app.modules.pqrsf.domain.improvement import OmFilters, OmHistoryRow, Signer
from app.modules.pqrsf.infrastructure.word_renderer import DocxTemplateRenderer
from app.modules.pqrsf.templates import BUNDLED
from app.modules.sites.domain.entities import Site, clean_om_code
from tests.modules.pqrsf.test_supplier_messages import (
    FakeContacts,
    FakeEmails,
    FakeFiles,
    FakeOutbox,
)
from tests.modules.pqrsf.test_tracking import NOW, Setup, _public_request, _request_type

TEMPLATE = BUNDLED["om"].read_bytes()


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


class OmStore:
    """In-memory ``ImprovementRepository``."""

    def __init__(self) -> None:
        self.templates: dict = {}
        self.next = 1181
        self.oms: dict = {}

    async def list_templates(self, kind):
        return sorted(
            (t for t in self.templates.values() if t.kind == kind),
            key=lambda t: t.created_at,
            reverse=True,
        )

    async def get_template(self, template_id):
        return self.templates.get(template_id)

    async def active_template(self, kind):
        return next((t for t in self.templates.values() if t.kind == kind and t.is_active), None)

    async def add_template(self, template):
        for other in self.templates.values():
            if other.kind == template.kind:
                other.is_active = False
        template.created_at = datetime.now(UTC)
        self.templates[template.id] = template
        return template

    async def take_om_number(self):
        self.next += 1
        return self.next - 1

    async def next_om_number(self):
        return self.next

    async def set_next_om_number(self, value, user_id):
        used = max((om.number for om in self.oms.values()), default=None)
        if used is not None and value <= used:
            raise ConflictError("ya existe")
        self.next = value

    async def list_for_case(self, case_id):
        return [om for om in self.oms.values() if om.case_id == case_id]

    async def get(self, om_id):
        return self.oms.get(om_id)

    async def save(self, om):
        for other in self.oms.values():
            if (
                other.id != om.id
                and other.case_id == om.case_id
                and other.supplier_code == om.supplier_code
            ):
                raise ConflictError("duplicada")
        self.oms[om.id] = replace(om)
        return replace(om)

    async def history(self, filters, offset, limit):
        self.asked = (filters, offset, limit)
        rows = [
            OmHistoryRow(om, "Q26MD0001", "En curso", False, "Cliente", "Medellín")
            for om in self.oms.values()
            if filters.status in (None, om.status)
        ]
        return rows[offset : offset + limit], len(rows), {"draft": 1, "awaiting": 2, "replied": 3}


class Bucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key, body, content_type=None):
        self.objects[key] = body

    async def get_object(self, key):
        return self.objects[key]

    async def delete_object(self, key):
        self.objects.pop(key, None)


class Converter:
    def __init__(self) -> None:
        self.down = False
        self.converted: list[str] = []

    async def to_pdf(self, filename, content):
        if self.down:
            raise DependencyUnavailableError("caído")
        self.converted.append(filename)
        return b"%PDF-1.7 " + filename.encode()


class Signers:
    async def signer(self, user_id):
        return Signer(
            user_id, "Cristian Garzón", "Analista de Calidad", "calidad@jdh.com.co", "317"
        )


class World:
    """A complaint with products of two suppliers and two pieces of evidence."""

    def __init__(self, om_code: str | None = "MED") -> None:
        self.s = Setup()
        self.queja = _request_type("queja", "Q")
        self.s.requests.types[self.queja.id] = self.queja
        self.bucket = Bucket()
        site = Site(
            uuid.uuid4(), "medellin", "Medellín", True, NOW, NOW, "MD", "Itagüí", "Cra 42", om_code
        )
        request = replace(
            _public_request(self.queja),
            site=site,
            products=[
                ClaimedProduct(
                    "FEV-1", "26081", "2965", date(2026, 2, 20), 60, "Leche", "P1", "Parmalat"
                ),
                ClaimedProduct("FEV-2", "9834", "L1", None, 3, "Aceite", "P2", "Team Foods"),
            ],
        )
        self.photo = self._attachment(request.id, "foto.jpg", "image/jpeg", _jpeg())
        self.video = self._attachment(request.id, "video.mp4", "video/mp4", b"mp4")
        request = replace(request, attachments=[self.photo, self.video])
        self.request = request
        self.s.requests.requests[request.id] = request
        self.s.requests.get_attachment = self._get_attachment
        self.store = OmStore()
        self.converter = Converter()
        self.service = ImprovementOpportunityService(
            tracking=self.s.service,
            requests=self.s.requests,
            repository=self.store,
            storage=self.bucket,
            renderer=DocxTemplateRenderer(),
            converter=self.converter,
            signers=Signers(),
            managers=self.s.managers,
            clock=lambda: NOW,
        )

    def _attachment(self, request_id, name, content_type, content):
        attachment = PqrsfAttachment(
            uuid.uuid4(),
            request_id,
            name,
            name.rsplit(".", 1)[1],
            content_type,
            len(content),
            "x",
            f"pqrsf/{request_id}/{name}",
            NOW,
        )
        self.bucket.objects[attachment.storage_key] = content
        return attachment

    async def _get_attachment(self, request_id, attachment_id):
        request = self.s.requests.requests.get(request_id)
        return next((a for a in request.attachments if a.id == attachment_id), None)

    async def case(self):
        await self.service.install_template("om", "om.docx", TEMPLATE, "V02", None)
        return await self.s.service.open_for_request(self.request)


def _text(document: bytes) -> str:
    parts = Document(io.BytesIO(document))
    body = [p.text for p in parts.paragraphs]
    for table in parts.tables:
        for row in table.rows:
            for cell in row.cells:
                body.append(cell.text)
    return "\n".join(body)


def _images(document: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(document)) as archive:
        return sum(
            1
            for name in archive.namelist()
            if name.startswith("word/media/image") and name.endswith(".jpg")
        )


# --- Template ---


@pytest.mark.asyncio
async def test_the_bundled_template_installs_and_replaces_the_active_one() -> None:
    w = World()

    first = await w.service.install_template("om", "om.docx", TEMPLATE, "V02", None)
    second = await w.service.install_template("om", "om-v3.docx", TEMPLATE, "V03", None)

    assert (await w.store.active_template("om")).id == second.id
    assert not w.store.templates[first.id].is_active
    assert w.bucket.objects[second.storage_key] == TEMPLATE
    history = await w.service.list_templates("om", w.s.admin)
    assert [t.version_label for t in history] == ["V03", "V02"]


@pytest.mark.asyncio
async def test_a_template_without_the_marks_or_not_a_word_is_refused() -> None:
    w = World()
    blank = io.BytesIO()
    Document().save(blank)

    with pytest.raises(InvalidInputError, match="faltan"):
        await w.service.install_template("om", "otra.docx", blank.getvalue(), "V9", None)
    with pytest.raises(InvalidInputError, match="Word"):
        await w.service.install_template("om", "om.pdf", b"%PDF", "V9", None)
    assert w.store.templates == {} and w.bucket.objects.keys() == {
        w.photo.storage_key,
        w.video.storage_key,
    }


@pytest.mark.asyncio
async def test_only_managers_change_the_template_and_the_consecutive() -> None:
    w = World()

    with pytest.raises(ForbiddenError):
        await w.service.upload_template("om", "om.docx", TEMPLATE, "V02", w.s.seller)
    with pytest.raises(ForbiddenError):
        await w.service.set_next_number(2000, w.s.seller)
    assert await w.service.set_next_number(2000, w.s.admin) == 2000
    assert await w.service.next_number(w.s.admin) == 2000


# --- Generating ---


@pytest.mark.asyncio
async def test_the_om_fills_the_p06_from_the_case_for_one_supplier() -> None:
    w = World()
    case = await w.case()

    om = await w.service.generate(
        case.id, OmCommand(supplier_code="P1", evidence_ids=(w.photo.id,)), w.s.admin
    )

    assert (om.number, om.code) == (1181, "1181MED")
    assert om.supplier_name == "Parmalat" and om.status == "draft"
    document = w.bucket.objects[om.docx_key]
    text = _text(document)
    for value in (
        "No. Consecutivo: 1181MED",
        "Ciudad: Itagüí",
        "Dirección Sede: Cra 42",
        "Documento No: FEV-1",
        "Clasificación (Q, R, S, F): Q",
        "Razón social del Proveedor/usuario afectado: Parmalat",
        "Nombre: Cristian Garzón",
        "Cargo: Analista de Calidad",
        "26081",
        "20/02/2026",
    ):
        assert value in text, value
    # Only the products of that supplier, and the chosen photo printed on it.
    assert "9834" not in text
    assert _images(document) == 1
    assert w.bucket.objects[om.pdf_key].startswith(b"%PDF")
    # Generating it is work on the case, and it is in the timeline.
    stored = w.s.cases.cases[case.id]
    assert stored.status.code == "en_curso"
    generated = [e for e in w.s.cases.events if e.event_type == "om_generated"]
    assert generated[0].data["code"] == "1181MED" and generated[0].data["again"] is False


@pytest.mark.asyncio
async def test_generating_again_keeps_the_number_and_a_second_supplier_takes_the_next() -> None:
    w = World()
    case = await w.case()

    first = await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    again = await w.service.generate(
        case.id, OmCommand(supplier_code="P1", immediate_actions="Se recogió."), w.s.admin
    )
    other = await w.service.generate(case.id, OmCommand(supplier_code="P2"), w.s.admin)

    assert again.id == first.id and again.code == "1181MED"
    assert "Acciones Inmediatas: Se recogió." in _text(w.bucket.objects[again.docx_key])
    assert other.code == "1182MED" and other.supplier_name == "Team Foods"
    assert {om.code for om in await w.service.list_for_case(case.id, w.s.admin)} == {
        "1181MED",
        "1182MED",
    }


@pytest.mark.asyncio
async def test_what_stops_an_om() -> None:
    w = World(om_code=None)
    case = await w.case()

    with pytest.raises(InvalidInputError, match="código de OM"):
        await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)

    w2 = World()
    case2 = await w2.s.service.open_for_request(w2.request)
    with pytest.raises(ConflictError, match="plantilla"):
        await w2.service.generate(case2.id, OmCommand(supplier_code="P1"), w2.s.admin)

    w3 = World()
    case3 = await w3.case()
    with pytest.raises(InvalidInputError, match="no es una foto"):
        await w3.service.generate(
            case3.id, OmCommand(supplier_code="P1", evidence_ids=(w3.video.id,)), w3.s.admin
        )
    with pytest.raises(InvalidInputError, match="no es evidencia"):
        await w3.service.generate(
            case3.id, OmCommand(supplier_code="P1", evidence_ids=(uuid.uuid4(),)), w3.s.admin
        )
    # Who only follows the case does not generate it.
    with pytest.raises(ForbiddenError):
        await w3.service.generate(case3.id, OmCommand(supplier_code="P1"), w3.s.seller)
    assert w3.store.oms == {}


@pytest.mark.asyncio
async def test_without_the_converter_the_om_keeps_its_word_and_the_pdf_is_retried() -> None:
    w = World()
    case = await w.case()
    w.converter.down = True

    om = await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    assert om.docx_key and om.pdf_key is None
    with pytest.raises(ConflictError, match="PDF"):
        await w.service.document(case.id, om.id, "pdf", w.s.admin)

    w.converter.down = False
    retried = await w.service.retry_pdf(case.id, om.id, w.s.admin)
    pdf = await w.service.document(case.id, om.id, "pdf", w.s.admin)
    assert retried.pdf_key and pdf.filename == "OM 1181MED.pdf"
    assert pdf.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_a_hand_edited_word_replaces_the_draft_and_its_pdf() -> None:
    w = World()
    case = await w.case()
    om = await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    edited = io.BytesIO()
    Document().save(edited)

    updated = await w.service.upload_edited(
        case.id, om.id, "OM editada.docx", edited.getvalue(), w.s.admin
    )

    assert updated.edited is True
    assert w.bucket.objects[updated.docx_key] == edited.getvalue()
    assert w.converter.converted[-1] == "OM 1181MED.docx"
    with pytest.raises(InvalidInputError):
        await w.service.upload_edited(case.id, om.id, "OM.pdf", b"%PDF", w.s.admin)


# --- Sending ---


class StoredFiles(FakeFiles):
    """Stores like the real comment service: ``CaseAttachment`` rows under the case."""

    async def store_files(self, case_id, accepted):
        return [
            CaseAttachment(
                uuid.uuid4(), case_id, None, name, extension, kind, len(data), "x", "k", NOW
            )
            for name, extension, kind, data in accepted
        ]


def _messages(w: World) -> tuple[SupplierMessageService, FakeOutbox]:
    outbox = FakeOutbox()
    service = SupplierMessageService(
        w.s.service,
        StoredFiles(),
        FakeEmails(),
        outbox,
        w.s.requests,
        FakeContacts(),
        clock=lambda: NOW,
        oms=w.service,
    )
    return service, outbox


@pytest.mark.asyncio
async def test_sending_the_om_attaches_its_pdf_and_marks_the_case() -> None:
    w = World()
    case = await w.case()
    om = await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    messages, outbox = _messages(w)

    result = await messages.send(
        case.id,
        w.s.admin,
        kind="improvement",
        to=["calidad@parmalat.com"],
        body="Adjuntamos la OM.",
        supplier_code="P1",
        om_id=om.id,
    )

    assert [f.original_filename for f in result.files] == ["OM 1181MED.pdf"]
    (email,) = outbox.items.values()
    assert email.payload["file_ids"] == [str(result.files[0].id)]
    assert result.event.event.data["om_code"] == "1181MED"
    sent = await w.store.get(om.id)
    assert sent.status == "sent" and sent.sent_at == NOW
    assert w.s.cases.cases[case.id].om_sent_at is not None
    events = [e for e in w.s.cases.events if e.event_type == "om_sent"]
    assert events[0].data["to"] == ["calidad@parmalat.com"]
    # A sent OM is not generated again.
    with pytest.raises(ConflictError, match="ya se envió"):
        await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)


@pytest.mark.asyncio
async def test_the_om_goes_only_with_an_improvement_message_and_with_its_pdf() -> None:
    w = World()
    case = await w.case()
    w.converter.down = True
    om = await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    messages, outbox = _messages(w)
    common = dict(to=["a@b.co"], body="Hola", om_id=om.id)

    with pytest.raises(InvalidInputError, match="oportunidad de mejora"):
        await messages.send(case.id, w.s.admin, kind="request", **common)
    with pytest.raises(ConflictError, match="PDF"):
        await messages.send(case.id, w.s.admin, kind="improvement", **common)
    assert outbox.items == {}


# --- Outcome of the case ---


async def _finished(w: World):
    return next(s for s in w.s.cases.statuses.values() if s.is_terminal)


@pytest.mark.asyncio
async def test_finishing_a_complaint_requires_how_it_ended() -> None:
    w = World()
    case = await w.s.service.open_for_request(w.request)
    finished = await _finished(w)

    with pytest.raises(InvalidInputError, match="procede"):
        await w.s.service.change_status(case.id, finished.id, None, w.s.admin)

    view = await w.s.service.change_status(
        case.id,
        finished.id,
        None,
        w.s.admin,
        resolution="procede",
        internal_failure=True,
        resolution_note="Lote vencido despachado",
    )

    assert view.case.resolution == "procede" and view.case.internal_failure is True
    assert view.case.resolution_note == "Lote vencido despachado"
    assert view.case.resolved_at is not None
    closing = [e for e in w.s.cases.events if e.to_status_id == finished.id]
    assert closing[-1].data == {"resolution": "procede", "internal_failure": True}


@pytest.mark.asyncio
async def test_a_petition_closes_without_outcome_and_the_outcome_only_goes_when_finishing() -> None:
    w = World()
    view = await w.s.service.create_case(w.s.command(), w.s.admin)
    finished = await _finished(w)

    with pytest.raises(InvalidInputError, match="al finalizarlo"):
        await w.s.service.change_status(
            view.case.id, w.s.en_curso.id, None, w.s.admin, resolution="procede"
        )
    for item in w.s.cases.cases[view.case.id].items:
        item.responded_on = date(2026, 9, 26)
    closed = await w.s.service.change_status(view.case.id, finished.id, None, w.s.admin)
    assert closed.case.resolution is None and closed.case.is_closed


def test_the_site_om_code_takes_three_letters() -> None:
    assert clean_om_code(" bog ") == "BOG"
    assert clean_om_code("") is None
    with pytest.raises(InvalidInputError):
        clean_om_code("BG")


# --- History ---


@pytest.mark.asyncio
async def test_the_history_is_for_who_reads_pqrsf_and_pages_the_oms() -> None:
    w = World()
    case = await w.case()
    await w.service.generate(case.id, OmCommand(supplier_code="P1"), w.s.admin)
    await w.service.generate(case.id, OmCommand(supplier_code="P2"), w.s.admin)

    with pytest.raises(ForbiddenError):
        await w.service.history(OmFilters(), 1, 25, w.s.seller)
    with pytest.raises(InvalidInputError):
        await w.service.history(OmFilters(status="perdida"), 1, 25, w.s.admin)

    result = await w.service.history(OmFilters(status="draft"), 2, 1, w.s.admin)

    assert result.total == 2 and len(result.rows) == 1
    assert (result.drafts, result.awaiting_reply, result.replied) == (1, 2, 3)
    assert w.store.asked[1:] == (1, 1)
