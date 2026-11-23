"""Supporting documents of supports: upload and classify them, one by one or by selection.

A selection is a list of support ids, a search (the same dynamic filters of the supports
list: references, clients, suppliers, dates...) or both. The files are stored once in the
supports folder of the document manager and linked to every support selected, so a screenshot
of the e-mail that agreed a support for twenty references is one document with twenty links.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain.attachments import (
    Attachment,
    AttachResult,
    FileUpload,
    SelectionPreview,
    StoredDocument,
    ViewLink,
)
from app.modules.supplier_supports.domain.ports import AttachmentRepository, SupportDocumentStore

MAX_FILES = 10
BASE_TAG = "apoyos"
MAX_SUPPLIER_TAGS = 10
# Without one of these a search would match every support: refuse it as a selection.
_CRITERIA = (
    "conditions",
    "customer_nits",
    "business_groups",
    "supplier_codes",
    "product_skus",
    "q",
    "state",
    "validity",
    "needs_review",
    "has_issues",
    "has_conflicts",
    "has_attachments",
)


class SupportAttachmentService:
    """Store supporting files and link them to supports."""

    def __init__(
        self,
        supports: SupplierSupportService,
        attachments: AttachmentRepository,
        store: SupportDocumentStore,
        max_supports: int = 1000,
        max_file_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        """Create the service.

        Args:
            supports (SupplierSupportService): Resolves searches and permissions.
            attachments (AttachmentRepository): Persistence of the links.
            store (SupportDocumentStore): The document manager's supports folder.
            max_supports (int): Most supports one upload may be linked to.
            max_file_bytes (int): Largest file read from a request (the document manager
                still applies its own limit and file types).
        """

        self.supports = supports
        self.attachments = attachments
        self.store = store
        self.max_supports = max_supports
        self.max_file_bytes = max_file_bytes

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.supports.can_manage(principal):
            raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")

    async def _resolve(
        self, support_ids: Sequence[UUID], search: Mapping[str, Any] | None
    ) -> tuple[list[UUID], bool]:
        """The supports of a selection and whether it was cut at the limit."""

        found: dict[UUID, None] = {}
        if support_ids:
            unique = list(dict.fromkeys(support_ids))
            known = await self.attachments.existing_supports(unique)
            if missing := [item for item in unique if item not in known]:
                raise InvalidInputError(
                    f"{len(missing)} de los apoyos elegidos no existen o están anulados"
                )
            found.update(dict.fromkeys(unique))
        if search is not None:
            if not any(search.get(name) not in (None, [], "") for name in _CRITERIA):
                raise InvalidInputError(
                    "La búsqueda de la selección necesita al menos un filtro "
                    "(proveedor, cliente, referencia, estado, texto...)"
                )
            found.update(
                dict.fromkeys(await self.supports.search_ids(self.max_supports + 1, **search))
            )
        if not found:
            raise InvalidInputError("La selección no coincide con ningún apoyo")
        ids = list(found)
        return ids[: self.max_supports], len(ids) > self.max_supports

    async def preview(
        self,
        principal: Principal,
        *,
        support_ids: Sequence[UUID] = (),
        search: Mapping[str, Any] | None = None,
    ) -> SelectionPreview:
        """How many supports a selection reaches, before uploading anything.

        Args:
            principal (Principal): The caller; must be a manager.
            support_ids (Sequence[UUID]): Supports chosen one by one.
            search (Mapping[str, Any] | None): Criteria of the supports search.

        Returns:
            SelectionPreview: The total (cut at the limit, ``capped``) and the first ids.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If the selection is empty, too loose or has unknown supports.
        """

        await self._require_manager(principal)
        ids, capped = await self._resolve(support_ids, search)
        return SelectionPreview(
            total=len(ids), capped=capped, limit=self.max_supports, supports=ids[:20]
        )

    async def attach(
        self,
        principal: Principal,
        *,
        support_ids: Sequence[UUID] = (),
        search: Mapping[str, Any] | None = None,
        files: Sequence[FileUpload] = (),
        document_ids: Sequence[UUID] = (),
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] = (),
    ) -> AttachResult:
        """Store files in the supports folder and link them, with the documents already
        there, to every support of the selection. Everything or nothing: when something
        fails, the files this call stored are removed.

        Args:
            principal (Principal): The caller; must be a manager.
            support_ids (Sequence[UUID]): Supports chosen one by one.
            search (Mapping[str, Any] | None): Criteria of the supports search.
            files (Sequence[FileUpload]): New files (screenshots, PDFs...).
            document_ids (Sequence[UUID]): Documents already in the supports folder.
            name (str | None): Name of the document; only with a single file.
            description (str | None): Shared by the stored documents.
            tags (Sequence[str]): Extra tags; ``apoyos`` and the supplier codes are added.

        Returns:
            AttachResult: The documents linked, the supports reached and the new links.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If there is nothing to attach, the selection is invalid or it
                exceeds the limit.
        """

        await self._require_manager(principal)
        if not files and not document_ids:
            raise InvalidInputError("Agrega al menos un archivo o un documento de la carpeta")
        if len(files) > MAX_FILES:
            raise InvalidInputError(f"Máximo {MAX_FILES} archivos por carga")
        if name and len(files) > 1:
            raise InvalidInputError("El nombre solo puede usarse con un archivo")
        ids, capped = await self._resolve(support_ids, search)
        if capped:
            raise InvalidInputError(
                f"La selección supera {self.max_supports} apoyos; acota la búsqueda"
            )
        codes = await self.attachments.supplier_codes(ids)
        all_tags = [
            BASE_TAG,
            *tags,
            *([f"proveedor {code}" for code in codes] if len(codes) <= MAX_SUPPLIER_TAGS else []),
        ]

        stored: list[StoredDocument] = []
        uploaded: list[UUID] = []
        try:
            for upload in files:
                document = await self.store.store(
                    principal, upload, name=name, description=description, tags=all_tags
                )
                stored.append(document)
                uploaded.append(document.id)
            for document_id in dict.fromkeys(document_ids):
                stored.append(await self.store.fetch(principal, document_id))
            created = await self.attachments.link_many(
                [(support, document.id) for document in stored for support in ids],
                principal.email,
                datetime.now(UTC),
            )
        except Exception:
            for document_id in uploaded:
                with contextlib.suppress(Exception):
                    await self.store.discard(principal, document_id)
            raise
        return AttachResult(
            documents=[document.id for document in stored], supports=len(ids), links_created=created
        )

    async def list(self, support_id: UUID) -> list[Attachment]:
        """The documents backing a support.

        Raises:
            NotFoundError: If the support does not exist.
        """

        await self.supports.get(support_id)
        return await self.attachments.list_for_support(support_id)

    async def view(
        self, principal: Principal, support_id: UUID, document_id: UUID, *, download: bool = False
    ) -> tuple[Attachment, ViewLink]:
        """A temporary link to open a document of a support (or to save it, with ``download``).

        Whoever can read the support can open its proofs: the document must be linked to that
        support, and the document manager's area membership is not asked (the supports master is
        read by people who are not in BI).

        Raises:
            NotFoundError: If the support does not exist or does not have that document.
        """

        await self.supports.get(support_id)
        attachment = await self.attachments.get_link(support_id, document_id)
        if attachment is None:
            raise NotFoundError("El apoyo no tiene ese documento")
        link = await self.store.view_link(principal, document_id, inline=not download)
        return attachment, link

    async def unlink(self, principal: Principal, support_id: UUID, document_id: UUID) -> None:
        """Take a document off a support; it stays in the document manager and on the others.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the support does not have that document.
        """

        await self._require_manager(principal)
        if not await self.attachments.unlink(support_id, document_id):
            raise NotFoundError("El apoyo no tiene ese documento")


__all__ = ["SupportAttachmentService"]
