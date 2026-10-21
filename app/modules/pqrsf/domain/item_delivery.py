"""The answer to a requested document of a case: a file, sent to the client by email."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

# Where the file of an answer comes from.
SOURCE_VAULT = "vault"
SOURCE_UPLOAD = "upload"

# Where the email of an answer stands. ``not_sent`` is an answer registered without emailing.
EMAIL_NOT_SENT = "not_sent"


@dataclass
class ItemDelivery:
    """One answered document of a case."""

    id: UUID
    case_id: UUID
    item_id: UUID
    source: str
    filename: str
    created_by: UUID
    created_at: datetime
    document_id: UUID | None = None
    attachment_id: UUID | None = None
    notification_id: UUID | None = None
    # Derived from the email when the answer is read.
    email_status: str = EMAIL_NOT_SENT
    email_sent_at: datetime | None = None
    email_error: str | None = None
    # Where the linked file is kept in the manager («Calidad / Carpeta / Documento»).
    document_path: str | None = None
