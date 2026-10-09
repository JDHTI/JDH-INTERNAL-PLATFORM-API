"""Suggestions and compliments: stored apart from the cases and thanked at once."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.modules.pqrsf.domain.entities import RequestType
from app.modules.sites.domain.entities import Site

# Where a message came from.
ORIGIN_FORM = "public_form"
ORIGIN_EMAIL = "email"

# Delivery of the automatic thank-you, as the manager screens show it.
REPLY_PENDING = "pending"
REPLY_SENT = "sent"
REPLY_FAILED = "failed"
REPLY_SKIPPED = "skipped"

# Why a message got no automatic reply.
SKIP_RECIPIENT_LIMIT = "recipient_limit"
SKIP_SAME_CONVERSATION = "same_conversation"

EXCERPT_LENGTH = 200


@dataclass
class FeedbackFile:
    """A file that came with a suggestion or compliment, stored in the bucket."""

    id: UUID
    feedback_id: UUID
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    created_at: datetime


@dataclass
class Feedback:
    """A suggestion or compliment from the public form or the PQRSF mailbox."""

    id: UUID
    request_type: RequestType
    origin: str
    full_name: str
    email: str
    body: str
    created_at: datetime
    site: Site | None = None
    business_name: str | None = None
    subject: str | None = None
    request_id: UUID | None = None
    email_message_id: UUID | None = None
    reply_notification_id: UUID | None = None
    reply_skipped_reason: str | None = None
    read_at: datetime | None = None
    read_by_user_id: UUID | None = None
    # Filled when the message is read: where the automatic reply stands.
    reply_status: str | None = None
    reply_sent_at: datetime | None = None
    files_count: int = 0
    files: list[FeedbackFile] = field(default_factory=list)

    @property
    def replied(self) -> bool:
        """Whether the thank-you was decided already (queued or deliberately skipped)."""

        return self.reply_notification_id is not None or self.reply_skipped_reason is not None

    @property
    def excerpt(self) -> str:
        """The first words of the message, for lists."""

        text = " ".join(self.body.split())
        return text if len(text) <= EXCERPT_LENGTH else text[: EXCERPT_LENGTH - 1] + "…"


@dataclass(frozen=True)
class FeedbackFilters:
    """What the manager list can be narrowed by."""

    request_type_code: str | None = None
    query: str | None = None
    unread_only: bool = False
    created_from: date | None = None
    created_to: date | None = None


@dataclass(frozen=True)
class FeedbackPage:
    """One page of messages."""

    items: list[Feedback]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class FeedbackKindCount:
    """Messages of one type and how many nobody opened yet."""

    code: str
    name: str
    count: int
    unread: int


@dataclass(frozen=True)
class FeedbackSummary:
    """Totals for the badge and the filter chips."""

    total: int
    unread: int
    by_kind: list[FeedbackKindCount]


def feedback_file_key(feedback_id: UUID, file_id: UUID, extension: str) -> str:
    """Bucket key of a file of a suggestion or compliment."""

    return f"pqrsf/feedback/{feedback_id}/{file_id}.{extension}"
