"""Answer a document requested in a case with a file, and send it to the client."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.application.attachment_guard import AttachmentGuard
from app.modules.pqrsf.application.dto import CaseView, ItemChanges
from app.modules.pqrsf.domain.certificates import ManagerDocument
from app.modules.pqrsf.domain.email import CaseAttachment, case_attachment_key
from app.modules.pqrsf.domain.item_delivery import (
    SOURCE_UPLOAD,
    SOURCE_VAULT,
    ItemDelivery,
)
from app.modules.pqrsf.domain.notifications import DOCUMENT_DELIVERY, PENDING, Notification
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    CertificateRepository,
    EmailRepository,
    ItemDeliveryRepository,
    NotificationRepository,
)
from app.modules.pqrsf.domain.tracking import CaseItem, TrackingCase

logger = logging.getLogger(__name__)


class ItemTracking(Protocol):
    """What this service needs from case tracking: access rules and registering the answer."""

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may work it."""
        ...

    async def update_item(
        self, case_id: UUID, item_id: UUID, changes: ItemChanges, principal: Principal
    ) -> CaseView:
        """Register the response of a document."""
        ...

    def today(self) -> date:
        """Today in the business time zone."""
        ...


@dataclass(frozen=True)
class DeliveryResult:
    """The case after the answer and the answer itself."""

    view: CaseView
    delivery: ItemDelivery


