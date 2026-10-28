"""Supporting documents of supports: what a document is and what a selection resolves to."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class StoredDocument:
    """A document the document manager holds in the supports folder."""

    id: UUID
    name: str


@dataclass(frozen=True)
class Attachment:
    """A document backing a support, with how many supports it backs in all."""

    document_id: UUID
    name: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    uploaded_by: str
    uploaded_at: datetime
    linked_by: str
    linked_at: datetime
    support_count: int


@dataclass(frozen=True)
class ViewLink:
    """A temporary signed link to open (or save) a support file."""

    url: str
    filename: str
    expires_in: int


@dataclass(frozen=True)
class FileUpload:
    """A file received to be stored as support."""

    filename: str | None
    content: bytes


@dataclass(frozen=True)
class SelectionPreview:
    """The supports a selection resolves to, before anything is uploaded."""

    total: int
    capped: bool
    limit: int
    supports: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class AttachResult:
    """What an upload or link did."""

    documents: list[UUID]
    supports: int
    links_created: int
