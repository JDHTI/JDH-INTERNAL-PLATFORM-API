"""HTTP contract of the improvement opportunity (OM): template, consecutive, case OMs."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.pqrsf.application.improvement_opportunities import OmFile
from app.modules.pqrsf.domain.improvement import (
    DocumentTemplate,
    ImprovementOpportunity,
    OmHistory,
    OmHistoryRow,
    Signer,
)
from tests.modules.pqrsf.test_comments_http import OpenRedis
from tests.modules.projects.test_live_http import StubIdentity, settings  # noqa: F401

NOW = datetime(2026, 11, 17, 15, tzinfo=UTC)
CASE_ID = uuid.uuid4()
OM_ID = uuid.uuid4()


def _om(**changes) -> ImprovementOpportunity:
    values = dict(
        id=OM_ID,
        case_id=CASE_ID,
        number=1181,
        code="1181BOG",
        supplier_code="P1",
        supplier_name="Parmalat",
        signer=Signer(None, "Cristian", "Analista", "calidad@jdh.com.co", "317"),
        docx_key="k.docx",
        pdf_key="k.pdf",
        generated_at=NOW,
        created_at=NOW,
    )
    values.update(changes)
    return ImprovementOpportunity(**values)


class FakeOms:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_templates(self, kind, principal):
        return [
            DocumentTemplate(uuid.uuid4(), kind, "V02", "om.docx", "k", 10, "c", True, None, NOW)
        ]

    async def upload_template(self, kind, filename, content, label, principal):
        self.calls.append(("upload", filename, len(content), label))
        return DocumentTemplate(
            uuid.uuid4(), kind, label, filename, "k", len(content), "c", True, None, NOW
        )

    async def next_number(self, principal):
        return 1181

    async def set_next_number(self, value, principal):
        self.calls.append(("number", value))
        return value

    async def list_for_case(self, case_id, principal):
        return [_om()]

    async def generate(self, case_id, command, principal):
        self.calls.append(("generate", command))
        return _om(immediate_actions=command.immediate_actions)

    async def document(self, case_id, om_id, fmt, principal):
        return OmFile(f"OM 1181BOG.{fmt}", "application/pdf", b"%PDF-1.7")

    async def upload_edited(self, case_id, om_id, filename, content, principal):
        return _om(edited=True)

    async def retry_pdf(self, case_id, om_id, principal):
        return _om()

    async def history(self, filters, page, size, principal):
        self.calls.append(("history", filters, page, size))
        sent = _om(status="sent", sent_at=datetime(2026, 11, 10, 15, tzinfo=UTC))
        answered = _om(id=uuid.uuid4(), status="sent", sent_at=NOW)
        rows = [
            OmHistoryRow(
                sent, "Q26BG0001", "En curso", False, "Cafam", "Bogotá", None, ("a@p.co",)
            ),
            OmHistoryRow(answered, "Q26BG0002", "Finalizada", True, None, None, NOW, ()),
        ]
        return OmHistory(rows, 2, 0, 1, 1)


@pytest.fixture
def oms():
    return FakeOms()


@pytest.fixture
def client(settings, oms):  # noqa: F811
    app = create_app(settings)
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(OpenRedis()),
        container.identity_service.override(StubIdentity(settings)),
        container.pqrsf_om_service.override(oms),
        TestClient(app) as test_client,
    ):
        test_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "valid")
        yield test_client


BASE = f"/api/v1/pqrsf/cases/{CASE_ID}/improvement-opportunities"


def test_generating_an_om_answers_its_data_and_documents(client, oms) -> None:
    response = client.post(
        BASE,
        json={
            "supplier_code": "P1",
            "immediate_actions": "Se aprobó la OR",
            "evidence_ids": [str(uuid.uuid4())],
            "document_date": "2026-11-10",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "1181BOG" and body["has_pdf"] is True and body["status"] == "draft"
    assert body["signer"]["position"] == "Analista"
    command = oms.calls[0][1]
    assert command.immediate_actions == "Se aprobó la OR" and len(command.evidence_ids) == 1
    assert client.post(BASE, json={"evidence_ids": [str(uuid.uuid4())] * 7}).status_code == 422


def test_the_documents_download_with_their_name(client) -> None:
    response = client.get(f"{BASE}/{OM_ID}/document", params={"format": "pdf", "inline": "true"})

    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert response.headers["content-disposition"].startswith(
        "inline; filename*=UTF-8''OM%201181BOG.pdf"
    )
    assert client.get(f"{BASE}/{OM_ID}/document", params={"format": "xls"}).status_code == 422


def test_an_edited_word_and_a_template_go_as_multipart(client, oms) -> None:
    edited = client.put(
        f"{BASE}/{OM_ID}/document",
        files={"file": ("OM.docx", b"PK\x03\x04", "application/octet-stream")},
    )
    template = client.post(
        "/api/v1/pqrsf/templates/om",
        files={"file": ("om.docx", b"PK\x03\x04", "application/octet-stream")},
        data={"version_label": "V03"},
    )
    missing = client.post("/api/v1/pqrsf/templates/om", data={"version_label": "V03"})

    assert edited.status_code == 200 and edited.json()["edited"] is True
    assert template.status_code == 201 and template.json()["version_label"] == "V03"
    assert ("upload", "om.docx", 4, "V03") in oms.calls
    assert missing.status_code == 422
    assert client.get("/api/v1/pqrsf/templates/otra").status_code == 422


def test_the_om_consecutive_is_read_and_set(client, oms) -> None:
    assert client.get("/api/v1/pqrsf/om/settings").json() == {"next_number": 1181}
    assert client.put("/api/v1/pqrsf/om/settings", json={"next_number": 1500}).status_code == 200
    assert ("number", 1500) in oms.calls
    assert client.put("/api/v1/pqrsf/om/settings", json={"next_number": 0}).status_code == 422


def test_the_history_lists_the_oms_with_their_case_and_reply(client, oms) -> None:
    response = client.get(
        "/api/v1/pqrsf/improvement-opportunities",
        params={"status": "sent", "replied": "false", "supplier": " Parmalat ", "page": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["awaiting_reply"], body["replied"]) == (2, 1, 1)
    waiting, answered = body["items"]
    assert waiting["case_code"] == "Q26BG0001" and waiting["sent_to"] == ["a@p.co"]
    assert waiting["replied_at"] is None and waiting["days_waiting"] >= 7
    assert answered["case_closed"] is True and answered["days_waiting"] is None
    _, filters, page, size = next(c for c in oms.calls if c[0] == "history")
    assert (filters.status, filters.replied, filters.supplier, page, size) == (
        "sent",
        False,
        "Parmalat",
        2,
        25,
    )
    bad = client.get("/api/v1/pqrsf/improvement-opportunities", params={"status": "lost"})
    assert bad.status_code == 422
