"""Keeps the supporting files of supports in the document manager.

The files live in one folder of the supports master's own area (BI; ``soporte_apoyos`` by
default), created the first time something is uploaded, so they are classified and searchable in
that area's document manager like any other document, for whoever belongs to it. The
document service this store receives does not check area membership: the supports managers
file the proofs, and whoever reads a support opens its proofs, without being members of BI.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import ConflictError, InvalidInputError
from app.modules.documents.application.dto import UploadCommand
from app.modules.documents.application.service import DocumentService
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.attachments import FileUpload, StoredDocument, ViewLink


class DocumentManagerSupportStore:
    """Support files in a folder of the document manager."""

    def __init__(
        self,
        documents: DocumentService,
        area_slug: str | None,
        folder_name: str = "soporte_apoyos",
    ) -> None:
        """Create the store.

        Args:
            documents (DocumentService): The document manager.
            area_slug (str | None): Area that owns the folder (the supports managers' area).
            folder_name (str): Name of the folder; created when missing.
        """

        self.documents = documents
        self.area_slug = (area_slug or "").strip() or None
        self.folder_name = folder_name

    def _area(self) -> str:
        if self.area_slug is None:
            raise InvalidInputError(
                "No hay un área configurada para guardar los soportes "
                "(SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG)"
            )
        return self.area_slug

    async def _folder_id(self, principal: Principal) -> UUID:
        area = self._area()
        for view in await self.documents.list_folders(area, principal):
            if view.folder.name.lower() == self.folder_name.lower():
                return view.folder.id
        try:
            created = await self.documents.create_folder(
                area, self.folder_name, "Soportes de los apoyos de proveedores", principal
            )
        except ConflictError:  # someone created it between the listing and now
            for view in await self.documents.list_folders(area, principal):
                if view.folder.name.lower() == self.folder_name.lower():
                    return view.folder.id
            raise
        return created.folder.id

    async def store(
        self,
        principal: Principal,
        upload: FileUpload,
        *,
        name: str | None,
        description: str | None,
        tags: Sequence[str],
    ) -> StoredDocument:
        """Upload a file to the supports folder.

        Without an explicit ``name`` the document is called after the file plus the time, and a
        clash (every pasted screenshot is called ``image.png``) is solved with a short suffix.
        With one, a clash is a conflict.
        """

        folder_id = await self._folder_id(principal)
        default = name is None
        stem = (upload.filename or "soporte").replace("\\", "/").rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0] or "soporte"
        label = name or f"{stem} {datetime.now(UTC):%Y-%m-%d %H%M%S}"
        for attempt in range(3):
            command = UploadCommand(
                filename=upload.filename,
                content=upload.content,
                name=label if attempt == 0 else f"{label} {uuid4().hex[:6]}",
                description=description,
                tags=",".join(tags),
            )
            try:
                document = await self.documents.upload(self._area(), folder_id, command, principal)
            except ConflictError:
                if not default:
                    raise
                continue
            return StoredDocument(id=document.id, name=document.name)
        raise ConflictError("No se pudo dar un nombre único al soporte; inténtalo de nuevo")

    async def fetch(self, principal: Principal, document_id: UUID) -> StoredDocument:
        """A document already in the supports folder (so only classified files get linked).

        Raises:
            NotFoundError: If it does not exist or the caller cannot use its area.
            InvalidInputError: If it is in another folder.
        """

        document = await self.documents.get_document(document_id, principal)
        if document.folder_id != await self._folder_id(principal):
            raise InvalidInputError(
                f"El documento «{document.name}» no está en la carpeta {self.folder_name}"
            )
        return StoredDocument(id=document.id, name=document.name)

    async def discard(self, principal: Principal, document_id: UUID) -> None:
        """Delete a file this load stored, when linking it did not work out."""

        await self.documents.delete_document(document_id, principal)

    async def view_link(self, principal: Principal, document_id: UUID, *, inline: bool) -> ViewLink:
        """A temporary link to a support file, to display it (``inline``) or save it.

        The caller has already checked that the file backs the support being looked at; this
        does not ask for membership of the document manager's area.
        """

        link = await self.documents.download_link(document_id, principal, inline=inline)
        return ViewLink(url=link.url, filename=link.filename, expires_in=link.expires_in)


__all__ = ["DocumentManagerSupportStore"]
