import base64
import json

import httpx
import pytest

from app.core.errors import DependencyUnavailableError
from app.modules.mail.domain import OutgoingFile
from app.modules.mail.infrastructure.graph import GraphMailClient

MAILBOX = "pqrsf@jdh.com.co"


class Graph:
    """A scripted Graph: records requests and answers from a handler."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens}", "expires_in": 3600}
            )
        self.requests.append(request)
        return self.handler(request)


def client(handler) -> tuple[GraphMailClient, Graph]:
    graph = Graph(handler)
    http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
    return GraphMailClient("tenant", "client", "secret", http=http), graph


@pytest.mark.asyncio
async def test_the_token_is_requested_once_and_reused() -> None:
    api, graph = client(lambda r: httpx.Response(200, json={"value": []}))

    await api.list_attachments(MAILBOX, "g1")
    await api.list_attachments(MAILBOX, "g1")

    assert graph.tokens == 1
    assert graph.requests[0].headers["Authorization"] == "Bearer tok1"


@pytest.mark.asyncio
async def test_a_rejected_token_is_renewed_once() -> None:
    def handler(request):
        if request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"value": []})

    api, graph = client(handler)

    assert await api.list_attachments(MAILBOX, "g1") == []
    assert graph.tokens == 2


@pytest.mark.asyncio
async def test_bad_credentials_surface_as_a_dependency_error() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": "invalid_client"})
        )
    )
    api = GraphMailClient("tenant", "client", "wrong", http=http)

    with pytest.raises(DependencyUnavailableError):
        await api.list_attachments(MAILBOX, "g1")


@pytest.mark.asyncio
async def test_delta_follows_the_pages_and_skips_removed_items() -> None:
    pages = {
        "start": {
            "value": [{"id": "a"}, {"id": "gone", "@removed": {"reason": "deleted"}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
        },
        "next": {"value": [{"id": "b"}], "@odata.deltaLink": "https://graph.microsoft.com/delta2"},
    }

    def handler(request):
        return httpx.Response(
            200, json=pages["next" if request.url.path.endswith("/next") else "start"]
        )

    api, graph = client(handler)

    result = await api.list_new_messages(MAILBOX, None)

    assert result.message_ids == ["a", "b"] and not result.reset
    assert result.delta_link == "https://graph.microsoft.com/delta2"
    assert "mailFolders/inbox/messages/delta" in str(graph.requests[0].url)
    assert "pqrsf%40jdh.com.co" in str(graph.requests[0].url)


@pytest.mark.asyncio
async def test_an_expired_cursor_lists_the_inbox_again() -> None:
    def handler(request):
        if str(request.url) == "https://graph.microsoft.com/old-cursor":
            return httpx.Response(410, json={"error": {"code": "syncStateNotFound"}})
        return httpx.Response(
            200,
            json={"value": [{"id": "a"}], "@odata.deltaLink": "https://graph.microsoft.com/new"},
        )

    api, _ = client(handler)

    result = await api.list_new_messages(MAILBOX, "https://graph.microsoft.com/old-cursor")

    assert result.reset and result.message_ids == ["a"]
    assert result.delta_link == "https://graph.microsoft.com/new"


@pytest.mark.asyncio
async def test_a_message_is_read_with_text_body_and_headers() -> None:
    body = {
        "id": "g1",
        "internetMessageId": "<abc@x>",
        "conversationId": "conv",
        "subject": "Hola",
        "from": {"emailAddress": {"name": "Ana", "address": "Ana@Cliente.com"}},
        "toRecipients": [{"emailAddress": {"address": "PQRSF@jdh.com.co"}}],
        "receivedDateTime": "2026-10-08T16:02:45Z",
        "body": {"contentType": "text", "content": "Tipo: Queja"},
        "hasAttachments": True,
        "internetMessageHeaders": [{"name": "Auto-Submitted", "value": "auto-replied"}],
    }
    api, graph = client(lambda r: httpx.Response(200, json=body))

    message = await api.get_message(MAILBOX, "g1")

    assert graph.requests[0].headers["Prefer"] == 'outlook.body-content-type="text"'
    assert message.sender_address == "ana@cliente.com" and message.sender_name == "Ana"
    assert message.to_addresses == ("pqrsf@jdh.com.co",)
    assert message.received_at.isoformat() == "2026-10-08T16:02:45+00:00"
    assert message.headers == {"auto-submitted": "auto-replied"}
    assert message.body_text == "Tipo: Queja" and message.has_attachments


@pytest.mark.asyncio
async def test_a_missing_message_is_none() -> None:
    api, _ = client(lambda r: httpx.Response(404, json={}))

    assert await api.get_message(MAILBOX, "gone") is None


@pytest.mark.asyncio
async def test_only_file_attachments_are_listed() -> None:
    items = [
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": "1", "name": "a.pdf", "size": 5},
        {"@odata.type": "#microsoft.graph.itemAttachment", "id": "2", "name": "mail", "size": 9},
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "3",
            "name": "logo.png",
            "contentType": "image/png",
            "size": 3,
            "isInline": True,
        },
    ]
    api, _ = client(lambda r: httpx.Response(200, json={"value": items}))

    files = await api.list_attachments(MAILBOX, "g1")

    assert [(f.id, f.is_inline) for f in files] == [("1", False), ("3", True)]
    assert files[0].content_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_reply_and_mark_use_the_message_endpoints() -> None:
    api, graph = client(lambda r: httpx.Response(202))

    await api.reply(MAILBOX, "g1=", "Recibido")
    await api.mark_processed(MAILBOX, "g1=", "P26AC0012")
    await api.download_attachment(MAILBOX, "g1=", "a1")

    reply, mark, download = graph.requests
    assert reply.method == "POST" and str(reply.url).endswith("/messages/g1%3D/reply")
    assert json.loads(reply.content) == {"comment": "Recibido"}
    assert mark.method == "PATCH"
    assert json.loads(mark.content) == {"isRead": True, "categories": ["P26AC0012"]}
    assert str(download.url).endswith("/attachments/a1/$value")


@pytest.mark.asyncio
async def test_server_errors_are_dependency_errors() -> None:
    api, _ = client(lambda r: httpx.Response(503, text="busy"))

    with pytest.raises(DependencyUnavailableError):
        await api.reply(MAILBOX, "g1", "x")


@pytest.mark.asyncio
async def test_send_posts_a_plain_text_message_from_the_mailbox() -> None:
    api, graph = client(lambda r: httpx.Response(202))

    await api.send(MAILBOX, "carla@jdh.com.co", "Nuevo caso asignado", "Hola Carla")

    (request,) = graph.requests
    assert request.method == "POST" and str(request.url).endswith(
        "/users/pqrsf%40jdh.com.co/sendMail"
    )
    assert json.loads(request.content) == {
        "message": {
            "subject": "Nuevo caso asignado",
            "body": {"contentType": "Text", "content": "Hola Carla"},
            "toRecipients": [{"emailAddress": {"address": "carla@jdh.com.co"}}],
        },
        "saveToSentItems": True,
    }


FILE = OutgoingFile("soporte.pdf", "application/pdf", b"%PDF-1.7 hola")


@pytest.mark.asyncio
async def test_a_reply_without_files_uses_the_plain_reply_endpoint() -> None:
    api, graph = client(lambda r: httpx.Response(202))

    assert await api.reply(MAILBOX, "g1", "Hola") is True

    (request,) = graph.requests
    assert str(request.url).endswith("/messages/g1/reply")
    assert json.loads(request.content) == {"comment": "Hola"}


@pytest.mark.asyncio
async def test_reply_text_keeps_its_lines_and_is_escaped_as_html() -> None:
    api, graph = client(lambda r: httpx.Response(201, json={"id": "d1"}))

    await api.reply(MAILBOX, "g1", "Hola\r\n\r\nValor < 5 & más\nPQRSF JDH")
    await api.reply(MAILBOX, "g1", "Con adjunto\nsegunda línea", [FILE])

    assert json.loads(graph.requests[0].content) == {
        "comment": "Hola<br><br>Valor &lt; 5 &amp; más<br>PQRSF JDH"
    }
    drafts = [r for r in graph.requests if str(r.url).endswith("/createReply")]
    assert json.loads(drafts[0].content) == {"comment": "Con adjunto<br>segunda línea"}


@pytest.mark.asyncio
async def test_a_reply_with_files_goes_through_a_draft_in_the_same_conversation() -> None:
    def handler(request):
        if str(request.url).endswith("/createReply"):
            return httpx.Response(201, json={"id": "draft-1"})
        return httpx.Response(201 if request.url.path.endswith("/attachments") else 202)

    api, graph = client(handler)

    assert await api.reply(MAILBOX, "g1", "Con adjunto", [FILE]) is True

    create, attach, send = graph.requests
    assert str(create.url).endswith("/messages/g1/createReply")
    assert json.loads(create.content) == {"comment": "Con adjunto"}
    assert str(attach.url).endswith("/messages/draft-1/attachments")
    sent = json.loads(attach.content)
    assert (
        sent["@odata.type"] == "#microsoft.graph.fileAttachment" and sent["name"] == "soporte.pdf"
    )
    assert base64.b64decode(sent["contentBytes"]) == FILE.content
    assert send.method == "POST" and str(send.url).endswith("/messages/draft-1/send")


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [[], [FILE]])
async def test_a_reply_to_a_message_that_no_longer_exists_says_so(files) -> None:
    api, _ = client(lambda r: httpx.Response(404, json={}))

    assert await api.reply(MAILBOX, "gone", "Hola", files) is False


@pytest.mark.asyncio
async def test_an_html_email_shows_its_logo_inline() -> None:
    api, graph = client(lambda r: httpx.Response(202))
    logo = OutgoingFile("jdh-logo.png", "image/png", b"PNG", "jdh-logo")

    await api.send(
        MAILBOX, "ana@cliente.com", "Radicamos tu queja", "<p>Hola</p>", [logo], html=True
    )

    message = json.loads(graph.requests[0].content)["message"]
    assert message["body"] == {"contentType": "HTML", "content": "<p>Hola</p>"}
    (attachment,) = message["attachments"]
    assert attachment["isInline"] is True and attachment["contentId"] == "jdh-logo"


@pytest.mark.asyncio
async def test_an_html_reply_is_not_escaped_again() -> None:
    api, graph = client(lambda r: httpx.Response(202))

    await api.reply(MAILBOX, "g1", "<table><tr><td>Hola</td></tr></table>", html=True)

    assert json.loads(graph.requests[0].content) == {
        "comment": "<table><tr><td>Hola</td></tr></table>"
    }


@pytest.mark.asyncio
async def test_send_attaches_the_files_inside_the_message() -> None:
    api, graph = client(lambda r: httpx.Response(202))

    await api.send(MAILBOX, "cliente@empresa.com", "Asunto", "Texto", [FILE])

    message = json.loads(graph.requests[0].content)["message"]
    assert [a["name"] for a in message["attachments"]] == ["soporte.pdf"]
    assert base64.b64decode(message["attachments"][0]["contentBytes"]) == FILE.content


def _big(size: int, name: str = "grande.pdf") -> OutgoingFile:
    return OutgoingFile(name, "application/pdf", b"%" * size)


@pytest.mark.asyncio
async def test_a_message_over_the_json_limit_goes_through_a_draft() -> None:
    def handler(request):
        if request.url.path.endswith("/messages") and request.method == "POST":
            return httpx.Response(201, json={"id": "draft-9"})
        return httpx.Response(201 if request.url.path.endswith("/attachments") else 202)

    api, graph = client(handler)
    files = [_big(1_500_000, "a.pdf"), _big(1_500_000, "b.pdf")]

    await api.send(MAILBOX, "cliente@empresa.com", "Documentos", "Texto", files)

    create, first, second, send = graph.requests
    assert str(create.url).endswith("/users/pqrsf%40jdh.com.co/messages")
    assert json.loads(create.content)["toRecipients"][0]["emailAddress"]["address"] == (
        "cliente@empresa.com"
    )
    assert "attachments" not in json.loads(create.content)
    assert [json.loads(r.content)["name"] for r in (first, second)] == ["a.pdf", "b.pdf"]
    assert str(send.url).endswith("/messages/draft-9/send")


@pytest.mark.asyncio
async def test_a_file_of_three_megabytes_or_more_is_uploaded_in_chunks() -> None:
    uploads: list[httpx.Request] = []

    def handler(request):
        if request.url.host == "upload.example":
            uploads.append(request)
            return httpx.Response(200 if len(uploads) < 2 else 201)
        if request.url.path.endswith("/createUploadSession"):
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/session/1"})
        if request.url.path.endswith("/messages") and request.method == "POST":
            return httpx.Response(201, json={"id": "draft-9"})
        return httpx.Response(202)

    api, graph = client(handler)
    size = 4_000_000

    await api.send(MAILBOX, "cliente@empresa.com", "Documentos", "Texto", [_big(size)])

    session = next(r for r in graph.requests if r.url.path.endswith("/createUploadSession"))
    assert json.loads(session.content)["AttachmentItem"] == {
        "attachmentType": "file",
        "name": "grande.pdf",
        "size": size,
        "contentType": "application/pdf",
    }
    first, last = uploads
    assert first.headers["Content-Range"] == "bytes 0-3276799/4000000"
    assert last.headers["Content-Range"] == "bytes 3276800-3999999/4000000"
    assert sum(len(u.content) for u in uploads) == size
    # The upload URL authorizes itself: our bearer token must not travel to it.
    assert all("authorization" not in u.headers for u in uploads)
    assert str(graph.requests[-1].url).endswith("/messages/draft-9/send")


@pytest.mark.asyncio
async def test_a_failed_chunk_is_a_dependency_error() -> None:
    def handler(request):
        if request.url.host == "upload.example":
            return httpx.Response(500)
        if request.url.path.endswith("/createUploadSession"):
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/s"})
        return httpx.Response(201, json={"id": "d"})

    api, _ = client(handler)

    with pytest.raises(DependencyUnavailableError):
        await api.send(MAILBOX, "c@e.com", "Asunto", "Texto", [_big(3_500_000)])
