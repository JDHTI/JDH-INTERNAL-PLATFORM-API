import json

import httpx
import pytest

from app.core.errors import DependencyUnavailableError
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