class ItemDeliveryService:
    """Answer a requested document with a file of the manager or a one-off upload.

    Two ways, both sending the file to the client by email when asked:

    * **Link a file of the quality document manager** (which may be uploaded there first,
      through the document manager itself). Only files of that area are accepted, so a
      case cannot send out a document from another area.
    * **Upload it once**: the file stays with the case and goes in the email, and never
      enters the manager. Only for the documents whose type allows it (the ones answered
      by a person, or those a manager marked as sensitive) and for free-text ones.

    Registering the answer, the stored file and the queued email stand or fall together:
    an answer that cannot be registered (a late one without its cause) stores and sends
    nothing.
    """

    def __init__(
        self,
        tracking: ItemTracking,
        deliveries: ItemDeliveryRepository,
        certificates: CertificateRepository,
        emails: EmailRepository,
        notifications: NotificationRepository,
        storage: BinaryStorage,
        guard: AttachmentGuard,
        area_slug: str,
        wake: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            tracking (ItemTracking): Access rules and the registration of the answer.
            deliveries (ItemDeliveryRepository): The log of answers.
            certificates (CertificateRepository): The manager's files, to find and check them.
            emails (EmailRepository): Case file metadata.
            notifications (NotificationRepository): The outbox the email goes through.
            storage (BinaryStorage): Private bucket for one-off uploads.
            guard (AttachmentGuard): The same file rules as email intake.
            area_slug (str): The only area whose files can be linked.
            wake (Callable[[], None] | None): Tells the sender there is something to send now.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.tracking = tracking
        self.deliveries = deliveries
        self.certificates = certificates
        self.emails = emails
        self.notifications = notifications
        self.storage = storage
        self.guard = guard
        self.area_slug = area_slug
        self.wake = wake
        self.clock = clock

    async def search_documents(
        self,
        case_id: UUID,
        principal: Principal,
        query: str | None = None,
        folder_id: UUID | None = None,
    ) -> list[ManagerDocument]:
        """Files of the quality area to link, for whoever can work the case."""

        await self.tracking.require_work(case_id, principal)
        return await self.certificates.list_documents(self.area_slug, query, folder_id, 100)

    async def list_for_case(self, case_id: UUID, principal: Principal) -> list[ItemDelivery]:
        """The answers given in a case, with where their email stands."""

        await self.tracking.require_work(case_id, principal)
        return await self.deliveries.list_for_case(case_id)

    async def deliver(
        self,
        case_id: UUID,
        item_id: UUID,
        principal: Principal,
        *,
        source: str,
        document_id: UUID | None = None,
        filename: str | None = None,
        content: bytes | None = None,
        send_to_client: bool = True,
        delay_cause: str | None = None,
        delay_detail: str | None = None,
    ) -> DeliveryResult:
        """Answer one requested document of a case.

        Args:
            case_id (UUID): The case.
            item_id (UUID): The requested document.
            principal (Principal): The responsible or a manager.
            source (str): ``vault`` (a file of the manager) or ``upload`` (a one-off file).
            document_id (UUID | None): The manager file, for ``vault``.
            filename (str | None): Name of the uploaded file, for ``upload``.
            content (bytes | None): Bytes of the uploaded file, for ``upload``.
            send_to_client (bool): Email the file to the requester.
            delay_cause (str | None): Why it is late, when the agreed time is over.
            delay_detail (str | None): Detail of the delay.
        """

        case = await self.tracking.require_work(case_id, principal)
        if case.is_closed:
            raise ConflictError("El caso está finalizado: no se puede responder")
        item = next((i for i in case.items if i.id == item_id), None)
        if item is None:
            raise NotFoundError("Documento no encontrado en el caso")
        if item.responded_on is not None:
            raise ConflictError("Este documento ya tiene su respuesta registrada")
        recipient = (case.requester_email or "").strip()
        if send_to_client and "@" not in recipient:
            raise InvalidInputError("El caso no tiene un correo del solicitante para responderle")

        document: ManagerDocument | None = None
        upload: tuple[str, str, str, bytes] | None = None
        if source == SOURCE_VAULT:
            document = await self._vault_document(document_id)
        elif source == SOURCE_UPLOAD:
            upload = await self._clean_upload(item, filename, content)
        else:
            raise InvalidInputError("La fuente debe ser «vault» o «upload»")

        attachment = await self._store_upload(case.id, upload) if upload else None
        changes = {"responded_on"}
        if delay_cause is not None:
            changes.add("delay_cause")
        if delay_detail is not None:
            changes.add("delay_detail")
        try:
            view = await self.tracking.update_item(
                case_id,
                item_id,
                ItemChanges(
                    fields=frozenset(changes),
                    responded_on=self.tracking.today(),
                    delay_cause=delay_cause,
                    delay_detail=delay_detail,
                ),
                principal,
            )
        except Exception:
            if attachment is not None:
                await self._discard(attachment)
            raise

        delivery = ItemDelivery(
            id=uuid4(),
            case_id=case.id,
            item_id=item.id,
            source=source,
            filename=document.filename if document else attachment.original_filename,
            created_by=principal.user_id,
            created_at=self.clock(),
            document_id=document.id if document else None,
            attachment_id=attachment.id if attachment else None,
            document_path=document.path if document else None,
        )
        if send_to_client:
            delivery.notification_id = await self._queue(case, item, recipient, delivery)
            delivery.email_status = PENDING
        if attachment is not None:
            await self.emails.add_attachments([attachment])
        await self.deliveries.add(delivery)
        return DeliveryResult(view, delivery)

    # --- Helpers ---

    async def _vault_document(self, document_id: UUID | None) -> ManagerDocument:
        if document_id is None:
            raise InvalidInputError("Elige el archivo del gestor documental")
        document = await self.certificates.get_document(document_id)
        if document is None:
            raise NotFoundError("El archivo no existe en el gestor documental")
        if document.area_slug != self.area_slug:
            raise InvalidInputError(
                "Solo se pueden enviar archivos del gestor documental de Calidad"
            )
        return document

    async def _clean_upload(
        self, item: CaseItem, filename: str | None, content: bytes | None
    ) -> tuple[str, str, str, bytes]:
        if item.document_type is not None and not item.document_type.allows_direct_upload:
            raise InvalidInputError(
                "Este documento debe guardarse en el gestor documental: vincula un archivo de allí"
            )
        if not filename or not content:
            raise InvalidInputError("Adjunta el archivo")
        reason = self.guard.refuse(filename, len(content), 0, 0)
        if reason:
            raise InvalidInputError(f"{filename}: {reason}")
        return await self.guard.clean(filename, content)

    async def _store_upload(
        self, case_id: UUID, upload: tuple[str, str, str, bytes]
    ) -> CaseAttachment:
        name, extension, content_type, data = upload
        attachment_id = uuid4()
        key = case_attachment_key(case_id, attachment_id, extension)
        try:
            await self.storage.put_object(key, data, content_type)
        except Exception as exc:
            raise DependencyUnavailableError("No se pudo guardar el archivo") from exc
        return CaseAttachment(
            id=attachment_id,
            case_id=case_id,
            message_id=None,
            original_filename=name,
            extension=extension,
            content_type=content_type,
            size_bytes=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            storage_key=key,
            created_at=self.clock(),
        )

    async def _discard(self, attachment: CaseAttachment) -> None:
        try:
            await self.storage.delete_object(attachment.storage_key)
        except Exception:
            logger.warning("Could not remove orphan object %s", attachment.storage_key)

    async def _queue(
        self, case: TrackingCase, item: CaseItem, recipient: str, delivery: ItemDelivery
    ) -> UUID:
        name = item.document_type.name if item.document_type else item.description or "Documento"
        subject, body = templates.case_document(case.requester_name, case.code, name)
        notification = Notification(
            id=uuid4(),
            kind=DOCUMENT_DELIVERY,
            recipient_email=recipient,
            recipient_user_id=None,
            subject=subject,
            body=body,
            case_id=case.id,
            dedupe_key=f"{DOCUMENT_DELIVERY}:item:{delivery.id}",
            next_attempt_at=self.clock(),
            payload={
                "document_ids": [str(delivery.document_id)] if delivery.document_id else [],
                "file_ids": [str(delivery.attachment_id)] if delivery.attachment_id else [],
            },
        )
        await self.notifications.enqueue(notification)
        if self.wake is not None:
            self.wake()
        return notification.id


__all__ = ["DeliveryResult", "ItemDeliveryService"]
