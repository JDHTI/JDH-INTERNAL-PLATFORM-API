import copy
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.pqrsf.application.feedback import FeedbackService
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import HANDLING_ACKNOWLEDGE
from app.modules.pqrsf.domain.feedback import (
    ORIGIN_FORM,
    SKIP_RECIPIENT_LIMIT,
    SKIP_SAME_CONVERSATION,
    Feedback,
    FeedbackFilters,
)
from app.modules.pqrsf.domain.notifications import FEEDBACK_ACK
from app.modules.sites.application.service import SiteService

from tests.modules.fakes import FakeDirectory
from tests.modules.pqrsf.test_email_intake import NOW, Intake, file
from tests.modules.pqrsf.test_service import (
    FakePqrsfRepository,
    FakeSiteRepository,
    _create,
    _payload,
    _service,
)
from tests.modules.pqrsf.test_tracking import FakeManagers

SUGGESTION = (
    "Tipo: Sugerencia\nNombre: Ana Pérez\nRazón social: XYZ SAS\n"
    "Motivo: Más horarios\nDescripción:\nAbrir también los sábados."
)


class FakeFeedbackRepository:
    def __init__(self) -> None:
        self.items: dict[uuid.UUID, Feedback] = {}
        self.files: list = []

    async def add(self, feedback):
        for item in self.items.values():
            if (feedback.request_id and item.request_id == feedback.request_id) or (
                feedback.email_message_id and item.email_message_id == feedback.email_message_id
            ):
                return copy.deepcopy(item), False
        self.items[feedback.id] = copy.deepcopy(feedback)
        return copy.deepcopy(feedback), True

    async def get(self, feedback_id):
        item = self.items.get(feedback_id)
        if item is None:
            return None
        found = copy.deepcopy(item)
        found.files = [f for f in self.files if f.feedback_id == feedback_id]
        found.files_count = len(found.files)
        return found

    async def list_feedback(self, filters, page, size):
        raise NotImplementedError

    async def summarize(self):
        raise NotImplementedError

    async def mark_read(self, feedback_id, user_id, at):
        item = self.items[feedback_id]
        if item.read_at is None:
            item.read_at, item.read_by_user_id = at, user_id

    async def set_reply(self, feedback_id, notification_id, skipped_reason):
        item = self.items[feedback_id]
        item.reply_notification_id, item.reply_skipped_reason = notification_id, skipped_reason

    async def add_files(self, files):
        self.files.extend(files)

    async def list_files(self, feedback_id):
        return [f for f in self.files if f.feedback_id == feedback_id]

    async def get_file(self, feedback_id, file_id):
        return next(
            (f for f in self.files if f.id == file_id and f.feedback_id == feedback_id), None
        )


class FakeOutbox:
    def __init__(self) -> None:
        self.items: list = []

    async def enqueue(self, notification):
        if notification.dedupe_key and any(
            n.dedupe_key == notification.dedupe_key for n in self.items
        ):
            return False
        self.items.append(notification)
        return True

    async def get_notification(self, notification_id):
        return next((n for n in self.items if n.id == notification_id), None)

    async def save_notification(self, notification):
        pass  # the service changes the stored object itself

    async def count_recent(self, kind, recipient_email, since):
        return sum(1 for n in self.items if n.kind == kind and n.recipient_email == recipient_email)


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def get_object(self, key):
        return self.objects[key]


