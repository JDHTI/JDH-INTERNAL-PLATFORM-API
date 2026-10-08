"""HTTP contract of the case comment: JSON or multipart, files and the client reply."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.main import create_app
from app.modules.pqrsf.application.comments import CommentResult, UploadedFile
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.domain.email import CaseAttachment
from app.modules.pqrsf.domain.notifications import CLIENT_REPLY, Notification
from app.modules.pqrsf.domain.tracking import CaseEvent
from fastapi.testclient import TestClient

from tests.modules.projects.test_live_http import StubIdentity, settings  # noqa: F401

NOW = datetime(2026, 10, 8, 18, tzinfo=UTC)
CASE_ID = uuid.uuid4()


class FakeComments:
    """Records what the router handed over and answers with a fixed result."""

    def __init__(self) -> None:
        self.guard = SimpleNamespace(max_attachment_bytes=1000)
        self.calls: list[tuple] = []

    async def comment(self, case_id, body, principal, files=(), reply_to_client=False):
        self.calls.append((case_id, body, list(files), reply_to_client))
        event = CaseEvent(
            id=uuid.uuid4(),
            case_id=case_id,
            event_type="comment",
            actor_user_id=principal.user_id,
            from_status_id=None,
            to_status_id=None,
            note=body,
            data={"reply_to_client": reply_to_client},
            created_at=NOW,
        )
        stored = [
            CaseAttachment(
                id=uuid.uuid4(),
                case_id=case_id,
                message_id=None,
                original_filename=f.filename,
                extension="pdf",
                content_type="application/pdf",
                size_bytes=len(f.content),
                checksum_sha256="0" * 64,
                storage_key=f"pqrsf/cases/{case_id}/x.pdf",
                created_at=NOW,
                comment_event_id=event.id,
            )
            for f in files
            if f.filename.endswith(".pdf")
        ]
        rejected = [
            {"name": f.filename, "reason": "tipo de archivo no permitido"}
            for f in files
            if not f.filename.endswith(".pdf")
        ]
        reply = (
            Notification(
                id=uuid.uuid4(),
                kind=CLIENT_REPLY,
                recipient_email="cliente@empresa.com",
                recipient_user_id=None,
                subject="s",
                body="b",
                case_id=case_id,
                dedupe_key=None,
                created_at=NOW,
                payload={"event_id": str(event.id)},
            )
            if reply_to_client
            else None
        )
        return CommentResult(EventView(event, None), stored, rejected, reply)


@pytest.fixture
def comments():
    return FakeComments()


class OpenRedis:
    """Lets every request through, so the traffic limiter needs no Redis."""

    async def allow_sliding_window(self, bucket, limit, window_seconds):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        pass


@pytest.fixture
def client(settings, comments):  # noqa: F811
    app = create_app(settings)
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(OpenRedis()),
        container.identity_service.override(StubIdentity(settings)),
        container.pqrsf_comment_service.override(comments),
        TestClient(app) as test_client,
    ):
        test_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "valid")
        yield test_client


URL = f"/api/v1/pqrsf/cases/{CASE_ID}/comments"


def test_a_json_comment_keeps_the_old_contract_and_adds_empty_extras(client, comments) -> None:
    response = client.post(URL, json={"body": "Hola"})

    assert response.status_code == 201
    body = response.json()
    assert body["event_type"] == "comment" and body["note"] == "Hola"
    assert body["files"] == [] and body["rejected_files"] == [] and body["client_reply"] is None
    assert comments.calls == [(CASE_ID, "Hola", [], False)]


def test_a_json_comment_can_ask_for_the_client_reply(client, comments) -> None:
    response = client.post(URL, json={"body": "Hola", "reply_to_client": True})

    assert response.status_code == 201
    reply = response.json()["client_reply"]
    assert reply["to"] == "cliente@empresa.com" and reply["status"] == "pending"
    assert reply["event_id"] == response.json()["id"]
    assert comments.calls[0][3] is True


def test_a_multipart_comment_carries_the_files_and_the_flag(client, comments) -> None:
    response = client.post(
        URL,
        data={"body": "Con archivos", "reply_to_client": "true"},
        files=[
            ("files", ("soporte.pdf", b"%PDF-1.7 hola", "application/pdf")),
            ("files", ("programa.exe", b"MZ", "application/octet-stream")),
        ],
    )

    assert response.status_code == 201
    body = response.json()
    assert [f["filename"] for f in body["files"]] == ["soporte.pdf"]
    assert body["files"][0]["comment_event_id"] == body["id"]
    assert body["rejected_files"] == [
        {"name": "programa.exe", "reason": "tipo de archivo no permitido"}
    ]
    assert body["client_reply"]["id"]
    _, text, files, reply = comments.calls[0]
    assert text == "Con archivos" and reply is True
    assert files == [
        UploadedFile("soporte.pdf", b"%PDF-1.7 hola"),
        UploadedFile("programa.exe", b"MZ"),
    ]


def test_a_multipart_comment_without_the_flag_is_internal(client, comments) -> None:
    client.post(
        URL, data={"body": "Interno"}, files=[("files", ("a.pdf", b"%PDF-", "application/pdf"))]
    )

    assert comments.calls[0][3] is False


def test_a_file_bigger_than_the_limit_reaches_the_service_marked_as_too_big(
    client, comments
) -> None:
    client.post(
        URL, data={"body": "Grande"}, files=[("files", ("a.pdf", b"x" * 5000, "application/pdf"))]
    )

    # The router never reads more than limit + 1 bytes: the service refuses it by size.
    assert len(comments.calls[0][2][0].content) == 1001


def test_an_invalid_json_body_is_a_validation_error(client, comments) -> None:
    for payload in ({"body": ""}, {"nope": 1}, {"body": "x" * 5001}):
        assert client.post(URL, json=payload).status_code == 422
    assert comments.calls == []


def test_the_openapi_describes_both_body_formats(client) -> None:
    content = client.app.openapi()["paths"][URL.replace(str(CASE_ID), "{case_id}")]["post"][
        "requestBody"
    ]["content"]

    assert set(content) == {"application/json", "multipart/form-data"}
    assert "files" in content["multipart/form-data"]["schema"]["properties"]
