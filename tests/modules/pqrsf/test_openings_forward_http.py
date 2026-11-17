"""HTTP contract of the case openings and of forwarding a supplier's answer to the customer."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConflictError
from app.main import create_app
from app.modules.identity.domain.principal import DirectoryUser
from app.modules.mail.layout import BrandedEmail
from app.modules.pqrsf.application.dto import CaseOpenings, EventView, PersonOpenings
from app.modules.pqrsf.application.forwards import ForwardPreview, ForwardResult
from app.modules.pqrsf.domain.notifications import CUSTOMER_FORWARD, Notification
from app.modules.pqrsf.domain.timeline import ACCESS, EventSummary
from app.modules.pqrsf.domain.tracking import CaseEvent
from tests.modules.pqrsf.test_comments_http import OpenRedis
from tests.modules.projects.test_live_http import StubIdentity, settings  # noqa: F401

NOW = datetime(2026, 11, 9, 15, tzinfo=UTC)
CASE_ID = uuid.uuid4()
EVENT_ID = uuid.uuid4()
ANA = DirectoryUser(id=uuid.uuid4(), email="ana@jdh.com.co", display_name="Ana", is_active=True)


def _event(event_type: str, title: str, **fields) -> EventView:
    event = CaseEvent(
        id=uuid.uuid4(),
        case_id=CASE_ID,
        event_type=event_type,
        actor_user_id=ANA.id,
        from_status_id=None,
        to_status_id=None,
        note=fields.get("note"),
        data=fields.get("data", {}),
        created_at=NOW,
    )
    return EventView(event, ANA, EventSummary("user", "Ana", title, fields.get("category", ACCESS)))


class FakeTracking:
    async def case_openings(self, case_id, principal):
        assert case_id == CASE_ID
        return CaseOpenings(
            people=[PersonOpenings(ANA.id, ANA, NOW, NOW, 3)],
            entries=[_event("viewed", "Abrió el caso")],
        )


class FakeForwards:
    def __init__(self) -> None:
        self.notes: list[str | None] = []
        self.fail = False

    async def preview(self, case_id, event_id, note, principal):
        self.notes.append(note)
        return ForwardPreview(
            to="cliente@x.co",
            sender="pqrsf@jdh.com.co",
            subject="Novedad caso Q26AC0001",
            content=BrandedEmail(title="Novedad de tu queja", paragraphs=("Hola",), notice="Aviso"),
            attachments=["lote.pdf"],
        )

    async def forward(self, case_id, event_id, note, principal):
        if self.fail:
            raise ConflictError("Esa respuesta ya se envió al cliente")
        self.notes.append(note)
        notification = Notification(
            id=uuid.uuid4(),
            kind=CUSTOMER_FORWARD,
            recipient_email="cliente@x.co",
            recipient_user_id=None,
            subject="Novedad caso Q26AC0001",
            body="b",
            case_id=case_id,
            dedupe_key=None,
            created_at=NOW,
        )
        event = _event(
            "forwarded_to_customer",
            "Envió al cliente la respuesta de p@x.co",
            note=note,
            data={"source_event_id": str(event_id), "to": "cliente@x.co"},
            category="communication",
        )
        return ForwardResult(event, notification)


@pytest.fixture
def forwards():
    return FakeForwards()


@pytest.fixture
def client(settings, forwards):  # noqa: F811
    app = create_app(settings)
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(OpenRedis()),
        container.identity_service.override(StubIdentity(settings)),
        container.pqrsf_tracking_service.override(FakeTracking()),
        container.pqrsf_forward_service.override(forwards),
        TestClient(app) as test_client,
    ):
        test_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "valid")
        yield test_client


FORWARD = f"/api/v1/pqrsf/cases/{CASE_ID}/events/{EVENT_ID}/forward-to-customer"


def test_the_openings_answer_people_and_entries(client) -> None:
    response = client.get(f"/api/v1/pqrsf/cases/{CASE_ID}/openings")

    assert response.status_code == 200
    body = response.json()
    (person,) = body["people"]
    assert person["user"] == {"id": str(ANA.id), "display_name": "Ana", "email": ANA.email}
    assert person["visits"] == 3 and person["first_opened_at"] and person["last_opened_at"]
    (entry,) = body["entries"]
    assert entry["event_type"] == "viewed" and entry["title"] == "Abrió el caso"
    assert entry["forwardable"] is False and entry["sender_role"] is None


def test_the_preview_takes_an_optional_note(client, forwards) -> None:
    empty = client.post(f"{FORWARD}/preview")
    null = client.post(f"{FORWARD}/preview", json={"note": None})
    with_note = client.post(f"{FORWARD}/preview", json={"note": "Te cuento"})

    assert empty.status_code == null.status_code == with_note.status_code == 200
    assert forwards.notes == [None, None, "Te cuento"]
    body = with_note.json()
    assert set(body) == {"to", "sender", "subject", "html", "text", "attachments"}
    assert body["html"].startswith("<!DOCTYPE html>") and "Aviso" in body["html"]
    assert body["attachments"] == ["lote.pdf"]


def test_forwarding_answers_the_event_and_its_delivery(client, forwards) -> None:
    response = client.post(FORWARD, json={"note": "Nota"})

    assert response.status_code == 201
    body = response.json()
    assert body["event_type"] == "forwarded_to_customer" and body["note"] == "Nota"
    assert body["delivery"]["to"] == "cliente@x.co" and body["delivery"]["status"] == "pending"

    # What the UI sends when nobody wrote a note.
    assert client.post(FORWARD, json={"note": None}).status_code == 201
    assert forwards.notes[-1] is None

    forwards.fail = True
    assert client.post(FORWARD).status_code == 409


def test_a_note_too_long_is_a_validation_error(client) -> None:
    assert client.post(FORWARD, json={"note": "x" * 2001}).status_code == 422
