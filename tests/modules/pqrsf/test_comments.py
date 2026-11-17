import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.mail.domain import OutgoingFile
from app.modules.pqrsf.application.attachment_guard import AttachmentGuard
from app.modules.pqrsf.application.comments import (
    REPLY_ATTACHMENT_LIMIT,
    CaseCommentService,
    UploadedFile,
)
from app.modules.pqrsf.application.notifications import NotificationService
from app.modules.pqrsf.domain.notifications import CLIENT_REPLY, FAILED, Notification
from app.modules.pqrsf.domain.tracking import EventType
from tests.modules.pqrsf.test_email_intake import FakeImages, FakeScanner
from tests.modules.pqrsf.test_notifications import (
    FakeHolidays,
    FakeOutbox,
    FakeTeam,
    OpenCases,
)
from tests.modules.pqrsf.test_tracking import Setup

NOW = datetime(2026, 10, 8, 18, tzinfo=UTC)
PDF = b"%PDF-1.7 contenido"
CLIENT = "cliente@empresa.com"


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = False

    async def put_object(self, key, body, content_type=None):
        if self.fail:
            raise DependencyUnavailableError("bucket caído")
        self.objects[key] = body

    async def get_object(self, key):
        return self.objects[key]

    async def delete_object(self, key):
        self.objects.pop(key, None)


class FakeCaseFiles:
    def __init__(self) -> None:
        self.files = []
        self.latest_graph_id: str | None = None

    async def add_attachments(self, attachments):
        self.files.extend(attachments)

    async def get_attachments(self, case_id, ids):
        by_id = {a.id: a for a in self.files if a.case_id == case_id}
        return [by_id[i] for i in ids if i in by_id]

    async def latest_inbound_graph_id(self, case_id):
        return self.latest_graph_id


class Env:
    def __init__(self) -> None:
        self.s = Setup()
        self.bucket = FakeBucket()
        self.files = FakeCaseFiles()
        self.outbox = FakeOutbox()
        self.scanner = FakeScanner()
        guard = AttachmentGuard(
            FakeImages(),
            self.scanner,
            ["pdf", "png", "csv", "xlsx", "exe"],
            max_attachments=3,
            max_attachment_bytes=REPLY_ATTACHMENT_LIMIT,
            max_total_attachment_bytes=REPLY_ATTACHMENT_LIMIT * 2,
        )
        self.service = CaseCommentService(
            tracking=self.s.service,
            emails=self.files,
            notifications=self.outbox,
            storage=self.bucket,
            guard=guard,
            clock=lambda: NOW,
            cc_domains=["jdh.com.co"],
        )
        self.worker = self.s.responsible
        self.s.managers.managers.add(self.s.responsible.user_id)

    async def case(self, requester_email: str | None = CLIENT, creator=None):
        view = await self.s.service.create_case(
            self.s.command(requester_email=requester_email, requester_name="Ana Pérez"),
            creator or self.s.seller,
        )
        return view.case

    def comment_events(self):
        return [e for e in self.s.cases.events if e.event_type == EventType.COMMENT]


def upload(name="soporte.pdf", content=PDF):
    return UploadedFile(name, content)


# --- Comments with files ---


@pytest.mark.asyncio
async def test_a_plain_comment_keeps_working_and_sends_nothing() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(case.id, "Voy a revisarlo", env.s.seller)

    assert result.event.event.note == "Voy a revisarlo"
    assert result.files == [] and result.rejected == [] and result.client_reply is None
    assert env.outbox.items == []
    assert result.event.event.data == {"file_ids": [], "reply_to_client": False}


@pytest.mark.asyncio
async def test_files_are_stored_under_the_case_and_linked_to_the_comment() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(
        case.id, "Adjunto soporte", env.s.seller, [upload(), upload("hoja.csv", b"a;b\n1;2\n")]
    )

    event = result.event.event
    assert [f.original_filename for f in result.files] == ["soporte.pdf", "hoja.csv"]
    assert all(f.comment_event_id == event.id and f.case_id == case.id for f in result.files)
    assert {f.storage_key for f in result.files} == set(env.bucket.objects)
    assert all(k.startswith(f"pqrsf/cases/{case.id}/") for k in env.bucket.objects)
    assert event.data["file_ids"] == [str(f.id) for f in result.files]
    assert [f.content_type for f in result.files] == ["application/pdf", "text/csv"]
    assert env.files.files == result.files


@pytest.mark.asyncio
async def test_a_refused_file_is_reported_and_the_comment_still_saves() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(
        case.id,
        "Con archivos",
        env.s.seller,
        [upload("programa.exe", b"MZ"), upload("falso.pdf", b"no soy pdf"), upload()],
    )

    assert [f.original_filename for f in result.files] == ["soporte.pdf"]
    assert {r["name"]: r["reason"] for r in result.rejected} == {
        "programa.exe": "el contenido no corresponde a su extensión",
        "falso.pdf": "el contenido no corresponde a su extensión",
    }
    assert result.event.event.data["rejected_files"] == result.rejected


