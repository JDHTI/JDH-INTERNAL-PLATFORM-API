"""Contracts required by the document use cases."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.documents.domain.entities import Document, DocumentFolder, TagColor
from app.modules.identity.domain.principal import Principal


@dataclass(frozen=True)
class SearchFilters:
    """Document search criteria. ``area_ids=None`` means every area."""

    area_ids: Sequence[UUID] | None = None
    folder_id: UUID | None = None
    text: str | None = None
    tags: Sequence[str] = ()
    extension: str | None = None


class AreaRef(Protocol):
    id: UUID
    slug: str


class AreaAccess(Protocol):
    """Answer area access questions without reading the areas module's tables."""

    async def authorize(self, slug: str, principal: Principal) -> AreaRef:
        """Return the area when the principal may use its space.

        Args:
            slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            AreaRef: The authorized area.
        """
        ...

    async def authorize_by_id(self, area_id: UUID, principal: Principal) -> AreaRef:
        """Return the area by identifier when the principal may use its space.

        Args:
            area_id (UUID): Area identifier.
            principal (Principal): The caller.

        Returns:
            AreaRef: The authorized area.
        """
        ...

    async def accessible_area_ids(self, principal: Principal) -> list[UUID] | None:
        """List the areas whose space the principal may use.

        Args:
            principal (Principal): The caller.

        Returns:
            list[UUID] | None: Area identifiers, or ``None`` meaning every area.
        """
        ...


class ObjectStorage(Protocol):
    """Binary storage for document contents."""

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        """Store an object.

        Args:
            key (str): Object key.
            body (bytes): Contents.
            content_type (str | None): MIME type served on download.

        Returns:
            None
        """
        ...

    async def delete_object(self, key: str) -> None:
        """Delete an object; succeeds when it no longer exists.

        Args:
            key (str): Object key.

        Returns:
            None
        """
        ...

    def presigned_download_url(self, key: str, filename: str, expires_in: int) -> str:
        """Sign a temporary download URL.

        Args:
            key (str): Object key.
            filename (str): Name the browser saves the file as.
            expires_in (int): Validity in seconds.

        Returns:
            str: The signed URL.
        """
        ...


class DocumentRepository(Protocol):
    """Define persistence operations for folders and document index entries."""

    async def list_folders(self, area_id: UUID) -> list[tuple[DocumentFolder, int]]:
        """List the folders of an area with their document count.

        Args:
            area_id (UUID): The area.

        Returns:
            list[tuple[DocumentFolder, int]]: Folders ordered by name, each with
                how many documents it holds.
        """
        ...

    async def get_folder(self, area_id: UUID, folder_id: UUID) -> DocumentFolder | None:
        """Find a folder inside a given area.

        Args:
            area_id (UUID): Area that must own the folder.
            folder_id (UUID): The folder.

        Returns:
            DocumentFolder | None: The folder when it belongs to the area.
        """
        ...

    async def folder_name_taken(
        self, area_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the area already has a folder with this name.

        Args:
            area_id (UUID): The area.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Folder to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """
        ...

    async def create_folder(
        self, area_id: UUID, name: str, description: str | None, created_by: str
    ) -> DocumentFolder:
        """Persist a folder.

        Args:
            area_id (UUID): Owning area.
            name (str): Folder name, unique inside the area.
            description (str | None): Optional description.
            created_by (str): Email of the creator.

        Returns:
            DocumentFolder: The stored folder.
        """
        ...

    async def save_folder(self, folder: DocumentFolder) -> DocumentFolder:
        """Persist changes to a folder.

        Args:
            folder (DocumentFolder): Folder with updated fields.

        Returns:
            DocumentFolder: The stored folder.
        """
        ...

    async def delete_folder(self, folder_id: UUID) -> None:
        """Delete a folder.

        Args:
            folder_id (UUID): Folder to delete; fails with a conflict while it
                still holds documents.

        Returns:
            None
        """
        ...

    async def count_folder_documents(self, folder_id: UUID) -> int:
        """Count the documents inside a folder.

        Args:
            folder_id (UUID): The folder.

        Returns:
            int: Number of documents.
        """
        ...

    async def get_document(self, document_id: UUID) -> Document | None:
        """Find a document by identifier.

        Args:
            document_id (UUID): The document.

        Returns:
            Document | None: The document when it exists.
        """
        ...

    async def document_name_taken(
        self, folder_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the folder already has a document with this name.

        Args:
            folder_id (UUID): The folder.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Document to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """
        ...

    async def add_document(self, document: Document) -> Document:
        """Persist a new document index entry.

        Args:
            document (Document): Entry with its identifier already assigned
                (the storage key is derived from it).

        Returns:
            Document: The stored entry.
        """
        ...

    async def save_document(self, document: Document) -> Document:
        """Persist metadata changes of a document.

        Args:
            document (Document): Entry with updated name, description or tags.

        Returns:
            Document: The stored entry.
        """
        ...

    async def delete_document(self, document_id: UUID) -> None:
        """Delete a document index entry.

        Args:
            document_id (UUID): The document.

        Returns:
            None
        """
        ...

    async def search(
        self, filters: SearchFilters, page: int, size: int
    ) -> tuple[list[Document], int]:
        """Search documents, newest first.

        Args:
            filters (SearchFilters): Criteria; tags must all match.
            page (int): 1-based page number.
            size (int): Page size.

        Returns:
            tuple[list[Document], int]: The page of documents and the total count.
        """
        ...

    async def list_tag_colors(self, area_id: UUID) -> list[TagColor]:
        """List the tag colors of an area.

        Args:
            area_id (UUID): The area.

        Returns:
            list[TagColor]: Colors ordered by tag.
        """
        ...

    async def save_tag_color(
        self, area_id: UUID, tag: str, color: str, updated_by: str
    ) -> TagColor:
        """Create or replace the color of a tag.

        Args:
            area_id (UUID): The area.
            tag (str): Normalized tag.
            color (str): Normalized ``#rrggbb`` color.
            updated_by (str): Email of whoever set it.

        Returns:
            TagColor: The stored color.
        """
        ...

    async def delete_tag_color(self, area_id: UUID, tag: str) -> None:
        """Remove the color of a tag; succeeds when it had none.

        Args:
            area_id (UUID): The area.
            tag (str): Normalized tag.

        Returns:
            None
        """
        ...
