"""The catalog of certificates that are sent automatically (a panel for PQRSF managers)."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.domain.certificates import CertificateEntry, ManagerDocument
from app.modules.pqrsf.domain.ports import CertificateRepository, ManagerPolicy

_DOCUMENT_LIMIT = 100


class CertificateService:
    """Choose, for each document type of the public form, the file that answers it.

    The file is a document of the quality area of the document manager, so whoever keeps
    that folder up to date keeps the certificates up to date. A request for a type with an
    active certificate is answered with the file and no case is opened (see
    ``DocumentDeliveryService``); everything else stays manual.
    """

    def __init__(
        self, repository: CertificateRepository, managers: ManagerPolicy, area_slug: str
    ) -> None:
        """Create the service.

        Args:
            repository (CertificateRepository): Certificates and the manager's files.
            managers (ManagerPolicy): Who manages PQRSF.
            area_slug (str): The only area whose files can be a certificate.
        """

        self.repository = repository
        self.managers = managers
        self.area_slug = area_slug

    async def list_entries(self, principal: Principal) -> list[CertificateEntry]:
        """Every document type the form offers, with its certificate if any."""

        await self._require_manager(principal)
        return await self.repository.list_entries()

    async def save(
        self, document_type_id: UUID, document_id: UUID, is_active: bool, principal: Principal
    ) -> CertificateEntry:
        """Set the file of a document type, and whether it is sent automatically."""

        await self._require_manager(principal)
        if await self.repository.get_entry(document_type_id) is None:
            raise NotFoundError(
                "Ese documento no se ofrece en el formulario o no admite envío automático"
            )
        document = await self.repository.get_document(document_id)
        if document is None:
            raise NotFoundError("El archivo no existe en el gestor documental")
        if document.area_slug != self.area_slug:
            raise InvalidInputError(
                "Solo se pueden enviar archivos del gestor documental de Calidad"
            )
        await self.repository.save(document_type_id, document_id, is_active, principal.user_id)
        entry = await self.repository.get_entry(document_type_id)
        assert entry is not None
        return entry

    async def remove(self, document_type_id: UUID, principal: Principal) -> None:
        """Stop answering a document type automatically; its requests open cases again."""

        await self._require_manager(principal)
        entry = await self.repository.get_entry(document_type_id)
        if entry is None or entry.certificate is None:
            raise NotFoundError("Ese documento no tiene certificado configurado")
        await self.repository.delete(document_type_id)

    async def search_documents(
        self, principal: Principal, query: str | None = None, folder_id: UUID | None = None
    ) -> list[ManagerDocument]:
        """Files of the quality area to choose a certificate from."""

        await self._require_manager(principal)
        return await self.repository.list_documents(
            self.area_slug, query, folder_id, _DOCUMENT_LIMIT
        )

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quienes gestionan PQRSF pueden configurar los certificados")


__all__ = ["CertificateService"]
