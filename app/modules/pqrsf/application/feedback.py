"""Suggestions and compliments: stored apart from the cases and thanked at once."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.errors import DependencyUnavailableError, ForbiddenError, NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.domain.entities import RequestType
from app.modules.pqrsf.domain.feedback import (
    SKIP_RECIPIENT_LIMIT,
    SKIP_SAME_CONVERSATION,
    Feedback,
    FeedbackFile,
    FeedbackFilters,
    FeedbackPage,
    FeedbackSummary,
)
from app.modules.pqrsf.domain.notifications import FEEDBACK_ACK, Notification
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    FeedbackRepository,
    ManagerPolicy,
    NotificationRepository,
    PeopleDirectory,
)
from app.modules.sites.domain.entities import Site

logger = logging.getLogger(__name__)

# Why a message was not thanked when sending is switched off in this environment.
SKIP_DISABLED = "disabled"
_REPLY_WINDOW = timedelta(hours=24)
_BODY_LIMIT = 20_000


class FeedbackService:
    """Record suggestions and compliments, thank their senders and show them to managers.

    A thank-you is queued in the same outbox as every other PQRSF email, so it is retried
    when sending fails. It is never sent twice for the same message, never more than
    ``max_replies_per_email`` times a day to one address (so the public form cannot be used
    to flood someone's inbox), and a person who keeps writing in one email conversation is
    thanked once.
    """

    def __init__(
        self,
        repository: FeedbackRepository,
        notifications: NotificationRepository,
        storage: BinaryStorage,
        managers: ManagerPolicy,
        people: PeopleDirectory,
        replies_enabled: bool = True,
        max_replies_per_email: int = 3,
        wake: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (FeedbackRepository): Stored messages and their files.
            notifications (NotificationRepository): The email outbox.
            storage (BinaryStorage): Private bucket of the files.
            managers (ManagerPolicy): Who may read the messages.
            people (PeopleDirectory): Platform users, to name who opened a message.
            replies_enabled (bool): Whether thank-yous are queued at all.
            max_replies_per_email (int): Thank-yous one address receives per 24 hours.
            wake (Callable[[], None] | None): Tells the sender there is something to send now.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.repository = repository
        self.notifications = notifications
        self.storage = storage
        self.managers = managers
        self.people = people
        self.replies_enabled = replies_enabled
        self.max_replies_per_email = max_replies_per_email
        self.wake = wake
        self.clock = clock

    # --- Intake (form and mailbox) ---

    async def record(
        self,
        *,
        request_type: RequestType,
        origin: str,
        full_name: str,
        email: str,
        body: str,
        subject: str | None = None,
        business_name: str | None = None,
        site: Site | None = None,
        request_id: UUID | None = None,
        email_message_id: UUID | None = None,
    ) -> Feedback:
        """Store a message once; a repeated delivery of the same one returns the stored row."""

        stored, _ = await self.repository.add(
            Feedback(
                id=uuid4(),
                request_type=request_type,
                origin=origin,
                full_name=" ".join(full_name.split())[:200] or email,
                email=email.strip().lower(),
                body=(body.strip() or "(sin contenido)")[:_BODY_LIMIT],
                created_at=self.clock(),
                site=site,
                business_name=business_name,
                subject=subject,
                request_id=request_id,
                email_message_id=email_message_id,
            )
        )
        return stored

    async def add_files(self, files: list[FeedbackFile]) -> None:
        """Record the files stored for a message."""

        if files:
            await self.repository.add_files(files)

    async def kind_of(self, feedback_id: UUID) -> RequestType | None:
        """The type of a stored message."""

        feedback = await self.repository.get(feedback_id)
        return feedback.request_type if feedback else None

    async def files_of(self, feedback_id: UUID) -> list[FeedbackFile]:
        """The files already stored for a message."""

        return await self.repository.list_files(feedback_id)

    async def acknowledge_by_id(
        self, feedback_id: UUID, graph_id: str | None = None, conversation_id: str | None = None
    ) -> None:
        """Thank the sender of a stored message (see ``acknowledge``)."""

        feedback = await self.repository.get(feedback_id)
        if feedback is not None:
            await self.acknowledge(feedback, graph_id, conversation_id)

    async def acknowledge(
        self, feedback: Feedback, graph_id: str | None = None, conversation_id: str | None = None
    ) -> None:
        """Queue the thank-you of a message unless it was decided before.

        Args:
            feedback (Feedback): The stored message.
            graph_id (str | None): The email to answer inside of, when it came by email.
            conversation_id (str | None): Its conversation: one thank-you per conversation.
        """

        if feedback.replied:
            return
        reason = await self._skip_reason(feedback)
        if reason is not None:
            await self.repository.set_reply(feedback.id, None, reason)
            return
        subject, body = templates.feedback_ack(feedback.request_type.code, feedback.full_name)
        notification = Notification(
            id=uuid4(),
            kind=FEEDBACK_ACK,
            recipient_email=feedback.email,
            recipient_user_id=None,
            subject=subject,
            body=body,
            case_id=None,
            dedupe_key=(
                f"{FEEDBACK_ACK}:conversation:{conversation_id}"
                if conversation_id
                else f"{FEEDBACK_ACK}:{feedback.id}"
            ),
            next_attempt_at=self.clock(),
            payload={"feedback_id": str(feedback.id), "graph_id": graph_id},
        )
        if await self.notifications.enqueue(notification):
            await self.repository.set_reply(feedback.id, notification.id, None)
            if self.wake is not None:
                self.wake()
        else:
            await self.repository.set_reply(feedback.id, None, SKIP_SAME_CONVERSATION)

    async def _skip_reason(self, feedback: Feedback) -> str | None:
        if not self.replies_enabled:
            return SKIP_DISABLED
        sent = await self.notifications.count_recent(
            FEEDBACK_ACK, feedback.email, self.clock() - _REPLY_WINDOW
        )
        if sent >= self.max_replies_per_email:
            logger.warning("Thank-you limit reached for %s; not answering", feedback.email)
            return SKIP_RECIPIENT_LIMIT
        return None

    # --- Manager screens ---

    async def list_feedback(
        self, principal: Principal, filters: FeedbackFilters, page: int, size: int
    ) -> FeedbackPage:
        """One page of messages for a PQRSF manager."""

        await self._require_manager(principal)
        return await self.repository.list_feedback(filters, page, size)

    async def summary(self, principal: Principal) -> FeedbackSummary:
        """Totals and unread counts for a PQRSF manager."""

        await self._require_manager(principal)
        return await self.repository.summarize()

    async def open(self, feedback_id: UUID, principal: Principal) -> Feedback:
        """A message with its files; the first opening marks it as read."""

        await self._require_manager(principal)
        feedback = await self.repository.get(feedback_id)
        if feedback is None:
            raise NotFoundError("Mensaje no encontrado")
        if feedback.read_at is None:
            now = self.clock()
            await self.repository.mark_read(feedback.id, principal.user_id, now)
            feedback.read_at, feedback.read_by_user_id = now, principal.user_id
        return feedback

    async def reader(self, feedback: Feedback) -> DirectoryUser | None:
        """The person who opened the message first."""

        if feedback.read_by_user_id is None:
            return None
        users = await self.people.get_users([feedback.read_by_user_id])
        return users.get(feedback.read_by_user_id)

    async def download(
        self, feedback_id: UUID, file_id: UUID, principal: Principal
    ) -> tuple[FeedbackFile, bytes]:
        """One file of a message with its bytes."""

        await self._require_manager(principal)
        found = await self.repository.get_file(feedback_id, file_id)
        if found is None:
            raise NotFoundError("Archivo no encontrado")
        try:
            return found, await self.storage.get_object(found.storage_key)
        except Exception as exc:
            raise DependencyUnavailableError("El almacenamiento de archivos no responde") from exc

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quienes gestionan PQRSF pueden ver estos mensajes")


__all__ = ["FeedbackService"]
