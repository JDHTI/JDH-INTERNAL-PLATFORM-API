"""Commands and results of the document use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.modules.documents.domain.entities import Document, DocumentFolder


@dataclass(frozen=True)
class FolderView:
    folder: DocumentFolder
    document_count: int


@dataclass(frozen=True)
class DocumentPage:
    items: list[Document]
    total: int
    page: int
    size: int

    @property
    def pages(self) -> int:
        return (self.total + self.size - 1) // self.size if self.total else 0


@dataclass(frozen=True)
class DownloadLink:
    url: str
    filename: str
    expires_in: int


@dataclass(frozen=True)
class SearchQuery:
    area_slug: str | None = None
    folder_id: UUID | None = None
    text: str | None = None
    tags: str | None = None
    extension: str | None = None
    page: int = 1
    size: int = 20


@dataclass(frozen=True)
class UploadCommand:
    filename: str | None
    content: bytes
    name: str | None = None
    description: str | None = None
    tags: str | None = None


@dataclass(frozen=True)
class UpdateDocumentCommand:
    """Partial update: only the names listed in ``fields`` are applied."""

    fields: frozenset[str] = field(default_factory=frozenset)
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
