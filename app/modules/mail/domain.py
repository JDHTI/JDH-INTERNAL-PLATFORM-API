"""Mail contracts: what a mailbox gateway returns and what it must offer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class MailAttachment:
    """Metadata of a file attached to a message (the bytes are fetched on demand)."""

    id: str
    name: str
    content_type: str
    size: int
    is_inline: bool = False


@dataclass(frozen=True)
class InboundMessage:
    """A received message with its body as plain text."""

    graph_id: str
    internet_message_id: str
    conversation_id: str | None
    subject: str
    sender_address: str
    sender_name: str | None
    to_addresses: tuple[str, ...]
    received_at: datetime
    body_text: str
    has_attachments: bool
    # Lower-cased header names, used to recognize automatic replies.
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeltaResult:
    """Message identifiers changed since the last sync and the cursor for the next one.

    ``reset`` is true when the previous cursor had expired and the whole folder was
    listed again; the caller must tolerate seeing messages it already handled.
    """

    message_ids: list[str]
    delta_link: str
    reset: bool = False


class MailGateway(Protocol):
    """Read, answer and mark the messages of one mailbox."""

    async def list_new_messages(self, mailbox: str, delta_link: str | None) -> DeltaResult:
        """Return the inbox messages added since ``delta_link`` (all when ``None``)."""
        ...

    async def get_message(self, mailbox: str, graph_id: str) -> InboundMessage | None:
        """Load one message, or ``None`` when it no longer exists."""
        ...

    async def list_attachments(self, mailbox: str, graph_id: str) -> list[MailAttachment]:
        """List the file attachments of a message."""
        ...

    async def download_attachment(self, mailbox: str, graph_id: str, attachment_id: str) -> bytes:
        """Read the bytes of one attachment."""
        ...

    async def reply(self, mailbox: str, graph_id: str, text: str) -> None:
        """Answer inside the same conversation, from the mailbox."""
        ...

    async def mark_processed(self, mailbox: str, graph_id: str, category: str | None) -> None:
        """Mark the message as read and tag it, so people see it was handled."""
        ...
