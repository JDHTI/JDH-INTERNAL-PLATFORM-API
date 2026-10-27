import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import DependencyUnavailableError, InvalidInputError
from app.modules.mail.domain import DeltaResult, InboundMessage, MailAttachment
from app.modules.pqrsf.application.email_intake import EmailIntakeService
from app.modules.pqrsf.domain.email import EVENT_EMAIL_RECEIVED, CaseAttachment, EmailMessage
from app.modules.pqrsf.domain.entities import SanitizedImage
from app.modules.pqrsf.domain.tracking import Dependency
from app.modules.pqrsf.infrastructure.models import CaseModel
from app.modules.sites.domain.entities import Site
from tests.modules.pqrsf.test_tracking import Setup, _classified

NOW = datetime(2026, 9, 28, 15, tzinfo=UTC)
MAILBOX = "pqrsf@jdh.com.co"
PDF = b"%PDF-1.7 contenido"
TEMPLATE = (
    "Tipo: Felicitación\nSede: Medellín\nNombre: Ana Pérez\nRazón social: XYZ SAS\n"
    "NIT: 900123456-7\nMotivo: Buen servicio\nDescripción:\nGracias por la entrega."
)


class FakeMail:
    def __init__(self) -> None:
        self.pending: list[str] = []
        self.messages: dict[str, InboundMessage] = {}
        self.attachments: dict[str, list[MailAttachment]] = {}
        self.contents: dict[str, bytes] = {}
        self.replies: list[tuple[str, str]] = []
        self.marked: list[tuple[str, str | None]] = []
        self.cursors: list[str | None] = []

    def add(self, message: InboundMessage, files: dict[MailAttachment, bytes] | None = None):
        self.messages[message.graph_id] = message
        self.pending.append(message.graph_id)
        for attachment, content in (files or {}).items():
            self.attachments.setdefault(message.graph_id, []).append(attachment)
            self.contents[attachment.id] = content

    async def list_new_messages(self, mailbox, delta_link):
        self.cursors.append(delta_link)
        ids, self.pending = self.pending, []
        return DeltaResult(ids, f"delta-{len(self.cursors)}")

    async def get_message(self, mailbox, graph_id):
        return self.messages.get(graph_id)

    async def list_attachments(self, mailbox, graph_id):
        return self.attachments.get(graph_id, [])

    async def download_attachment(self, mailbox, graph_id, attachment_id):
        return self.contents[attachment_id]

    async def reply(self, mailbox, graph_id, text):
        self.replies.append((graph_id, text))

    async def mark_processed(self, mailbox, graph_id, category):
        self.marked.append((graph_id, category))


class FakeEmails:
    def __init__(self) -> None:
        self.messages: dict[str, EmailMessage] = {}
        self.delta: str | None = None
        self.attachments: list[CaseAttachment] = []
        self.cases = None

    async def add_message(self, message):
        if message.internet_message_id in self.messages:
            return False
        self.messages[message.internet_message_id] = message
        return True

    async def get_message(self, internet_message_id):
        return self.messages.get(internet_message_id)

    async def save_message(self, message):
        self.messages[message.internet_message_id] = message

    async def list_retryable(self, max_attempts):
        return [
            m
            for m in self.messages.values()
            if m.status in ("received", "failed") and m.attempts < max_attempts
        ]

    async def find_case_by_conversation(self, conversation_id):
        rows = [
            m
            for m in self.messages.values()
            if m.conversation_id == conversation_id and m.case_id is not None
        ]
        return max(rows, key=lambda m: m.received_at).case_id if rows else None

    async def find_feedback_by_conversation(self, conversation_id):
        rows = [
            m
            for m in self.messages.values()
            if m.conversation_id == conversation_id and m.detail.get("feedback_id")
        ]
        found = max(rows, key=lambda m: m.received_at).detail["feedback_id"] if rows else None
        return uuid.UUID(found) if found else None

    async def find_case_by_code(self, code):
        return next((c.id for c in self.cases.cases.values() if c.code == code), None)

    async def get_delta_link(self, mailbox):
        return self.delta

    async def save_delta_link(self, mailbox, delta_link):
        self.delta = delta_link

    async def add_attachments(self, attachments):
        self.attachments.extend(attachments)

    async def list_attachments(self, case_id):
        return [a for a in self.attachments if a.case_id == case_id]