@pytest.mark.asyncio
async def test_the_same_file_twice_in_a_comment_is_stored_once() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(
        case.id, "Doble", env.s.seller, [upload(), upload("copia.pdf")]
    )

    assert [f.original_filename for f in result.files] == ["soporte.pdf"]


@pytest.mark.asyncio
async def test_a_bucket_failure_saves_nothing_and_leaves_no_objects() -> None:
    env = Env()
    case = await env.case()
    env.bucket.fail = True

    with pytest.raises(DependencyUnavailableError):
        await env.service.comment(case.id, "Con archivo", env.s.seller, [upload()])

    assert env.comment_events() == [] and env.files.files == [] and env.bucket.objects == {}


@pytest.mark.asyncio
async def test_a_comment_needs_text_and_someone_who_can_see_the_case() -> None:
    env = Env()
    case = await env.case()

    with pytest.raises(InvalidInputError):
        await env.service.comment(case.id, "   ", env.s.seller)
    with pytest.raises(ForbiddenError):
        await env.service.comment(case.id, "Hola", env.s.stranger)


# --- Partial reply to the client ---


@pytest.mark.asyncio
async def test_replying_to_the_client_queues_the_email_with_the_files_and_the_signature() -> None:
    env = Env()
    case = await env.case()
    await env.s.service.assign(case.id, env.worker.user_id, None, env.s.admin)

    result = await env.service.comment(
        case.id, "Ya revisamos tu factura.", env.worker, [upload()], reply_to_client=True
    )

    (item,) = env.outbox.items
    assert item.kind == CLIENT_REPLY and item.recipient_email == CLIENT
    assert item.case_id == case.id and item.status == "pending"
    assert item.subject.startswith(f"Actualización de tu solicitud {case.code}")
    assert item.body.startswith("Hola Ana,\n\nYa revisamos tu factura.")
    assert f"Radicado: {case.code}" in item.body and "equipo" not in item.body.lower()
    # The branded email travels with the queued item and is rendered when it is sent.
    email = item.payload["email"]
    assert email["paragraphs"] == ["Ya revisamos tu factura."]
    assert email["facts"][0] == ["Radicado", case.code]
    assert item.payload["file_ids"] == [str(f.id) for f in result.files]
    assert item.payload["event_id"] == str(result.event.event.id)
    assert item.dedupe_key == f"client_reply:{result.event.event.id}"
    reply = result.event.event.data["client_reply"]
    assert reply == {"to": CLIENT, "cc": [], "notification_id": str(item.id)}
    assert result.client_reply is item


@pytest.mark.asyncio
async def test_a_reply_answers_inside_the_clients_own_conversation() -> None:
    env = Env()
    env.files.latest_graph_id = "graph-msg-9"
    case = await env.case()

    await env.service.comment(case.id, "Respuesta", env.s.admin, reply_to_client=True)

    assert env.outbox.items[0].payload["graph_id"] == "graph-msg-9"


@pytest.mark.asyncio
async def test_a_reply_moves_the_case_to_en_curso_but_never_closes_it() -> None:
    env = Env()
    case = await env.case()

    await env.service.comment(case.id, "Parcial", env.s.admin, reply_to_client=True)

    stored = env.s.cases.cases[case.id]
    assert stored.status.code == "en_curso" and not stored.closed_at


@pytest.mark.asyncio
async def test_only_who_works_the_case_may_reply_to_the_client() -> None:
    env = Env()
    case = await env.case(creator=env.s.seller)  # can see it, cannot work it

    with pytest.raises(ForbiddenError):
        await env.service.comment(case.id, "Hola", env.s.seller, reply_to_client=True)

    assert env.outbox.items == [] and env.comment_events() == []


