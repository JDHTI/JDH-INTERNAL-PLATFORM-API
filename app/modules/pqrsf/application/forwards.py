"""Pass a supplier's answer on to the customer, by hand, from a mailbox nobody reads.

Whoever works the case decides: nothing is forwarded on its own. The email leaves from
the no-reply mailbox (``MAIL_NOREPLY_SENDER``) so the customer's answers never reach the
case, and the email says so at the top.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.mail.layout import BrandedEmail, render_html, render_text
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.domain.email import EVENT_EMAIL_RECEIVED, strip_quoted_reply
from app.modules.pqrsf.domain.entities import clean_multiline
from app.modules.pqrsf.domain.notifications import CUSTOMER_FORWARD, Notification
from app.modules.pqrsf.domain.ports import EmailRepository, NotificationRepository
from app.modules.pqrsf.domain.timeline import (
    TimelineContext,
    forwards_by_source,
    sender_role,
    supplier_addresses,
)
from app.modules.pqrsf.domain.tracking import CaseEvent, TrackingCase

logger = logging.getLogger(__name__)

NOTE_LIMIT = 2_000


class ForwardTracking(Protocol):
    """What forwarding needs from case tracking (access rules and the timeline)."""

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may work it."""
        ...

    async def list_events_of(self, case: TrackingCase) -> list[CaseEvent]:
        """The timeline of the case."""
        ...

    async def record_forward(
        self, case: TrackingCase, principal: Principal, note: str | None, data: dict
    ) -> EventView:
        """Write the forward in the timeline."""
        ...


@dataclass(frozen=True)
class ForwardPreview:
    """The email as the customer would get it."""

    to: str
    sender: str
    subject: str
    content: BrandedEmail
    attachments: list[str]

    @property
    def html(self) -> str:
        """The full HTML document."""

        return render_html(self.content)

    @property
    def text(self) -> str:
        """The plain-text version."""

        return render_text(self.content)


@dataclass(frozen=True)
class ForwardResult:
    """The new timeline entry and the queued email."""

    event: EventView
    notification: Notification


@dataclass(frozen=True)
class _Prepared:
    case: TrackingCase
    source: CaseEvent
    preview: ForwardPreview
    note: str | None
    file_ids: list[str]


class CustomerForwardService:
    """Forward a supplier's answer to the customer of a case, once and by hand."""

    def __init__(
        self,
        tracking: ForwardTracking,
        emails: EmailRepository,
        notifications: NotificationRepository,
        noreply_sender: str,
        mailbox: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            tracking (ForwardTracking): Access rules and the case timeline.
            emails (EmailRepository): The received email and its files.
            notifications (NotificationRepository): The outbox the email goes through.
            noreply_sender (str): Mailbox nobody reads; empty turns forwarding off.
            mailbox (str): The PQRSF mailbox, where the customer can write instead.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.tracking = tracking
        self.emails = emails
        self.notifications = notifications
        self.noreply_sender = noreply_sender.strip()
        self.mailbox = mailbox
        self.clock = clock

    async def preview(
        self, case_id: UUID, event_id: UUID, note: str | None, principal: Principal
    ) -> ForwardPreview:
        """The email the customer would get, without sending it.

        Same checks as :meth:`forward`, so a preview that works means the send will.
        """

        return (await self._prepare(case_id, event_id, note, principal)).preview

    async def forward(
        self, case_id: UUID, event_id: UUID, note: str | None, principal: Principal
    ) -> ForwardResult:
        """Queue the supplier's answer to the customer and write it in the timeline.

        Args:
            case_id (UUID): Case identifier.
            event_id (UUID): The ``email_received`` event with the supplier's answer.
            note (str | None): What the team adds for the customer (optional).
            principal (Principal): The responsible of the case or a manager.

        Returns:
            ForwardResult: The ``forwarded_to_customer`` entry and the queued email.

        Raises:
            NotFoundError: The case or the email does not exist.
            ForbiddenError: The caller cannot work the case.
            InvalidInputError: Not a supplier's answer, or no requester email.
            ConflictError: The case is finished, or the email was already forwarded.
            DependencyUnavailableError: No no-reply mailbox is configured.
        """

        prepared = await self._prepare(case_id, event_id, note, principal)
        preview = prepared.preview
        notification = Notification(
            id=uuid4(),
            kind=CUSTOMER_FORWARD,
            recipient_email=preview.to,
            recipient_user_id=None,
            subject=preview.subject,
            body=preview.text,
            case_id=prepared.case.id,
            # One forward per received email, even with two clicks at once.
            dedupe_key=f"{CUSTOMER_FORWARD}:{prepared.source.id}",
            next_attempt_at=self.clock(),
            payload={
                "source_event_id": str(prepared.source.id),
                "file_ids": prepared.file_ids,
                "email": preview.content.to_payload(),
            },
        )
        if not await self.notifications.enqueue(notification):
            raise ConflictError("Esa respuesta ya se envió al cliente")
        event = await self.tracking.record_forward(
            prepared.case,
            principal,
            prepared.note,
            {
                "source_event_id": str(prepared.source.id),
                "from": (prepared.source.data or {}).get("from"),
                "to": preview.to,
                "notification_id": str(notification.id),
                "file_ids": prepared.file_ids,
            },
        )
        return ForwardResult(event, notification)

    async def _prepare(
        self, case_id: UUID, event_id: UUID, note: str | None, principal: Principal
    ) -> _Prepared:
        case = await self.tracking.require_work(case_id, principal)
        events = await self.tracking.list_events_of(case)
        source = next(
            (e for e in events if e.id == event_id and e.event_type == EVENT_EMAIL_RECEIVED),
            None,
        )
        if source is None:
            raise NotFoundError("Correo no encontrado en el caso")
        context = TimelineContext(
            requester_name=case.requester_name,
            requester_email=case.requester_email,
            supplier_addresses=supplier_addresses(events),
        )
        if sender_role(source.data or {}, context) != "supplier":
            raise InvalidInputError("Solo se envían al cliente las respuestas de un proveedor")
        if str(source.id) in forwards_by_source(events):
            raise ConflictError("Esa respuesta ya se envió al cliente")
        if case.is_closed:
            raise ConflictError("El caso está finalizado: no se puede escribir al cliente")
        to = (case.requester_email or "").strip()
        if "@" not in to:
            raise InvalidInputError("El caso no tiene un correo del solicitante")
        if not self.noreply_sender:
            raise DependencyUnavailableError(
                "El envío al cliente necesita la cuenta no-reply (MAIL_NOREPLY_SENDER)"
            )
        cleaned = clean_multiline(note, "nota", NOTE_LIMIT) if note else None

        message_id = (source.data or {}).get("message_id")
        message = await self.emails.get_message_by_id(UUID(message_id)) if message_id else None
        text = strip_quoted_reply(message.body_text) if message else (source.note or "")
        files = (
            [a for a in await self.emails.list_attachments(case.id) if a.message_id == message.id]
            if message
            else []
        )
        subject, content = templates.customer_forward(case, text, cleaned, self.mailbox)
        preview = ForwardPreview(
            to=to,
            sender=self.noreply_sender,
            subject=subject,
            content=content,
            attachments=[a.original_filename for a in files],
        )
        return _Prepared(case, source, preview, cleaned, [str(a.id) for a in files])