class FakeSites:
    def __init__(self, sites) -> None:
        self.sites = sites

    async def list_sites(self, include_inactive=False):
        return self.sites


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        # Objects it accepts before the bucket "goes down" (None = never).
        self.fail_after: int | None = None

    async def put_object(self, key, body, content_type=None):
        if self.fail_after is not None and len(self.objects) >= self.fail_after:
            raise DependencyUnavailableError("bucket caído")
        self.objects[key] = body

    async def delete_object(self, key):
        self.objects.pop(key, None)


class FakeImages:
    async def sanitize(self, filename, content_type, content):
        if content == b"broken":
            raise InvalidInputError("La imagen no es válida o está dañada")
        return SanitizedImage(filename, filename.rsplit(".", 1)[1].lower(), content_type, content)


class FakeScanner:
    def __init__(self) -> None:
        self.infected = False
        self.down = 0

    async def scan(self, content):
        if self.down:
            self.down -= 1
            raise DependencyUnavailableError("antivirus caído")
        if self.infected or b"EICAR" in content:
            raise InvalidInputError("La imagen fue rechazada por el antivirus")


class Intake:
    def __init__(self, **overrides) -> None:
        self.setup = Setup()
        self.mail = FakeMail()
        self.emails = FakeEmails()
        self.emails.cases = self.setup.cases
        self.storage = FakeStorage()
        self.scanner = FakeScanner()
        self.medellin = Site(uuid.uuid4(), "medellin", "Medellín", True, NOW, NOW, "MD")
        values = {
            "mail": self.mail,
            "emails": self.emails,
            "cases": self.setup.service,
            "requests": self.setup.requests,
            "sites": FakeSites([self.medellin]),
            "storage": self.storage,
            "image_processor": FakeImages(),
            "malware_scanner": self.scanner,
            "mailbox": MAILBOX,
            "ignored_senders": ["info@jdh.com.co"],
            "allowed_extensions": ["pdf", "docx", "png", "jpg", "txt"],
            "max_attachments": 3,
            "max_attachment_bytes": 1000,
            "max_total_attachment_bytes": 1500,
            "clock": lambda: NOW,
        }
        values.update(overrides)
        self.service = EmailIntakeService(**values)
        self.count = 0

    def message(self, body="Hola", **overrides) -> InboundMessage:
        self.count += 1
        values = {
            "graph_id": f"g{self.count}",
            "internet_message_id": f"<m{self.count}@x>",
            "conversation_id": f"conv{self.count}",
            "subject": "Una solicitud",
            "sender_address": "ana@cliente.com",
            "sender_name": "Ana",
            "to_addresses": (MAILBOX,),
            "received_at": NOW + timedelta(minutes=self.count),
            "body_text": body,
            "has_attachments": False,
        }
        values.update(overrides)
        return InboundMessage(**values)

    def cases(self):
        return list(self.setup.cases.cases.values())

    def events(self, event_type=EVENT_EMAIL_RECEIVED):
        return [e for e in self.setup.cases.events if e.event_type == event_type]


def file(name="soporte.pdf", size=100, inline=False, ident=None):
    return MailAttachment(ident or f"a-{name}", name, "application/octet-stream", size, inline)


@pytest.mark.asyncio
async def test_a_template_email_opens_a_case_and_is_answered() -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE))

    report = await t.service.sync()

    (case,) = t.cases()
    assert report.created == 1
    assert case.origin == "email"
    assert case.request_type.code == "felicitacion"
    assert case.site == t.medellin
    # The subject is the email's Subject header; a different Motivo opens the description.
    assert case.subject == "Una solicitud"
    assert case.requester_name == "Ana Pérez"
    assert case.requester_email == "ana@cliente.com"
    assert (case.customer_name, case.customer_nit) == ("XYZ SAS", "900123456-7")
    assert case.description == "Motivo: Buen servicio\n\nGracias por la entrega."
    assert case.code == "F26MD0001"
    assert case.received_on == NOW.date()
    ((graph_id, text),) = t.mail.replies
    assert graph_id == "g1" and case.code in text and "Tipo:" not in text
    assert t.mail.marked == [("g1", case.code)]
    stored = t.emails.messages["<m1@x>"]
    assert (stored.status, stored.case_id) == ("created", case.id)
    assert stored.detail["needs_review"] is False


@pytest.mark.asyncio
async def test_free_text_still_opens_a_case_flagged_for_review() -> None:
    t = Intake()
    t.mail.add(t.message("Mi pedido llegó incompleto, ayuda."))

    await t.service.sync()

    (case,) = t.cases()
    assert case.request_type.code == "peticion"
    assert case.description == "Mi pedido llegó incompleto, ayuda."
    assert case.subject == "Una solicitud"
    assert t.emails.messages["<m1@x>"].detail["needs_review"] is True
    assert "Tipo:" in t.mail.replies[0][1]