def _feedback_service(**overrides):
    values = {
        "repository": FakeFeedbackRepository(),
        "notifications": FakeOutbox(),
        "storage": FakeBucket(),
        "managers": FakeManagers(),
        "people": FakeDirectory(),
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return FeedbackService(**values)


def _intake(**overrides) -> tuple[Intake, FeedbackService]:
    t = Intake()
    # Suggestions and compliments are the types that are only thanked.
    sugerencia = replace(
        t.setup.felicitacion, id=uuid.uuid4(), code="sugerencia", name="Sugerencia"
    )
    sugerencia.prefix = "S"
    for kind in (t.setup.felicitacion, sugerencia):
        kind.handling = HANDLING_ACKNOWLEDGE
    t.setup.requests.types[sugerencia.id] = sugerencia
    wakes: list[int] = []
    service = _feedback_service(wake=lambda: wakes.append(1), **overrides)
    service.wakes = wakes
    t.service.feedback = service
    t.service.storage = service.storage = t.storage
    return t, service


@pytest.mark.asyncio
async def test_a_suggestion_by_email_is_stored_apart_and_thanked_in_the_thread() -> None:
    t, feedback = _intake()
    t.mail.add(t.message(SUGGESTION, conversation_id="thread"))

    report = await t.service.sync()

    assert t.cases() == []  # no case was opened
    assert report.created == 1
    (stored,) = feedback.repository.items.values()
    assert (stored.request_type.code, stored.origin) == ("sugerencia", "email")
    assert (stored.full_name, stored.email) == ("Ana Pérez", "ana@cliente.com")
    assert stored.subject == "Una solicitud"
    assert stored.body == "Abrir también los sábados."
    (thanks,) = feedback.notifications.items
    assert thanks.kind == FEEDBACK_ACK and thanks.recipient_email == "ana@cliente.com"
    assert "Recibimos tu sugerencia" in thanks.subject
    assert thanks.body.rstrip().endswith("PQRSF JDH") and "Hola Ana," in thanks.body
    # The thank-you answers the client's own email, and the sender is told to hurry.
    assert thanks.payload == {"feedback_id": str(stored.id), "graph_id": "g1"}
    assert feedback.wakes == [1]
    assert stored.reply_notification_id == thanks.id
    # The acknowledgment of a case is not sent: the thank-you replaces it.
    assert t.mail.replies == []
    assert t.mail.marked == [("g1", "Sugerencia")]
    row = t.emails.messages["<m1@x>"]
    assert row.case_id is None and row.detail["feedback_id"] == str(stored.id)


@pytest.mark.asyncio
async def test_a_compliment_gets_its_own_thank_you() -> None:
    t, feedback = _intake()
    t.mail.add(t.message("Tipo: Felicitación\nDescripción:\nExcelente atención."))

    await t.service.sync()

    (thanks,) = feedback.notifications.items
    assert thanks.subject == "Recibimos tu felicitación"
    assert "felicitación" in thanks.body and t.cases() == []


@pytest.mark.asyncio
async def test_files_of_a_suggestion_are_stored_under_it_not_under_a_case() -> None:
    t, feedback = _intake()
    t.mail.add(
        t.message(SUGGESTION, has_attachments=True),
        {file("idea.pdf"): b"%PDF-1.7 mi idea"},
    )

    await t.service.sync()

    (stored,) = feedback.repository.items.values()
    (saved,) = feedback.repository.files
    assert saved.feedback_id == stored.id and saved.original_filename == "idea.pdf"
    assert saved.storage_key == f"pqrsf/feedback/{stored.id}/{saved.id}.pdf"
    assert t.storage.objects[saved.storage_key] == b"%PDF-1.7 mi idea"
    assert t.emails.attachments == []  # nothing went to the case attachments


@pytest.mark.asyncio
async def test_answering_the_thank_you_does_not_open_a_petition_or_thank_again() -> None:
    t, feedback = _intake()
    t.mail.add(t.message(SUGGESTION, conversation_id="thread"))
    await t.service.sync()
    # "Gracias" has no template: it must stay with the suggestion, not become a petition.
    t.mail.add(t.message("Muchas gracias por responder", conversation_id="thread"))

    await t.service.sync()

    assert t.cases() == []
    first, second = sorted(feedback.repository.items.values(), key=lambda f: f.created_at)
    assert second.request_type.code == "sugerencia"
    assert len(feedback.notifications.items) == 1  # one thank-you per conversation
    assert second.reply_skipped_reason == SKIP_SAME_CONVERSATION


@pytest.mark.asyncio
async def test_the_same_email_is_never_stored_or_thanked_twice() -> None:
    t, feedback = _intake()
    original = t.message(SUGGESTION)
    t.mail.add(original)
    await t.service.sync()
    t.mail.add(original)

    await t.service.sync()

    assert len(feedback.repository.items) == 1 and len(feedback.notifications.items) == 1


@pytest.mark.asyncio
async def test_a_failed_step_resumes_without_duplicating_the_message() -> None:
    t, feedback = _intake()
    t.mail.add(t.message(SUGGESTION, has_attachments=True), {file("idea.pdf"): b"%PDF-1.7 x"})
    t.scanner.down = 1  # the antivirus is down for the first attempt
    await t.service.sync()
    assert feedback.notifications.items == []

    await t.service.sync()  # next pass retries

    assert len(feedback.repository.items) == 1
    assert len(feedback.repository.files) == 1
    assert len(feedback.notifications.items) == 1


@pytest.mark.asyncio
async def test_a_petition_by_email_still_opens_a_case_and_is_not_thanked() -> None:
    t, feedback = _intake()
    t.mail.add(t.message("Tipo: Petición\nDescripción:\nNecesito un certificado."))

    await t.service.sync()

    assert len(t.cases()) == 1 and feedback.repository.items == {}
    assert feedback.notifications.items == []


@pytest.mark.asyncio
async def test_an_address_gets_a_limited_number_of_thank_yous_a_day() -> None:
    t, feedback = _intake(max_replies_per_email=2)
    for _ in range(3):
        t.mail.add(t.message(SUGGESTION))  # a distinct email and conversation each time
    await t.service.sync()

    reasons = sorted(f.reply_skipped_reason or "" for f in feedback.repository.items.values())
    assert len(feedback.repository.items) == 3  # all three are kept for the team
    assert len(feedback.notifications.items) == 2
    assert reasons == ["", "", SKIP_RECIPIENT_LIMIT]


@pytest.mark.asyncio
async def test_no_thank_you_is_queued_while_sending_is_switched_off() -> None:
    t, feedback = _intake(replies_enabled=False)
    t.mail.add(t.message(SUGGESTION))

    await t.service.sync()

    (stored,) = feedback.repository.items.values()
    assert feedback.notifications.items == [] and stored.reply_skipped_reason == "disabled"


@pytest.mark.asyncio
async def test_without_the_feedback_service_every_type_still_opens_a_case() -> None:
    t, _ = _intake()
    t.service.feedback = None
    t.mail.add(t.message(SUGGESTION))

    await t.service.sync()

    assert len(t.cases()) == 1


# --- Public form ---


def _form_service(feedback: FeedbackService | None):
    petition_service, request_type, site = _service(petition=False)
    request_type.handling = HANDLING_ACKNOWLEDGE
    service = PqrsfService(
        FakePqrsfRepository(request_type),
        SiteService(FakeSiteRepository(site)),
        feedback=feedback,
    )
    return service, request_type, site


@pytest.mark.asyncio
async def test_a_suggestion_from_the_form_is_thanked_and_has_no_consecutive() -> None:
    feedback = _feedback_service()
    service, request_type, site = _form_service(feedback)
    values = _payload(request_type, site, str(uuid.uuid4()))

    result = await _create(service, request_type, site, values)

    assert (result.handled_as, result.tracking_code) == ("feedback", None)
    (stored,) = feedback.repository.items.values()
    assert (stored.origin, stored.request_id) == (ORIGIN_FORM, result.request.id)
    assert (stored.site, stored.business_name) == (site, "Comercial SAS")
    (thanks,) = feedback.notifications.items
    # The form has no email conversation, so the thank-you is a new email.
    assert thanks.recipient_email == "ana@example.com" and thanks.payload["graph_id"] is None


@pytest.mark.asyncio
async def test_resubmitting_the_form_does_not_thank_twice() -> None:
    feedback = _feedback_service()
    service, request_type, site = _form_service(feedback)
    values = _payload(request_type, site, str(uuid.uuid4()))

    await _create(service, request_type, site, values)
    again = await _create(service, request_type, site, values)

    assert again.replayed is True and again.handled_as == "feedback"
    assert len(feedback.repository.items) == 1 and len(feedback.notifications.items) == 1


@pytest.mark.asyncio
async def test_a_failure_storing_the_message_never_fails_the_submission() -> None:
    feedback = _feedback_service()

    async def broken(*args, **kwargs):
        raise RuntimeError("db caída")

    feedback.repository.add = broken
    service, request_type, site = _form_service(feedback)

    result = await _create(
        service, request_type, site, _payload(request_type, site, str(uuid.uuid4()))
    )

    assert result.request.id is not None


@pytest.mark.asyncio
async def test_a_type_that_opens_cases_is_not_diverted() -> None:
    feedback = _feedback_service()
    service, request_type, site = _service()  # petition: handling is "case"
    service.feedback = feedback
    result = await _create(
        service, request_type, site, _payload(request_type, site, str(uuid.uuid4()))
    )

    assert result.handled_as == "case" and feedback.repository.items == {}


# --- Manager screens ---


@pytest.mark.asyncio
async def test_only_managers_read_and_the_first_opening_marks_it_read() -> None:
    directory = FakeDirectory()
    admin = directory.sign_in("calidad@jdh.com.co", admin=True)
    stranger = directory.sign_in("otro@jdh.com.co")
    managers = FakeManagers()
    feedback = _feedback_service(managers=managers, people=directory)
    t, _ = _intake()
    stored = await feedback.record(
        request_type=next(iter(t.setup.requests.types.values())),
        origin=ORIGIN_FORM,
        full_name="Ana",
        email="ana@cliente.com",
        body="Hola",
    )

    with pytest.raises(ForbiddenError):
        await feedback.open(stored.id, stranger)
    with pytest.raises(ForbiddenError):
        await feedback.list_feedback(stranger, FeedbackFilters(), 1, 20)
    with pytest.raises(NotFoundError):
        await feedback.open(uuid.uuid4(), admin)

    first = await feedback.open(stored.id, admin)
    assert first.read_by_user_id == admin.user_id and first.read_at == NOW
    assert (await feedback.reader(first)).email == "calidad@jdh.com.co"


# --- Not a type of case ---


@pytest.mark.asyncio
async def test_suggestions_and_compliments_are_not_types_of_the_case_screens() -> None:
    t = Intake()
    t.setup.felicitacion.handling = HANDLING_ACKNOWLEDGE

    async def nothing(*args, **kwargs):
        return []

    for name in ("list_statuses", "list_dependencies", "list_document_types"):
        setattr(t.setup.cases, name, nothing)
    t.setup.service.sites = SimpleNamespace(list_sites=nothing)

    catalog = await t.setup.service.catalog(t.setup.admin)

    assert [kind.code for kind in catalog.request_types] == ["peticion"]
    with pytest.raises(InvalidInputError, match="propia sección"):
        await t.setup.service.create_case(
            t.setup.command(request_type_id=t.setup.felicitacion.id), t.setup.admin
        )