@pytest.mark.asyncio
async def test_no_reply_is_allowed_on_a_finished_case_but_comments_still_are() -> None:
    env = Env()
    case = await env.case()
    env.s.cases.cases[case.id].status = env.s.finalizada

    with pytest.raises(ConflictError, match="finalizado"):
        await env.service.comment(case.id, "Tarde", env.s.admin, reply_to_client=True)
    await env.service.comment(case.id, "Nota interna", env.s.admin)

    assert env.outbox.items == [] and len(env.comment_events()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("email", [None, ""])
async def test_a_reply_needs_the_requesters_email_and_saves_nothing_without_it(email) -> None:
    env = Env()
    case = await env.case(requester_email=email)

    with pytest.raises(InvalidInputError, match="correo del solicitante"):
        await env.service.comment(case.id, "Hola", env.s.admin, [upload()], reply_to_client=True)

    assert env.comment_events() == [] and env.bucket.objects == {} and env.outbox.items == []


@pytest.mark.asyncio
async def test_the_files_of_a_reply_are_capped_at_what_graph_takes_in_one_request() -> None:
    env = Env()
    case = await env.case()
    big = PDF + b"x" * (REPLY_ATTACHMENT_LIMIT - len(PDF) - 10)
    other = PDF + b"y" * 1000

    with pytest.raises(InvalidInputError, match="3 MB"):
        await env.service.comment(
            case.id, "Pesado", env.s.admin, [upload("a.pdf", big), upload("b.pdf", other)], True
        )
    # The same files are fine in an internal comment.
    result = await env.service.comment(
        case.id, "Pesado", env.s.admin, [upload("a.pdf", big), upload("b.pdf", other)]
    )

    assert len(result.files) == 2


# --- Delivery state ---


@pytest.mark.asyncio
async def test_the_delivery_state_is_listed_and_a_failed_reply_can_be_retried() -> None:
    env = Env()
    case = await env.case()
    await env.service.comment(case.id, "Parcial", env.s.admin, reply_to_client=True)
    (item,) = await env.service.list_client_replies(case.id, env.s.admin)
    item.status, item.attempts, item.error = FAILED, 5, "graph caído"

    retried = await env.service.retry_client_reply(case.id, item.id, env.s.admin)

    assert (retried.status, retried.attempts, retried.error) == ("pending", 0, None)
    assert retried.next_attempt_at == NOW
    with pytest.raises(ConflictError):
        await env.service.retry_client_reply(case.id, item.id, env.s.admin)
    with pytest.raises(NotFoundError):
        await env.service.retry_client_reply(case.id, uuid.uuid4(), env.s.admin)
    with pytest.raises(ForbiddenError):
        await env.service.list_client_replies(case.id, env.s.stranger)


# --- Sending it ---


class FakeMailbox:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.copies: list[list[str]] = []
        self.html: list[bool] = []
        self.reply_found = True

    async def reply(self, mailbox, graph_id, text, files=(), cc=(), html=False):
        self.calls.append(("reply", graph_id, text, [f for f in files if not f.content_id]))
        self.copies.append(list(cc))
        self.html.append(html)
        self.inline = [f for f in files if f.content_id]
        return self.reply_found

    async def send(self, mailbox, to, subject, text, files=(), cc=(), html=False):
        self.calls.append(("send", to, subject, text, [f for f in files if not f.content_id]))
        self.copies.append(list(cc))
        self.html.append(html)
        self.inline = [f for f in files if f.content_id]


def _sender(env: Env, redirect: str | None = None) -> tuple[NotificationService, FakeMailbox]:
    mailbox = FakeMailbox()
    service = NotificationService(
        repository=env.outbox,
        cases=OpenCases(env.s.cases),
        people=env.s.directory,
        team=FakeTeam([]),
        mail=mailbox,
        managers=env.s.managers,
        holidays=FakeHolidays(),
        mailbox="pqrsf@jdh.com.co",
        area_slug="calidad",
        redirect_to=redirect,
        emails=env.files,
        storage=env.bucket,
        clock=lambda: NOW,
    )
    return service, mailbox


async def _queued_reply(env: Env, graph_id: str | None, files=True):
    env.files.latest_graph_id = graph_id
    case = await env.case()
    await env.service.comment(
        case.id, "Listo", env.s.admin, [upload()] if files else [], reply_to_client=True
    )
    return env.outbox.items[0]


@pytest.mark.asyncio
async def test_the_reply_goes_inside_the_clients_thread_with_its_files() -> None:
    env = Env()
    item = await _queued_reply(env, "graph-msg-9")
    service, mailbox = _sender(env)

    assert await service.dispatch() == 1

    ((kind, graph_id, text, files),) = mailbox.calls
    assert (kind, graph_id) == ("reply", "graph-msg-9")
    # Inside a thread the layout goes as a fragment (Graph quotes the original below it).
    assert text.startswith("<table") and "Listo" in text and "<html" not in text
    assert mailbox.html == [True] and [f.content_id for f in mailbox.inline] == ["jdh-logo"]
    assert files == [OutgoingFile("soporte.pdf", "application/pdf", PDF)]
    assert item.status == "sent"


@pytest.mark.asyncio
async def test_a_deleted_original_falls_back_to_a_new_email() -> None:
    env = Env()
    item = await _queued_reply(env, "graph-msg-9")
    service, mailbox = _sender(env)
    mailbox.reply_found = False

    await service.dispatch()

    assert [c[0] for c in mailbox.calls] == ["reply", "send"]
    kind, to, subject, _, files = mailbox.calls[1]
    assert (to, subject) == (CLIENT, item.subject) and len(files) == 1
    assert item.status == "sent"


@pytest.mark.asyncio
async def test_a_case_that_did_not_come_by_email_is_answered_with_a_new_email() -> None:
    env = Env()
    item = await _queued_reply(env, None, files=False)
    service, mailbox = _sender(env)

    await service.dispatch()

    ((kind, to, subject, text, files),) = mailbox.calls
    assert (kind, to, subject, files) == ("send", CLIENT, item.subject, [])
    assert text.startswith("<!DOCTYPE html>") and 'src="cid:jdh-logo"' in text
    assert "Listo" in text and item.payload["email"]["facts"][0][1] in text
    assert mailbox.html == [True] and len(mailbox.inline) == 1


@pytest.mark.asyncio
async def test_the_testing_redirect_never_reaches_the_client_nor_replies_in_its_thread() -> None:
    env = Env()
    item = await _queued_reply(env, "graph-msg-9")
    service, mailbox = _sender(env, redirect="analista.ti@jdh.com.co")

    await service.dispatch()

    ((kind, to, subject, _, files),) = mailbox.calls
    assert kind == "send" and to == "analista.ti@jdh.com.co"
    assert subject == f"[PRUEBA para {CLIENT}] {item.subject}" and len(files) == 1


@pytest.mark.asyncio
async def test_a_reply_copies_the_people_of_the_organization() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(
        case.id,
        "Te escribimos",
        env.s.admin,
        reply_to_client=True,
        cc=[" Luis.Mora@JDH.com.co ", "luis.mora@jdh.com.co", CLIENT, "ana.ruiz@jdh.com.co"],
    )

    (item,) = env.outbox.items
    # Unique, lower-case, and the client is not copied on their own email.
    assert item.payload["cc"] == ["luis.mora@jdh.com.co", "ana.ruiz@jdh.com.co"]
    assert result.event.event.data["client_reply"]["cc"] == item.payload["cc"]
    service, mailbox = _sender(env)
    await service.dispatch()
    assert mailbox.copies == [["luis.mora@jdh.com.co", "ana.ruiz@jdh.com.co"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cc", "reason"),
    [
        (["externo@proveedor.com"], "no es de la organización"),
        (["no-es-correo"], "no es válido"),
        ([f"p{i}@jdh.com.co" for i in range(6)], "Máximo 5"),
    ],
)
async def test_the_copy_only_takes_valid_people_of_the_organization(cc, reason) -> None:
    env = Env()
    case = await env.case()

    with pytest.raises(InvalidInputError, match=reason):
        await env.service.comment(case.id, "Hola", env.s.admin, reply_to_client=True, cc=cc)

    assert env.outbox.items == [] and env.comment_events() == []


@pytest.mark.asyncio
async def test_a_copy_without_a_reply_to_the_client_is_refused() -> None:
    env = Env()
    case = await env.case()

    with pytest.raises(InvalidInputError, match="solo aplica al responder"):
        await env.service.comment(case.id, "Nota", env.s.admin, cc=["luis@jdh.com.co"])


@pytest.mark.asyncio
async def test_the_testing_redirect_copies_nobody() -> None:
    env = Env()
    await env.service.comment(
        (await env.case()).id,
        "Hola",
        env.s.admin,
        reply_to_client=True,
        cc=["luis@jdh.com.co"],
    )
    service, mailbox = _sender(env, redirect="analista.ti@jdh.com.co")

    await service.dispatch()

    assert mailbox.copies == [[]]


@pytest.mark.asyncio
async def test_a_failed_send_keeps_the_reply_pending_for_a_retry() -> None:
    env = Env()
    item = await _queued_reply(env, None)
    service, mailbox = _sender(env)

    async def broken(*args, **kwargs):
        raise RuntimeError("graph caído")

    mailbox.send = broken

    assert await service.dispatch() == 0
    assert (item.status, item.attempts) == ("pending", 1) and "graph caído" in item.error


def test_a_notification_defaults_to_an_empty_payload() -> None:
    item = Notification(
        id=uuid.uuid4(),
        kind=CLIENT_REPLY,
        recipient_email=CLIENT,
        recipient_user_id=None,
        subject="s",
        body="b",
        case_id=None,
        dedupe_key=None,
    )

    assert item.payload == {} and replace(item, subject="x").payload == {}


@pytest.mark.asyncio
async def test_a_comment_keeps_its_lines_in_the_timeline_and_in_the_email() -> None:
    env = Env()
    case = await env.case()

    result = await env.service.comment(
        case.id,
        "Revisamos tu caso.\r\n\r\n\r\n\r\nTe confirmamos:\r\n"
        "-  Ya  tenemos tu factura.\r\n- Falta la nota.  ",
        env.s.admin,
        reply_to_client=True,
    )

    expected = "Revisamos tu caso.\n\nTe confirmamos:\n- Ya tenemos tu factura.\n- Falta la nota."
    assert result.event.event.note == expected
    assert expected in env.outbox.items[0].body
