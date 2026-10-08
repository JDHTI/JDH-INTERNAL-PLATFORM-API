"""Microsoft Graph adapter: app-only token and the mailbox calls PQRSF needs."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import DependencyUnavailableError
from app.modules.mail.domain import DeltaResult, InboundMessage, MailAttachment

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"
_MESSAGE_FIELDS = (
    "id,internetMessageId,conversationId,subject,from,toRecipients,"
    "receivedDateTime,body,hasAttachments,internetMessageHeaders"
)
_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
_PAGE_LIMIT = 200


def _segment(value: str) -> str:
    return quote(value, safe="")


class GraphMailClient:
    """Talk to Graph with client credentials; one instance is shared and reuses its token."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> None:
        """Create the client.

        Args:
            tenant_id (str): Directory that owns the app registration.
            client_id (str): Application (client) identifier.
            client_secret (str): Secret value (never its identifier).
            http (httpx.AsyncClient | None): Injected transport for tests.
            timeout (float): Seconds allowed per request.
        """

        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._token_value: str | None = None
        self._token_expires = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Release the HTTP connections."""

        await self._http.aclose()

    # --- Token ---

    async def _token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token_value and time.monotonic() < self._token_expires:
                return self._token_value
            try:
                response = await self._http.post(
                    f"{LOGIN_URL}/{self._tenant_id}/oauth2/v2.0/token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": "https://graph.microsoft.com/.default",
                        "grant_type": "client_credentials",
                    },
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError("No se pudo contactar a Microsoft Entra") from exc
            if response.status_code != 200:
                logger.error("Graph token request failed: %s", response.status_code)
                raise DependencyUnavailableError(
                    "Microsoft Entra rechazó las credenciales del correo"
                )
            payload = response.json()
            self._token_value = payload["access_token"]
            # Renew a minute early so a request never carries a token about to expire.
            self._token_expires = time.monotonic() + int(payload.get("expires_in", 3600)) - 60
            return self._token_value

    async def _request(
        self,
        method: str,
        url: str,
        *,
        missing: tuple[int, ...] = (),
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Call Graph, renewing the token once on 401.

        Returns ``None`` when the status is one of ``missing`` (a 404, an expired cursor).
        """

        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", **(headers or {})},
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError("No se pudo contactar a Microsoft Graph") from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in missing:
                return None
            if response.status_code >= 400:
                logger.error(
                    "Graph %s %s failed: %s %s",
                    method,
                    url.split("?")[0],
                    response.status_code,
                    response.text[:300],
                )
                raise DependencyUnavailableError(
                    f"Microsoft Graph respondió {response.status_code}"
                )
            return response
        raise DependencyUnavailableError("Microsoft Graph rechazó el token")  # pragma: no cover

    # --- MailGateway ---

    async def list_new_messages(self, mailbox: str, delta_link: str | None) -> DeltaResult:
        """Follow the delta pages until Graph hands back the next cursor."""

        start = (
            f"{GRAPH_URL}/users/{_segment(mailbox)}/mailFolders/inbox/messages/delta"
            "?$select=id,receivedDateTime"
        )
        url = delta_link or start
        reset = False
        ids: list[str] = []
        for _ in range(_PAGE_LIMIT):
            # 410 means the cursor expired: list the folder again from scratch, once.
            response = await self._request(
                "GET", url, missing=(410,), headers={"Prefer": "odata.maxpagesize=50"}
            )
            if response is None:
                if reset or url == start:
                    raise DependencyUnavailableError("Microsoft Graph rechazó la sincronización")
                logger.warning("The delta cursor of %s expired; listing the inbox again", mailbox)
                url, ids, reset = start, [], True
                continue
            body = response.json()
            ids.extend(item["id"] for item in body.get("value", []) if "@removed" not in item)
            if "@odata.nextLink" in body:
                url = body["@odata.nextLink"]
                continue
            return DeltaResult(ids, body["@odata.deltaLink"], reset=reset)
        raise DependencyUnavailableError(
            "La bandeja tiene demasiadas páginas para una sincronización"
        )

    async def get_message(self, mailbox: str, graph_id: str) -> InboundMessage | None:
        """Load a message with a text body and its headers."""

        response = await self._request(
            "GET",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}"
            f"?$select={_MESSAGE_FIELDS}",
            missing=(404,),
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        if response is None:
            return None
        data = response.json()
        sender = (data.get("from") or {}).get("emailAddress") or {}
        return InboundMessage(
            graph_id=data["id"],
            internet_message_id=data.get("internetMessageId") or data["id"],
            conversation_id=data.get("conversationId"),
            subject=data.get("subject") or "",
            sender_address=(sender.get("address") or "").lower(),
            sender_name=sender.get("name"),
            to_addresses=tuple(
                (item.get("emailAddress") or {}).get("address", "").lower()
                for item in data.get("toRecipients", [])
            ),
            received_at=datetime.fromisoformat(data["receivedDateTime"].replace("Z", "+00:00")),
            body_text=(data.get("body") or {}).get("content") or "",
            has_attachments=bool(data.get("hasAttachments")),
            headers={
                item["name"].lower(): item.get("value", "")
                for item in data.get("internetMessageHeaders") or []
            },
        )

    async def list_attachments(self, mailbox: str, graph_id: str) -> list[MailAttachment]:
        """List file attachments; item and reference attachments are not downloadable."""

        response = await self._request(
            "GET",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}/attachments"
            "?$select=id,name,contentType,size,isInline",
        )
        assert response is not None
        return [
            MailAttachment(
                id=item["id"],
                name=item.get("name") or "adjunto",
                content_type=item.get("contentType") or "application/octet-stream",
                size=int(item.get("size") or 0),
                is_inline=bool(item.get("isInline")),
            )
            for item in response.json().get("value", [])
            if item.get("@odata.type") == _FILE_ATTACHMENT
        ]

    async def download_attachment(self, mailbox: str, graph_id: str, attachment_id: str) -> bytes:
        """Read the raw bytes of an attachment."""

        response = await self._request(
            "GET",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}"
            f"/attachments/{_segment(attachment_id)}/$value",
        )
        assert response is not None
        return response.content

    async def reply(self, mailbox: str, graph_id: str, text: str) -> None:
        """Reply in the same conversation; Graph quotes the original and keeps the thread."""

        await self._request(
            "POST",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}/reply",
            json={"comment": text},
        )

    async def mark_processed(self, mailbox: str, graph_id: str, category: str | None) -> None:
        """Mark as read and tag the message with the case code."""

        payload: dict[str, Any] = {"isRead": True}
        if category:
            payload["categories"] = [category]
        await self._request(
            "PATCH",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}",
            json=payload,
        )

    async def send(self, mailbox: str, to: str, subject: str, text: str) -> None:
        """Send a plain-text message from the mailbox; it is kept in its Sent Items."""

        await self._request(
            "POST",
            f"{GRAPH_URL}/users/{_segment(mailbox)}/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": text},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": True,
            },
        )