@pytest.mark.asyncio
async def test_an_unknown_type_or_site_is_flagged_but_not_lost() -> None:
    t = Intake()
    t.mail.add(t.message("Tipo: Duda\nSede: Marte\nDescripción:\nHola"))

    await t.service.sync()

    (case,) = t.cases()
    assert case.request_type.code == "peticion" and case.site is None
    assert t.emails.messages["<m1@x>"].detail["needs_review"] is True


@pytest.mark.asyncio
async def test_the_same_message_never_opens_two_cases() -> None:
    t = Intake()
    original = t.message(TEMPLATE)
    t.mail.add(original)
    await t.service.sync()
    t.mail.add(original)  # Graph lists it again (cursor reset, redelivery)

    await t.service.sync()

    assert len(t.cases()) == 1 and len(t.mail.replies) == 1


@pytest.mark.asyncio
async def test_a_reply_in_the_same_conversation_joins_the_open_case() -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE, conversation_id="thread"))
    await t.service.sync()
    t.mail.add(
        t.message("Adjunto más datos.\n\nEl lun escribió:\n> hola", conversation_id="thread")
    )

    report = await t.service.sync()

    (case,) = t.cases()
    assert report.threaded == 1
    (event,) = t.events()
    assert event.case_id == case.id
    assert event.note == "Adjunto más datos."
    assert event.data["from"] == "ana@cliente.com"
    assert len(t.mail.replies) == 1  # only the new case was acknowledged
    assert t.emails.messages["<m2@x>"].status == "threaded"


@pytest.mark.asyncio
async def test_a_reply_is_matched_by_the_code_in_the_subject_when_the_thread_changed() -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE))
    await t.service.sync()
    (case,) = t.cases()
    t.mail.add(t.message("Una pregunta", subject=f"RE: {case.code} seguimiento"))

    await t.service.sync()

    assert len(t.cases()) == 1 and len(t.events()) == 1


@pytest.mark.asyncio
async def test_a_reply_to_a_closed_case_opens_a_new_one_that_points_back() -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE, conversation_id="thread"))
    await t.service.sync()
    (first,) = t.cases()
    first.status = t.setup.finalizada
    t.mail.add(t.message("Sigue el problema", conversation_id="thread"))

    await t.service.sync()

    second = next(c for c in t.cases() if c.id != first.id)
    created = next(e for e in t.events("created") if e.case_id == second.id)
    assert created.data["related_case"] == first.code
    # The rest of the conversation now belongs to the new case.
    t.mail.add(t.message("Otro dato", conversation_id="thread"))
    await t.service.sync()
    assert len(t.cases()) == 2
    assert t.events()[-1].case_id == second.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"sender_address": "info@jdh.com.co"},
        {"sender_address": "pqrsf@jdh.com.co"},
        {"sender_address": "mailer-daemon@x.com"},
        {"headers": {"auto-submitted": "auto-replied"}},
    ],
)
async def test_automatic_or_own_mail_is_ignored(overrides) -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE, **overrides))

    report = await t.service.sync()

    assert report.ignored == 1 and not t.cases() and not t.mail.replies
    assert t.emails.messages["<m1@x>"].status == "ignored"


@pytest.mark.asyncio
async def test_the_first_sync_leaves_the_old_inbox_alone() -> None:
    t = Intake()
    t.mail.add(t.message(TEMPLATE, received_at=NOW - timedelta(days=3)))
    t.mail.add(t.message(TEMPLATE, received_at=NOW + timedelta(minutes=1)))

    await t.service.sync()

    assert len(t.cases()) == 1 and t.emails.delta == "delta-1"

    # With a start date the earlier mail is taken too.
    t = Intake(since=NOW - timedelta(days=7))
    t.mail.add(t.message(TEMPLATE, received_at=NOW - timedelta(days=3)))
    await t.service.sync()
    assert len(t.cases()) == 1


@pytest.mark.asyncio
async def test_the_cursor_is_passed_on_and_saved() -> None:
    t = Intake()
    await t.service.sync()
    await t.service.sync()

    assert t.mail.cursors == [None, "delta-1"]
    assert t.emails.delta == "delta-2"


