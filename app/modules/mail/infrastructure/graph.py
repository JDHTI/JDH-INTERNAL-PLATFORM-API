"""Microsoft Graph adapter: app-only token and the mailbox calls PQRSF needs."""

from __future__ import annotations

import base64
import html
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import DependencyUnavailableError
from app.infrastructure.graph_auth import GRAPH_URL, LOGIN_URL, GraphAppClient
from app.modules.mail.domain import DeltaResult, InboundMessage, MailAttachment, OutgoingFile

logger = logging.getLogger(__name__)

__all__ = ["GRAPH_URL", "LOGIN_URL", "GraphMailClient"]

_MESSAGE_FIELDS = (
    "id,internetMessageId,conversationId,subject,from,toRecipients,"
    "receivedDateTime,body,hasAttachments,internetMessageHeaders"
)
_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
_PAGE_LIMIT = 200
# A file attachment sent as JSON must stay well under 3 MB (base64 adds a third and the whole
# request is capped near 4 MB): above this a message goes through a draft, and a single
# file of 3 MB or more through an upload session, sent in chunks that are multiples of 320 KiB.
_INLINE_LIMIT = 3 * 1024 * 1024
_DRAFT_THRESHOLD = 2 * 1024 * 1024
_UPLOAD_CHUNK = 320 * 1024 * 10


def _segment(value: str) -> str:
    return quote(value, safe="")


class GraphMailClient(GraphAppClient):
    """The mailbox calls PQRSF needs, over the shared app-only Graph client."""

    credentials_label = "del correo"

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

    async def reply(
        self,
        mailbox: str,
        graph_id: str,
        text: str,
        files: Sequence[OutgoingFile] = (),
        cc: Sequence[str] = (),
    ) -> bool:
        """Reply in the same conversation; Graph quotes the original and keeps the thread.

        With files the reply goes through a draft (Graph's plain reply takes no
        attachments): create it, attach each file, send it.

        Returns:
            bool: ``False`` when the message to answer no longer exists.
        """

        message = f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(graph_id)}"
        body: dict[str, Any] = {"comment": _html(text)}
        if cc:
            body["message"] = {"ccRecipients": _recipients(cc)}
        if not files:
            response = await self._request("POST", f"{message}/reply", json=body, missing=(404,))
            return response is not None
        draft = await self._request("POST", f"{message}/createReply", json=body, missing=(404,))
        if draft is None:
            return False
        draft_url = f"{GRAPH_URL}/users/{_segment(mailbox)}/messages/{_segment(draft.json()['id'])}"
        for file in files:
            await self._attach(draft_url, file)
        await self._request("POST", f"{draft_url}/send")
        return True

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

    async def send(
        self,
        mailbox: str,
        to: str,
        subject: str,
        text: str,
        files: Sequence[OutgoingFile] = (),
        cc: Sequence[str] = (),
    ) -> None:
        """Send a plain-text message from the mailbox; it is kept in its Sent Items."""

        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": _recipients([to]),
        }
        if cc:
            message["ccRecipients"] = _recipients(cc)
        user = f"{GRAPH_URL}/users/{_segment(mailbox)}"
        if sum(len(file.content) for file in files) >= _DRAFT_THRESHOLD:
            # Too big for one JSON request: create a draft, attach each file, send it.
            draft = await self._request("POST", f"{user}/messages", json=message)
            assert draft is not None
            draft_url = f"{user}/messages/{_segment(draft.json()['id'])}"
            for file in files:
                await self._attach(draft_url, file)
            await self._request("POST", f"{draft_url}/send")
            return
        if files:
            message["attachments"] = [_file_attachment(file) for file in files]
        await self._request(
            "POST", f"{user}/sendMail", json={"message": message, "saveToSentItems": True}
        )

    async def _attach(self, draft_url: str, file: OutgoingFile) -> None:
        """Add a file to a draft: as JSON when small, through an upload session otherwise."""

        if len(file.content) < _INLINE_LIMIT:
            await self._request("POST", f"{draft_url}/attachments", json=_file_attachment(file))
            return
        session = await self._request(
            "POST",
            f"{draft_url}/attachments/createUploadSession",
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": file.name,
                    "size": len(file.content),
                    "contentType": file.content_type,
                }
            },
        )
        assert session is not None
        upload_url = session.json()["uploadUrl"]
        total = len(file.content)
        for start in range(0, total, _UPLOAD_CHUNK):
            chunk = file.content[start : start + _UPLOAD_CHUNK]
            try:
                # The upload URL carries its own authorization: no bearer token goes with it.
                response = await self._http.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{total}",
                    },
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError(
                    "No se pudo subir el adjunto a Microsoft Graph"
                ) from exc
            if response.status_code >= 400:
                logger.error("Graph upload session failed: %s", response.status_code)
                raise DependencyUnavailableError(
                    f"Microsoft Graph respondió {response.status_code} al subir el adjunto"
                )


def _recipients(addresses: Sequence[str]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": address}} for address in addresses]


def _html(text: str) -> str:
    """Plain text as the HTML Graph puts a reply comment into: lines stay lines."""

    return html.escape(text.replace("\r\n", "\n"), quote=False).replace("\n", "<br>")


def _file_attachment(file: OutgoingFile) -> dict[str, Any]:
    return {
        "@odata.type": _FILE_ATTACHMENT,
        "name": file.name,
        "contentType": file.content_type,
        "contentBytes": base64.b64encode(file.content).decode("ascii"),
    }
