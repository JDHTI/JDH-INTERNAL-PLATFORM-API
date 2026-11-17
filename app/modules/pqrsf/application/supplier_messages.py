"""Write to a supplier from a case: a request, a response, an improvement or a notice."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.application.comments import CaseCommentService, UploadedFile
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.domain.email import CaseAttachment
from app.modules.pqrsf.domain.entities import clean_multiline, clean_optional
from app.modules.pqrsf.domain.notifications import FAILED, PENDING, SUPPLIER_MESSAGE, Notification
from app.modules.pqrsf.domain.ports import (
    EmailRepository,
    NotificationRepository,
    PqrsfRepository,
    SupplierContactRepository,
)
from app.modules.pqrsf.domain.supplier_messages import (
    MESSAGE_KINDS,
    SupplierContact,
    clean_cc,
    clean_kind,
    clean_recipients,
)
from app.modules.pqrsf.domain.tracking import TrackingCase

# What one message to a supplier may carry, evidence of the client included. Exchange
# takes messages of about 35 MB once encoded.
MESSAGE_ATTACHMENT_LIMIT = 25 * 1024 * 1024


class OmAttachments(Protocol):
    """The OM a message can carry (implemented by the OM service)."""

    async def for_sending(self, case: TrackingCase, om_id: UUID):  # noqa: ANN201
        """The OM of the case and its PDF (``(om, OmFile)``)."""
        ...

    async def mark_sent(self, case, om, principal: Principal, recipients: Sequence[str]) -> None:
        """Record that the OM left for the supplier."""
        ...


class SupplierTracking(Protocol):
    """What supplier messages need from case tracking."""

    async def require_view(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may see it."""
        ...

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may work it."""
        ...

    async def record_supplier_message(
        self, case_id: UUID, text: str, principal: Principal, data: dict
    ) -> EventView:
        """Write the message in the timeline."""
        ...


@dataclass(frozen=True)
class SupplierMessageResult:
    """The timeline entry, the files stored with it and one queued email per recipient."""

    event: EventView
    files: list[CaseAttachment]
    rejected: list[dict]
    emails: list[Notification]


class SupplierMessageService:
    """Send messages to suppliers from a case and keep them in its timeline."""

    def __init__(
        self,
        tracking: SupplierTracking,
        files: CaseCommentService,
        emails: EmailRepository,
        notifications: NotificationRepository,
        requests: PqrsfRepository,
        contacts: SupplierContactRepository,
        wake: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        oms: OmAttachments | None = None,
    ) -> None:
        """Create the service.

        Args:
            tracking (SupplierTracking): Access rules and the case timeline.
            files (CaseCommentService): The same file rules and storage as comments.
            emails (EmailRepository): Case file metadata.
            notifications (NotificationRepository): The outbox the emails go through.
            requests (PqrsfRepository): The public request: its products and evidence.
            contacts (SupplierContactRepository): Addresses already used per supplier.
            wake (Callable[[], None] | None): Wakes the sender so the email leaves at once.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
            oms (OmAttachments | None): The OMs, so an improvement message carries one.
        """

        self.oms = oms
        self.tracking = tracking
        self.files = files
        self.emails = emails
        self.notifications = notifications
        self.requests = requests
        self.contacts = contacts
        self.wake = wake
        self.clock = clock

    async def send(
        self,
        case_id: UUID,
        principal: Principal,
        *,
        kind: str,
        to: Sequence[str],
        body: str,
        supplier_code: str | None = None,
        supplier_name: str | None = None,
        files: Sequence[UploadedFile] = (),
        include_evidence: bool = False,
        include_case_data: bool = True,
        cc: Sequence[str] = (),
        om_id: UUID | None = None,
    ) -> SupplierMessageResult:
        """Email a supplier from an open case and record it in the timeline.

        Only who works the case (its responsible or a manager) writes to suppliers. Each
        recipient gets its own email from the PQRSF mailbox, with the consecutive in the
        subject so the answer comes back to the case.

        Args:
            case_id (UUID): Case identifier.
            principal (Principal): The author.
            kind (str): ``request``, ``response``, ``improvement`` or ``notice``.
            to (Sequence[str]): One to five addresses.
            body (str): The message.
            supplier_code (str | None): ERP supplier code, to suggest these addresses again.
            supplier_name (str | None): Supplier name shown in the timeline.
            files (Sequence[UploadedFile]): New files to attach (kept with the case).
            include_evidence (bool): Also attach the client's photos and video.
            include_case_data (bool): Add client, site and products to the email.
            cc (Sequence[str]): People of the organization copied (up to 5). Each address
                gets one copy, on the first email.
            om_id (UUID | None): An improvement opportunity of the case: its PDF goes
                attached (only with ``kind=improvement``) and it is marked as sent.

        Returns:
            SupplierMessageResult: The timeline entry, the files and the queued emails.
        """

        case = await self.tracking.require_work(case_id, principal)
        if case.is_closed:
            raise ConflictError("El caso está finalizado: no se puede escribir al proveedor")
        kind = clean_kind(kind)
        recipients = clean_recipients(list(to))
        copied = clean_cc(cc, self.files.cc_domains, exclude=recipients)
        text = clean_multiline(body, "mensaje", 5_000)
        if text is None:
            raise InvalidInputError("El mensaje no puede estar vacío")
        code = clean_optional(supplier_code, "código del proveedor", 40) or ""
        name = clean_optional(supplier_name, "proveedor", 200)

        om = None
        if om_id is not None:
            if kind != "improvement":
                raise InvalidInputError("La OM se envía con un mensaje de oportunidad de mejora")
            if self.oms is None:
                raise InvalidInputError("El envío de la OM no está disponible")
            om, om_file = await self.oms.for_sending(case, om_id)
        request = await self.requests.get_request(case.request_id) if case.request_id else None
        evidence = list(request.attachments) if include_evidence and request else []
        accepted, rejected = await self.files.accept_files(files)
        if om is not None:
            # The generated PDF is trusted: it skips the upload rules and goes first.
            accepted.insert(0, (om_file.filename, "pdf", om_file.content_type, om_file.content))
        size = sum(len(item[3]) for item in accepted) + sum(a.size_bytes for a in evidence)
        if size > MESSAGE_ATTACHMENT_LIMIT:
            raise InvalidInputError("Los adjuntos de un mensaje no pueden superar 25 MB en total")

        stored = await self.files.store_files(case.id, accepted)
        notification_ids = [uuid4() for _ in recipients]
        data: dict = {
            "kind": kind,
            "to": recipients,
            "cc": copied,
            "supplier_code": code or None,
            "supplier_name": name,
            "file_ids": [str(a.id) for a in stored],
            "evidence_ids": [str(a.id) for a in evidence],
            "notification_ids": [str(value) for value in notification_ids],
        }
        if om is not None:
            data["om_id"], data["om_code"] = str(om.id), om.code
        if rejected:
            data["rejected_files"] = rejected
        try:
            event = await self.tracking.record_supplier_message(case.id, text, principal, data)
            attachments = [replace(a, comment_event_id=event.event.id) for a in stored]
            await self.emails.add_attachments(attachments)
        except Exception:
            await self.files.discard_files(stored)
            raise

        subject, message = templates.supplier_message(
            case,
            MESSAGE_KINDS[kind],
            text,
            request.products if request else (),
            include_case_data,
        )
        queued = []
        for position, (recipient, notification_id) in enumerate(
            zip(recipients, notification_ids, strict=True)
        ):
            notification = Notification(
                id=notification_id,
                kind=SUPPLIER_MESSAGE,
                recipient_email=recipient,
                recipient_user_id=None,
                subject=subject,
                body=message,
                case_id=case.id,
                dedupe_key=f"{SUPPLIER_MESSAGE}:{event.event.id}:{recipient}",
                next_attempt_at=self.clock(),
                payload={
                    "event_id": str(event.event.id),
                    "file_ids": data["file_ids"],
                    "request_id": str(request.id) if request else None,
                    "evidence_ids": data["evidence_ids"],
                    # One copy for the people in CC, not one per supplier address.
                    "cc": copied if position == 0 else [],
                },
            )
            await self.notifications.enqueue(notification)
            queued.append(notification)
        await self.contacts.remember(
            [
                SupplierContact(uuid4(), code, name, recipient, last_used_at=self.clock())
                for recipient in recipients
            ]
        )
        if om is not None and self.oms is not None:
            await self.oms.mark_sent(case, om, principal, recipients)
        if self.wake is not None:
            self.wake()
        return SupplierMessageResult(event, attachments, rejected, queued)

    async def list_sent(self, case_id: UUID, principal: Principal) -> list[Notification]:
        """The emails sent to suppliers from a case, with their delivery state.

        Args:
            case_id (UUID): Case identifier.
            principal (Principal): Anyone who can see the case.

        Returns:
            list[Notification]: One entry per recipient of each message.
        """

        await self.tracking.require_view(case_id, principal)
        return await self.notifications.list_for_case(case_id, SUPPLIER_MESSAGE)

    async def retry(
        self, case_id: UUID, notification_id: UUID, principal: Principal
    ) -> Notification:
        """Send an email to a supplier again after its delivery gave up.

        Args:
            case_id (UUID): Case identifier.
            notification_id (UUID): The failed email.
            principal (Principal): Who works the case.

        Returns:
            Notification: The email, pending again.
        """

        await self.tracking.require_work(case_id, principal)
        item = await self.notifications.get_notification(notification_id)
        if item is None or item.case_id != case_id or item.kind != SUPPLIER_MESSAGE:
            raise NotFoundError("Mensaje al proveedor no encontrado")
        if item.status != FAILED:
            raise ConflictError("Solo se puede reintentar un mensaje que falló")
        item.status, item.attempts, item.error = PENDING, 0, None
        item.next_attempt_at = self.clock()
        await self.notifications.save_notification(item)
        if self.wake is not None:
            self.wake()
        return item

    async def contacts_for(
        self, supplier_code: str | None, query: str | None, limit: int = 10
    ) -> list[SupplierContact]:
        """Addresses already used for a supplier, or matching a text.

        Args:
            supplier_code (str | None): ERP supplier code.
            query (str | None): Part of the address, contact or supplier name.
            limit (int): Most results (1 to 50).

        Returns:
            list[SupplierContact]: Most recently used first.
        """

        return await self.contacts.search(
            (supplier_code or "").strip() or None,
            clean_optional(query, "búsqueda", 100),
            max(1, min(limit, 50)),
        )