@pytest.mark.asyncio
async def test_files_are_cleaned_scanned_and_stored_under_the_case() -> None:
    t = Intake()
    t.mail.add(
        t.message(TEMPLATE, has_attachments=True),
        {
            file("soporte.pdf"): PDF,
            file("foto.png"): b"imagen",
            file("captura.png", inline=True): b"pegada en el cuerpo",
        },
    )

    await t.service.sync()

    (case,) = t.cases()
    saved = t.emails.attachments
    # A picture pasted into the body is evidence too: inline images are kept.
    assert sorted(a.original_filename for a in saved) == ["captura.png", "foto.png", "soporte.pdf"]
    assert all(a.case_id == case.id for a in saved)
    assert all(a.storage_key.startswith(f"pqrsf/cases/{case.id}/") for a in saved)
    assert {a.storage_key for a in saved} == set(t.storage.objects)
    pdf = next(a for a in saved if a.extension == "pdf")
    assert pdf.content_type == "application/pdf" and pdf.size_bytes == len(PDF)
    assert len(pdf.checksum_sha256) == 64
    assert not t.events()  # nothing was refused


@pytest.mark.asyncio
async def test_refused_files_are_reported_without_blocking_the_case() -> None:
    t = Intake()
    t.mail.add(
        t.message(TEMPLATE, has_attachments=True),
        {
            file("programa.exe"): b"MZ",
            file("falso.pdf"): b"no soy pdf",
            file("grande.pdf", size=5000): PDF,
            file("virus.docx"): b"PK\x03\x04 EICAR",
            file("roto.png"): b"broken",
            file("bien.txt"): b"hola",
        },
    )

    await t.service.sync()

    assert [a.original_filename for a in t.emails.attachments] == ["bien.txt"]
    (event,) = t.events()
    reasons = {f["name"]: f["reason"] for f in event.data["rejected_files"]}
    assert reasons == {
        "programa.exe": "tipo de archivo no permitido",
        "falso.pdf": "el contenido no corresponde a su extensión",
        "grande.pdf": "supera el tamaño máximo por archivo",
        "virus.docx": "rechazado por el antivirus",
        "roto.png": "La imagen no es válida o está dañada",
    }
    assert len(t.cases()) == 1


@pytest.mark.asyncio
async def test_attachment_limits_are_enforced() -> None:
    t = Intake(max_attachments=2, max_total_attachment_bytes=250)

    def pdf(size: int) -> bytes:
        return PDF + b"x" * (size - len(PDF))

    t.mail.add(
        t.message(TEMPLATE, has_attachments=True),
        {
            file("a.pdf", size=200): pdf(200),
            file("b.pdf", size=100): pdf(100),
            file("c.pdf", size=30): pdf(30),
            file("d.pdf", size=30): pdf(30),
        },
    )

    await t.service.sync()

    assert [a.original_filename for a in t.emails.attachments] == ["a.pdf", "c.pdf"]
    (event,) = t.events()
    assert {f["name"]: f["reason"] for f in event.data["rejected_files"]} == {
        "b.pdf": "supera el tamaño máximo total",
        "d.pdf": "supera el máximo de adjuntos por correo",
    }


@pytest.mark.asyncio
async def test_a_scanner_outage_is_retried_without_a_second_case_or_answer() -> None:
    t = Intake()
    t.scanner.down = 1
    t.mail.add(t.message(TEMPLATE, has_attachments=True), {file(): PDF})

    first = await t.service.sync()

    stored = t.emails.messages["<m1@x>"]
    assert first.failed == 1 and stored.status == "failed" and stored.attempts == 1
    assert len(t.cases()) == 1 and stored.case_id is not None
    assert not t.mail.replies and not t.emails.attachments

    second = await t.service.sync()

    assert second.created == 1 and len(t.cases()) == 1
    assert stored.status == "created" and stored.attempts == 2
    assert [a.original_filename for a in t.emails.attachments] == ["soporte.pdf"]
    assert len(t.mail.replies) == 1


@pytest.mark.asyncio
async def test_a_bucket_outage_leaves_no_orphan_objects_and_stops_after_the_attempts() -> None:
    t = Intake(max_attempts=2)
    t.storage.fail_after = 1  # the first file is stored, the second one fails
    t.mail.add(
        t.message(TEMPLATE, has_attachments=True),
        {file("a.pdf"): PDF, file("b.pdf"): PDF + b" distinto"},
    )

    await t.service.sync()
    await t.service.sync()
    await t.service.sync()

    stored = t.emails.messages["<m1@x>"]
    assert stored.status == "failed" and stored.attempts == 2
    assert not t.storage.objects and not t.emails.attachments


@pytest.mark.asyncio
async def test_a_message_deleted_before_a_retry_is_dropped() -> None:
    t = Intake()
    t.scanner.down = 1
    t.mail.add(t.message(TEMPLATE, has_attachments=True), {file(): PDF})
    await t.service.sync()
    del t.mail.messages["g1"]

    await t.service.sync()

    assert t.emails.messages["<m1@x>"].status == "ignored"


