"""Answer a document request with the configured certificates, without a person."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.domain.certificates import (
    DELIVERY_FAILED,
    FALLBACK_DISABLED,
    FALLBACK_RECIPIENT_LIMIT,
    DeliveryFilters,
    DeliveryItem,
    DeliveryPage,
    DeliveryPlan,
    DeliverySummary,
    DocumentDelivery,
)
from app.modules.pqrsf.domain.entities import PqrsfRequest
from app.modules.pqrsf.domain.notifications import (
    DOCUMENT_DELIVERY,
    FAILED,
    PENDING,
    Notification,
)
from app.modules.pqrsf.domain.ports import (
    CertificateRepository,
    DeliveryRepository,
    ManagerPolicy,
    NotificationRepository,
    PeopleDirectory,
)

logger = logging.getLogger(__name__)

_WINDOW = timedelta(hours=24)


class DocumentDeliveryService:
    """Split a document request into what is sent now and what a person answers.

    A requested document that has an active certificate (a file of the quality document
    manager) is attached to an email from the PQRSF mailbox, queued in the same outbox as
    every other PQRSF email, so it is retried when sending fails. What has no certificate,
    and anything written as «otro», goes to a case as before. When nothing is left for a
    person no case is opened: the request is logged in its own section instead.

    Nothing is sent automatically (everything becomes a case) when sending is switched off
    in this environment, or when the address already received its daily share: the public
    form must not become a way to flood a mailbox with our email.
    """

    def __init__(
        self,
        deliveries: DeliveryRepository,
        certificates: CertificateRepository,
        notifications: NotificationRepository,
        managers: ManagerPolicy,
        people: PeopleDirectory,
        enabled: bool = True,
        max_emails_per_address: int = 5,
        max_email_bytes: int = 15 * 1024 * 1024,
        wake: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            deliveries (DeliveryRepository): The log of automatic deliveries.
            certificates (CertificateRepository): Which file answers each document type.
            notifications (NotificationRepository): The email outbox.
            managers (ManagerPolicy): Who may read the log.
            people (PeopleDirectory): Platform users, to name who opened a delivery.
            enabled (bool): Whether emails are sent in this environment.
            max_emails_per_address (int): Delivery emails one address receives per 24 hours.
            max_email_bytes (int): Attachments per email; a bigger request goes in several.
            wake (Callable[[], None] | None): Tells the sender there is something to send now.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.deliveries = deliveries
        self.certificates = certificates
        self.notifications = notifications
        self.managers = managers
        self.people = people
        self.enabled = enabled
        self.max_emails_per_address = max_emails_per_address
        self.max_email_bytes = max_email_bytes
        self.wake = wake
        self.clock = clock

    # --- Public form ---

    async def plan(self, request: PqrsfRequest) -> DeliveryPlan:
        """What of a request is sent now and what a person answers.

        A request that was already answered keeps its split, whatever the certificates
        look like today: a retry of the submission never sends or opens twice.
        """

        existing = await self.deliveries.get_by_request(request.id)
        if existing is not None:
            sent = {item.document_type_id for item in existing.items}
            return DeliveryPlan(
                existing.items, [d for d in request.documents if d.document_type_id not in sent]
            )
        type_ids = [d.document_type_id for d in request.documents if d.document_type_id]
        found = await self.certificates.automatic_for(type_ids)
        if not found:
            return DeliveryPlan([], list(request.documents))
        fallback = await self._fallback(request.email)
        if fallback is not None:
            logger.warning("Certificates of %s not sent automatically: %s", request.id, fallback)
            return DeliveryPlan([], list(request.documents), fallback)
        # The customer is told what they asked for, not how the file is named in the manager.
        names = await self.certificates.type_names(list(found))
        automatic: list[DeliveryItem] = []
        manual = []
        for document in request.documents:
            certificate = (
                found.get(document.document_type_id) if document.document_type_id else None
            )
            if certificate is None or certificate.document is None:
                manual.append(document)
                continue
            automatic.append(
                DeliveryItem(
                    document_type_id=document.document_type_id,
                    name=names.get(document.document_type_id, certificate.document.name),
                    document_id=certificate.document.id,
                    filename=certificate.document.filename,
                    size_bytes=certificate.document.size_bytes,
                )
            )
        return DeliveryPlan(automatic, manual)

    async def automatic_documents(self, document_type_ids: list[UUID]) -> set[UUID]:
        """The document types a request gets answered at once (none when sending is off)."""

        if not self.enabled:
            return set()
        return set(await self.certificates.automatic_for(document_type_ids))

    async def _fallback(self, email: str) -> str | None:
        if not self.enabled:
            return FALLBACK_DISABLED
        sent = await self.notifications.count_recent(
            DOCUMENT_DELIVERY, email.lower(), self.clock() - _WINDOW
        )
        return FALLBACK_RECIPIENT_LIMIT if sent >= self.max_emails_per_address else None

    async def record(self, request: PqrsfRequest, plan: DeliveryPlan) -> DocumentDelivery:
        """Log the request and what is going to be sent (idempotent); nothing is emailed yet.

        The emails wait for `send` so the first one can name the case that is opened, in
        between, for what a person still has to answer.
        """

        names = await self.certificates.type_names(
            [d.document_type_id for d in plan.manual if d.document_type_id]
        )
        manual = [
            d.description or names.get(d.document_type_id, "Documento")
            for d in plan.manual
            if d.description or d.document_type_id
        ]
        delivery, _ = await self.deliveries.add(
            DocumentDelivery(
                id=uuid4(),
                full_name=request.full_name,
                email=request.email,
                created_at=self.clock(),
                items=plan.automatic,
                request_id=request.id,
                site=request.site,
                business_name=request.business_name,
                nit=request.nit,
                manual_items=manual,
            )
        )
        return delivery

    async def set_case(self, delivery: DocumentDelivery, case_code: str) -> None:
        """Remember the case opened for what a person answers."""

        await self.deliveries.set_case(delivery.id, case_code)
        delivery.case_code = case_code

    async def send(self, delivery: DocumentDelivery) -> None:
        """Queue the emails with the certificates; a delivery that has them queued is left alone."""

        if not delivery.notification_ids:
            await self._queue(delivery)

    async def _queue(self, delivery: DocumentDelivery) -> None:
        chunks = self._chunks(delivery.items)
        queued: list[UUID] = []
        for number, items in enumerate(chunks, start=1):
            subject, body = templates.document_delivery(
                delivery.full_name,
                [item.name for item in items],
                number,
                len(chunks),
                delivery.case_code,
                delivery.manual_items,
            )
            notification = Notification(
                id=uuid4(),
                kind=DOCUMENT_DELIVERY,
                recipient_email=delivery.email,
                recipient_user_id=None,
                subject=subject,
                body=body,
                case_id=None,
                dedupe_key=f"{DOCUMENT_DELIVERY}:{delivery.id}:{number}",
                next_attempt_at=self.clock(),
                payload={
                    "delivery_id": str(delivery.id),
                    "document_ids": [str(item.document_id) for item in items if item.document_id],
                },
            )
            await self.notifications.enqueue(notification)
            queued.append(notification.id)
        await self.deliveries.set_notifications(delivery.id, queued)
        if self.wake is not None:
            self.wake()

    def _chunks(self, items: list[DeliveryItem]) -> list[list[DeliveryItem]]:
        """Group the documents so no email carries more than the size cap (a big file alone)."""

        chunks: list[list[DeliveryItem]] = []
        size = 0
        for item in items:
            if not chunks or size + item.size_bytes > self.max_email_bytes:
                chunks.append([])
                size = 0
            chunks[-1].append(item)
            size += item.size_bytes
        return chunks

    # --- Manager screens ---

    async def list_deliveries(
        self, principal: Principal, filters: DeliveryFilters, page: int, size: int
    ) -> DeliveryPage:
        """One page of deliveries for a PQRSF manager."""

        await self._require_manager(principal)
        return await self.deliveries.list_deliveries(filters, page, size)

    async def summary(self, principal: Principal) -> DeliverySummary:
        """Totals and unread, for the menu badge."""

        await self._require_manager(principal)
        return await self.deliveries.summarize()

    async def open(self, delivery_id: UUID, principal: Principal) -> DocumentDelivery:
        """A delivery in full; the first opening marks it as read."""

        await self._require_manager(principal)
        delivery = await self.deliveries.get(delivery_id)
        if delivery is None:
            raise NotFoundError("Solicitud de documentos no encontrada")
        if delivery.read_at is None:
            now = self.clock()
            await self.deliveries.mark_read(delivery.id, principal.user_id, now)
            delivery.read_at, delivery.read_by_user_id = now, principal.user_id
        return delivery

    async def reader(self, delivery: DocumentDelivery) -> DirectoryUser | None:
        """The person who opened the delivery first."""

        if delivery.read_by_user_id is None:
            return None
        users = await self.people.get_users([delivery.read_by_user_id])
        return users.get(delivery.read_by_user_id)

    async def retry(self, delivery_id: UUID, principal: Principal) -> DocumentDelivery:
        """Send again the emails of a delivery that gave up."""

        delivery = await self.open(delivery_id, principal)
        if delivery.status != DELIVERY_FAILED:
            raise ConflictError("Solo se puede reintentar un envío que falló")
        for notification_id in delivery.notification_ids:
            item = await self.notifications.get_notification(notification_id)
            if item is not None and item.status == FAILED:
                item.status, item.attempts, item.error = PENDING, 0, None
                item.next_attempt_at = self.clock()
                await self.notifications.save_notification(item)
        if self.wake is not None:
            self.wake()
        refreshed = await self.deliveries.get(delivery_id)
        assert refreshed is not None
        return refreshed

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quienes gestionan PQRSF pueden ver estas solicitudes")


__all__ = ["DocumentDeliveryService"]
