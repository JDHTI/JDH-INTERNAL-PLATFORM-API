"""Read the files stored under a case; access follows the case's own rules."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.errors import DependencyUnavailableError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.domain.email import CaseAttachment
from app.modules.pqrsf.domain.ports import BinaryStorage, EmailRepository


class CaseAccessCheck(Protocol):
    """Raises unless the caller may see the case (implemented by tracking)."""

    async def require_view(self, case_id: UUID, principal: Principal) -> object:
        """Return the case, or raise not found / forbidden."""
        ...


class CaseAttachmentService:
    """List and download the attachments of the cases a person can see."""

    def __init__(
        self, emails: EmailRepository, access: CaseAccessCheck, storage: BinaryStorage
    ) -> None:
        """Create the service.

        Args:
            emails (EmailRepository): Attachment metadata.
            access (CaseAccessCheck): Case visibility rules.
            storage (BinaryStorage): Private bucket.
        """

        self.emails = emails
        self.access = access
        self.storage = storage

    async def list_attachments(self, case_id: UUID, principal: Principal) -> list[CaseAttachment]:
        """Attachments of a case the caller can see."""

        await self.access.require_view(case_id, principal)
        return await self.emails.list_attachments(case_id)

    async def download(
        self, case_id: UUID, attachment_id: UUID, principal: Principal
    ) -> tuple[CaseAttachment, bytes]:
        """One attachment with its bytes."""

        await self.access.require_view(case_id, principal)
        attachment = await self.emails.get_attachment(case_id, attachment_id)
        if attachment is None:
            raise NotFoundError("Adjunto del caso no encontrado")
        try:
            content = await self.storage.get_object(attachment.storage_key)
        except Exception as exc:
            raise DependencyUnavailableError("El almacenamiento de archivos no responde") from exc
        return attachment, content