def test_the_cases_table_accepts_the_email_origin() -> None:
    # The fakes accept any origin; the database does not, so the schema must list it.

    check = next(c for c in CaseModel.__table__.constraints if c.name == "ck_pqrsf_cases_origin")

    assert "'email'" in str(check.sqltext)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("seguimiento.csv", b"a;b\n1;2\n", "text/csv"),
        (
            "hoja.xlsx",
            b"PK\x03\x04 hoja",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("antiguo.xls", b"\xd0\xcf\x11\xe0 hoja", "application/vnd.ms-excel"),
        ("nota.txt", b"hola", "text/plain"),
    ],
)
async def test_content_types_come_from_a_fixed_table_not_the_operating_system(
    name, content, expected
) -> None:
    t = Intake(allowed_extensions=["csv", "xlsx", "xls", "txt"])
    t.mail.add(t.message(TEMPLATE, has_attachments=True), {file(name): content})

    await t.service.sync()

    assert [a.content_type for a in t.emails.attachments] == [expected]


@pytest.mark.asyncio
async def test_a_signature_logo_repeated_in_every_reply_is_kept_once() -> None:
    t = Intake()
    t.mail.add(
        t.message(TEMPLATE, has_attachments=True, conversation_id="thread"),
        {file("logo.png", inline=True, ident="a1"): b"logo de firma"},
    )
    await t.service.sync()
    t.mail.add(
        t.message("Adjunto otro dato", has_attachments=True, conversation_id="thread"),
        {
            file("image001.png", inline=True, ident="a2"): b"logo de firma",
            file("extra.pdf", ident="a3"): PDF,
        },
    )

    await t.service.sync()

    assert sorted(a.original_filename for a in t.emails.attachments) == ["extra.pdf", "logo.png"]


@pytest.mark.asyncio
async def test_inline_objects_that_are_not_pictures_are_skipped() -> None:
    t = Intake()
    t.mail.add(
        t.message(TEMPLATE, has_attachments=True),
        {file("firma.pdf", inline=True): PDF, file("foto.png", inline=True): b"imagen"},
    )

    await t.service.sync()

    assert [a.original_filename for a in t.emails.attachments] == ["foto.png"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject", "motivo", "expected_subject", "expected_description"),
    [
        # Same text in both places: nothing to add to the description.
        (
            "RE: Nota crédito factura FV-1234",
            "nota credito factura fv-1234",
            "Nota crédito factura FV-1234",
            "Detalle.",
        ),
        # Different Motivo: the header wins and the Motivo is kept.
        ("Factura", "Cambio de factura", "Factura", "Motivo: Cambio de factura\n\nDetalle."),
        # No usable header (only a tag): the Motivo becomes the subject.
        ("[PQRSF]", "Cambio de factura", "Cambio de factura", "Detalle."),
        # No Motivo at all.
        ("Factura", None, "Factura", "Detalle."),
    ],
)
async def test_the_case_subject_is_the_email_subject_and_the_motivo_is_kept_if_it_differs(
    subject, motivo, expected_subject, expected_description
) -> None:
    t = Intake()
    body = "Tipo: Petición\n" + (f"Motivo: {motivo}\n" if motivo else "") + "Descripción:\nDetalle."
    t.mail.add(t.message(body, subject=subject))

    await t.service.sync()

    (case,) = t.cases()
    assert (case.subject, case.description) == (expected_subject, expected_description)


@pytest.mark.asyncio
async def test_novedad_and_causal_route_the_case_to_the_dependency_of_the_cause() -> None:
    t = Intake()
    distribucion = Dependency(uuid.uuid4(), "distribucion", "Distribución", 2, True, NOW, NOW)
    t.setup.cases.dependencies[distribucion.id] = distribucion
    subject, cause = _classified(distribucion.id)
    t.setup.requests.subjects = [subject]
    t.mail.add(t.message("Tipo: Petición\nCausal: producto vencido\nDescripción:\nLlegó vencido"))
    t.mail.add(t.message("Tipo: Petición\nNovedad: Algo raro\nDescripción:\nNo está en la lista"))

    await t.service.sync()

    first, second = t.cases()
    assert first.dependency == distribucion
    assert t.emails.messages["<m1@x>"].detail["needs_review"] is False
    assert second.dependency is None
    assert t.emails.messages["<m2@x>"].detail["needs_review"] is True
