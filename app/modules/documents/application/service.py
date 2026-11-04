"""Document management use cases, segmented by area.

Rules:
- Every operation first asks the areas module for the ``documentos`` module of
  the area: ``read`` to list, search and download; ``manage`` to create, edit,
  delete and color tags.
- PostgreSQL is the index (names, tags, search); object storage keeps the
  bytes under ``areas/{area_id}/{folder_id}/{document_id}.{ext}``.
- Upload validates extension and size, stores the object, then indexes it; if
  indexing fails the object is removed so no orphan is left behind.
- Delete removes the object first (idempotent) and then the index entry, so a
  storage failure leaves the entry in place and the delete can be retried.
- A folder that still holds documents cannot be deleted.
- Tag colors belong to the area, not to a document: coloring "contratos" once
  colors it on every document of that area. Anyone who can use the area may
  set them.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaError,
)
from app.core.logging import logger
from app.modules.documents.application.dto import (
    DocumentPage,
    DownloadLink,
    FolderView,
    SearchQuery,
    UpdateDocumentCommand,
    UploadCommand,
)
from app.modules.documents.domain.entities import (
    Document,
    DocumentFolder,
    TagColor,
    clean_text,
    content_type_for,
    download_filename,
    normalize_color,
    normalize_tag,
    normalize_tags,
    split_filename,
    storage_key_for,
)
from app.modules.documents.domain.ports import (
    AreaAccess,
    DocumentRepository,
    ObjectStorage,
    SearchFilters,
)
from app.modules.identity.domain.principal import Principal

# Module key of the document manager in the permission catalog, and its two actions.
MODULE = "documentos"
READ = "read"
MANAGE = "manage"

_DUPLICATE_DOCUMENT = "Ya existe un documento con ese nombre en la carpeta"
_DUPLICATE_FOLDER = "Ya existe una carpeta con ese nombre en el área"


def _optional(value: str | None, field: str, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    return clean_text(value, field, max_length)


class DocumentService:
    """Coordinate folders and documents inside the areas a caller can access."""

    def __init__(
        self,
        repository: DocumentRepository,
        storage: ObjectStorage,
        areas: AreaAccess,
        max_upload_bytes: int,
        allowed_extensions: Iterable[str],
        download_url_ttl: int,
    ) -> None:
        """Create the service with its ports and upload policy.

        Args:
            repository (DocumentRepository): Port that stores folders and the
                document index.
            storage (ObjectStorage): Port that stores document bytes.
            areas (AreaAccess): Port that authorizes access to an area.
            max_upload_bytes (int): Largest accepted file.
            allowed_extensions (Iterable[str]): Accepted file extensions.
            download_url_ttl (int): Validity of download links, in seconds.
        """

        self.repository = repository
        self.storage = storage
        self.areas = areas
        self.max_upload_bytes = max_upload_bytes
        self.allowed_extensions = frozenset(e.lower().lstrip(".") for e in allowed_extensions)
        self.download_url_ttl = download_url_ttl

    # --- Helpers ---

    async def _folder(self, area_id: UUID, folder_id: UUID) -> DocumentFolder:
        folder = await self.repository.get_folder(area_id, folder_id)
        if folder is None:
            raise NotFoundError("Carpeta no encontrada en esta área")
        return folder

    async def _document(
        self, document_id: UUID, principal: Principal, action: str = READ
    ) -> Document:
        document = await self.repository.get_document(document_id)
        if document is None:
            raise NotFoundError("Documento no encontrado")
        await self.areas.authorize_by_id(document.area_id, principal, MODULE, action)
        return document

    async def _area(self, area_slug: str, principal: Principal, action: str = READ):
        return await self.areas.authorize(area_slug, principal, MODULE, action)

    async def _discard_object(self, key: str) -> None:
        try:
            await self.storage.delete_object(key)
        except Exception as exc:  # noqa: BLE001 - the original failure is the one to report
            logger.error("Could not remove orphan object %s: %s", key, exc)

    # --- Folders ---

    async def list_folders(self, area_slug: str, principal: Principal) -> list[FolderView]:
        """List the folders of an area with their document counts.

        Args:
            area_slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            list[FolderView]: Folders ordered by name.
        """

        area = await self._area(area_slug, principal)
        rows = await self.repository.list_folders(area.id)
        return [FolderView(folder=f, document_count=n) for f, n in rows]

    async def create_folder(
        self, area_slug: str, name: str, description: str | None, principal: Principal
    ) -> FolderView:
        """Create a folder in an area.

        Args:
            area_slug (str): Area slug.
            name (str): Folder name, unique inside the area.
            description (str | None): Optional description.
            principal (Principal): The caller.

        Returns:
            FolderView: The created, empty folder.
        """

        area = await self._area(area_slug, principal, MANAGE)
        name = clean_text(name, "nombre", 150)
        if await self.repository.folder_name_taken(area.id, name):
            raise ConflictError(_DUPLICATE_FOLDER)
        folder = await self.repository.create_folder(
            area.id, name, _optional(description, "descripción", 1000), principal.email
        )
        return FolderView(folder=folder, document_count=0)

    async def update_folder(
        self,
        area_slug: str,
        folder_id: UUID,
        fields: frozenset[str],
        name: str | None,
        description: str | None,
        principal: Principal,
    ) -> FolderView:
        """Rename a folder or change its description.

        Args:
            area_slug (str): Area slug.
            folder_id (UUID): The folder.
            fields (frozenset[str]): Which of ``name``/``description`` to apply.
            name (str | None): New name.
            description (str | None): New description; ``None`` clears it.
            principal (Principal): The caller.

        Returns:
            FolderView: The updated folder.
        """

        area = await self._area(area_slug, principal, MANAGE)
        folder = await self._folder(area.id, folder_id)
        if "name" in fields and name is not None:
            folder.name = clean_text(name, "nombre", 150)
            if await self.repository.folder_name_taken(area.id, folder.name, folder.id):
                raise ConflictError(_DUPLICATE_FOLDER)
        if "description" in fields:
            folder.description = _optional(description, "descripción", 1000)
        folder = await self.repository.save_folder(folder)
        count = await self.repository.count_folder_documents(folder.id)
        return FolderView(folder=folder, document_count=count)

    async def delete_folder(self, area_slug: str, folder_id: UUID, principal: Principal) -> None:
        """Delete an empty folder.

        Args:
            area_slug (str): Area slug.
            folder_id (UUID): The folder.
            principal (Principal): The caller.

        Returns:
            None
        """

        area = await self._area(area_slug, principal, MANAGE)
        folder = await self._folder(area.id, folder_id)
        if await self.repository.count_folder_documents(folder.id):
            raise ConflictError("La carpeta tiene documentos: elimínalos antes de borrarla")
        await self.repository.delete_folder(folder.id)

    # --- Documents ---

    async def search(self, query: SearchQuery, principal: Principal) -> DocumentPage:
        """Search documents across the areas the caller can access.

        Args:
            query (SearchQuery): Criteria; without ``area_slug`` it covers every
                area the caller can access. Comma-separated ``tags`` must all match.
            principal (Principal): The caller.

        Returns:
            DocumentPage: One page of documents, newest first.
        """

        if query.area_slug:
            area_ids: list[UUID] | None = [(await self._area(query.area_slug, principal)).id]
        else:
            area_ids = await self.areas.accessible_area_ids(principal, MODULE)
        page = max(query.page, 1)
        size = min(max(query.size, 1), 100)
        filters = SearchFilters(
            area_ids=area_ids,
            folder_id=query.folder_id,
            text=query.text.strip() if query.text and query.text.strip() else None,
            tags=normalize_tags(query.tags),
            extension=query.extension.lower().lstrip(".") if query.extension else None,
        )
        items, total = await self.repository.search(filters, page, size)
        return DocumentPage(items=items, total=total, page=page, size=size)

    async def get_document(self, document_id: UUID, principal: Principal) -> Document:
        """Return one document of an area the caller can access.

        Args:
            document_id (UUID): The document.
            principal (Principal): The caller.

        Returns:
            Document: The document index entry.
        """

        return await self._document(document_id, principal)

    async def upload(
        self, area_slug: str, folder_id: UUID, command: UploadCommand, principal: Principal
    ) -> Document:
        """Store a file in a folder and index it.

        Args:
            area_slug (str): Area slug.
            folder_id (UUID): Destination folder of that area.
            command (UploadCommand): File name, bytes and optional metadata;
                ``name`` defaults to the file name.
            principal (Principal): The caller.

        Returns:
            Document: The indexed document.
        """

        area = await self._area(area_slug, principal, MANAGE)
        folder = await self._folder(area.id, folder_id)

        original_filename, extension = split_filename(command.filename)
        if extension not in self.allowed_extensions:
            raise UnsupportedMediaError(f"Tipo de archivo no permitido: .{extension}")
        if len(command.content) > self.max_upload_bytes:
            limit_mb = self.max_upload_bytes // (1024 * 1024)
            raise PayloadTooLargeError(f"El archivo supera el máximo de {limit_mb} MB")
        if not command.content:
            raise InvalidInputError("El archivo está vacío")
        name = clean_text(command.name, "nombre", 255) if command.name else original_filename
        tags = normalize_tags(command.tags)
        if await self.repository.document_name_taken(folder.id, name):
            raise ConflictError(_DUPLICATE_DOCUMENT)

        document_id = uuid.uuid4()
        key = storage_key_for(area.id, folder.id, document_id, extension)
        content_type = content_type_for(extension)
        try:
            await self.storage.put_object(key, command.content, content_type)
        except Exception as exc:
            logger.error("Could not store object %s: %s", key, exc)
            raise DependencyUnavailableError("El almacenamiento de documentos no responde") from exc

        now = datetime.now(UTC)
        document = Document(
            id=document_id,
            area_id=area.id,
            folder_id=folder.id,
            name=name,
            original_filename=original_filename,
            extension=extension,
            content_type=content_type,
            size_bytes=len(command.content),
            checksum_sha256=hashlib.sha256(command.content).hexdigest(),
            storage_key=key,
            description=_optional(command.description, "descripción", 2000),
            tags=tags,
            uploaded_by=principal.email,
            created_at=now,
            updated_at=now,
        )
        try:
            return await self.repository.add_document(document)
        except Exception:
            await self._discard_object(key)
            raise

    async def update_document(
        self, document_id: UUID, command: UpdateDocumentCommand, principal: Principal
    ) -> Document:
        """Change a document's name, description or tags; the bytes stay put.

        Args:
            document_id (UUID): The document.
            command (UpdateDocumentCommand): Fields to change.
            principal (Principal): The caller.

        Returns:
            Document: The updated document.
        """

        document = await self._document(document_id, principal, MANAGE)
        if "name" in command.fields and command.name is not None:
            document.name = clean_text(command.name, "nombre", 255)
            if await self.repository.document_name_taken(
                document.folder_id, document.name, document.id
            ):
                raise ConflictError(_DUPLICATE_DOCUMENT)
        if "description" in command.fields:
            document.description = _optional(command.description, "descripción", 2000)
        if "tags" in command.fields and command.tags is not None:
            document.tags = normalize_tags(command.tags)
        return await self.repository.save_document(document)

    async def delete_document(self, document_id: UUID, principal: Principal) -> None:
        """Delete a document's bytes and its index entry.

        Args:
            document_id (UUID): The document.
            principal (Principal): The caller.

        Returns:
            None
        """

        document = await self._document(document_id, principal, MANAGE)
        try:
            await self.storage.delete_object(document.storage_key)
        except Exception as exc:
            logger.error("Could not delete object %s: %s", document.storage_key, exc)
            raise DependencyUnavailableError("El almacenamiento de documentos no responde") from exc
        await self.repository.delete_document(document.id)

    async def download_link(
        self, document_id: UUID, principal: Principal, inline: bool = False
    ) -> DownloadLink:
        """Sign a temporary link that downloads the document.

        Args:
            document_id (UUID): The document.
            principal (Principal): The caller.
            inline (bool): Link that displays the file in the browser instead of saving it.

        Returns:
            DownloadLink: Signed URL, the file name it saves as and its validity.
        """

        document = await self._document(document_id, principal)
        filename = download_filename(document)
        url = self.storage.presigned_download_url(
            document.storage_key, filename, self.download_url_ttl, inline=inline
        )
        return DownloadLink(url=url, filename=filename, expires_in=self.download_url_ttl)

    # --- Tag colors ---

    async def list_tag_colors(self, area_slug: str, principal: Principal) -> list[TagColor]:
        """List the tag colors of an area.

        Args:
            area_slug (str): Area slug.
            principal (Principal): The caller.

        Returns:
            list[TagColor]: Colors ordered by tag; tags without one are absent.
        """

        area = await self._area(area_slug, principal)
        return await self.repository.list_tag_colors(area.id)

    async def set_tag_color(
        self, area_slug: str, tag: str, color: str, principal: Principal
    ) -> TagColor:
        """Assign a color to a tag of an area, replacing the previous one.

        Args:
            area_slug (str): Area slug.
            tag (str): Tag, normalized like the ones on documents.
            color (str): ``#rrggbb``, case-insensitive.
            principal (Principal): The caller.

        Returns:
            TagColor: The stored color.
        """

        area = await self._area(area_slug, principal, MANAGE)
        return await self.repository.save_tag_color(
            area.id, normalize_tag(tag), normalize_color(color), principal.email
        )

    async def clear_tag_color(self, area_slug: str, tag: str, principal: Principal) -> None:
        """Return a tag to the default look.

        Args:
            area_slug (str): Area slug.
            tag (str): Tag, normalized like the ones on documents.
            principal (Principal): The caller.

        Returns:
            None
        """

        area = await self._area(area_slug, principal, MANAGE)
        await self.repository.delete_tag_color(area.id, normalize_tag(tag))
