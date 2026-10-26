"""Case comments with files and, optionally, a partial reply to the client by email."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.application.attachment_guard import AttachmentGuard
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.domain.email import CaseAttachment, case_attachment_key
from app.modules.pqrsf.domain.entities import clean_multiline
from app.modules.pqrsf.domain.notifications import (
    CLIENT_REPLY,
    FAILED,
    PENDING,
    Notification,
)
from app.modules.pqrsf.domain.ports import BinaryStorage, EmailRepository, NotificationRepository
from app.modules.pqrsf.domain.tracking import TrackingCase

logger = logging.getLogger(__name__)

# Graph takes an attachment inside the request up to about 3 MB (bigger ones need an
# upload session), so a reply to the client carries at most that much in total.
REPLY_ATTACHMENT_LIMIT = 3 * 1024 * 1024


class CommentTracking(Protocol):
    """What the comment service needs from case tracking (access rules and the timeline)."""

    async def require_view(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may see it."""
        ...

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may work it."""
        ...

    async def add_comment(
        self, case_id: UUID, body: str, principal: Principal, data: dict | None = None
    ) -> EventView:
        """Write the comment in the timeline."""
        ...


@dataclass(frozen=True)
class UploadedFile:
    """A file sent with a comment, as received."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class CommentResult:
    """The new timeline entry, its files and the client reply it queued, if any."""

    event: EventView
    files: list[CaseAttachment]
    rejected: list[dict]
    client_reply: Notification | None


class CaseCommentService:
    """Write comments that can carry files and be sent to the client as a partial reply."""

    def __init__(
        self,
        tracking: CommentTracking,
        emails: EmailRepository,
        notifications: NotificationRepository,
        storage: BinaryStorage,
        guard: AttachmentGuard,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            tracking (CommentTracking): Access rules and the case timeline.
            emails (EmailRepository): Case file metadata and the client's thread.
            notifications (NotificationRepository): The outbox the reply goes through.
            storage (BinaryStorage): Private bucket for the files.
            guard (AttachmentGuard): The same file rules as email intake.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.tracking = tracking
        self.emails = emails
        self.notifications = notifications
        self.storage = storage
        self.guard = guard
        self.clock = clock

    async def comment(
        self,
        case_id: UUID,
        body: str,
        principal: Principal,
        files: Sequence[UploadedFile] = (),
        reply_to_client: bool = False,
    ) -> CommentResult:
        """Add a comment, store its files and, if asked, email it to the client.

        Anyone who can see the case may comment; replying to the client needs the right
        to work the case, an open case and the requester's email. A reply is partial:
        it never closes the case. A file that fails the rules is reported and skipped.

        Args:
            case_id (UUID): Case identifier.
            body (str): Comment text (also the text of the email).
            principal (Principal): The author.
            files (Sequence[UploadedFile]): Files to attach to the comment (and the email).
            reply_to_client (bool): Also email the comment to the requester.

        Returns:
            CommentResult: The timeline entry, the stored files and the queued reply.
        """

        case = await (
            self.tracking.require_work(case_id, principal)
            if reply_to_client
            else self.tracking.require_view(case_id, principal)
        )
        text = clean_multiline(body, "comentario", 5_000)
        if text is None:
            raise InvalidInputError("El comentario no puede estar vacío")
        recipient = ""
        if reply_to_client:
            if case.is_closed:
                raise ConflictError("El caso está finalizado: no se puede responder al cliente")
            recipient = (case.requester_email or "").strip()
            if "@" not in recipient:
                raise InvalidInputError(
                    "El caso no tiene un correo del solicitante para responderle"
                )

        accepted, rejected = await self.accept_files(files)
        if reply_to_client and sum(len(a[3]) for a in accepted) > REPLY_ATTACHMENT_LIMIT:
            raise InvalidInputError(
                "Los adjuntos de una respuesta al cliente no pueden superar 3 MB en total"
            )

        stored = await self.store_files(case.id, accepted)
        notification_id = uuid4()
        data: dict = {"file_ids": [str(a.id) for a in stored], "reply_to_client": reply_to_client}
        if rejected:
            data["rejected_files"] = rejected
        if reply_to_client:
            data["client_reply"] = {"to": recipient, "notification_id": str(notification_id)}
        try:
            event = await self.tracking.add_comment(case.id, text, principal, data)
            attachments = [replace(a, comment_event_id=event.event.id) for a in stored]
            await self.emails.add_attachments(attachments)
        except Exception:
            await self.discard_files(stored)
            raise

        reply = None
        if reply_to_client:
            reply = await self._queue_reply(case, text, recipient, notification_id, event, stored)
        return CommentResult(event, attachments, rejected, reply)

    async def list_client_replies(self, case_id: UUID, principal: Principal) -> list[Notification]:
        """The partial replies sent to the client of a case, with their delivery state."""

        await self.tracking.require_view(case_id, principal)
        return await self.notifications.list_for_case(case_id, CLIENT_REPLY)

    async def retry_client_reply(
        self, case_id: UUID, notification_id: UUID, principal: Principal
    ) -> Notification:
        """Send a reply again after its delivery gave up (the responsible or a manager)."""

        await self.tracking.require_work(case_id, principal)
        item = await self.notifications.get_notification(notification_id)
        if item is None or item.case_id != case_id or item.kind != CLIENT_REPLY:
            raise NotFoundError("Respuesta al cliente no encontrada")
        if item.status != FAILED:
            raise ConflictError("Solo se puede reintentar una respuesta que falló")
        item.status, item.attempts, item.error = PENDING, 0, None
        item.next_attempt_at = self.clock()
        await self.notifications.save_notification(item)
        return item

    # --- Helpers ---

    async def accept_files(
        self, files: Sequence[UploadedFile]
    ) -> tuple[list[tuple[str, str, str, bytes]], list[dict]]:
        """Clean and scan uploaded files with the case rules; also used by supplier messages.

        Args:
            files (Sequence[UploadedFile]): Files as received.

        Returns:
            tuple[list[tuple[str, str, str, bytes]], list[dict]]: ``(name, extension,
            content type, bytes)`` of each accepted file (repeated ones once), and
            ``{name, reason}`` of each refused one.
        """

        accepted: list[tuple[str, str, str, bytes]] = []
        rejected: list[dict] = []
        known: set[str] = set()
        total = 0
        for upload in files:
            reason = self.guard.refuse(upload.filename, len(upload.content), len(accepted), total)
            if reason:
                rejected.append({"name": upload.filename, "reason": reason})
                continue
            try:
                name, extension, content_type, data = await self.guard.clean(
                    upload.filename, upload.content
                )
            except InvalidInputError as exc:
                rejected.append({"name": upload.filename, "reason": str(exc)})
                continue
            checksum = hashlib.sha256(data).hexdigest()
            if checksum in known:
                continue
            known.add(checksum)
            total += len(data)
            accepted.append((name, extension, content_type, data))
        return accepted, rejected

    async def store_files(
        self, case_id: UUID, accepted: list[tuple[str, str, str, bytes]]
    ) -> list[CaseAttachment]:
        """Put accepted files in the bucket under the case; nothing stays if one fails.

        Args:
            case_id (UUID): Case the files belong to.
            accepted (list[tuple[str, str, str, bytes]]): Files from ``accept_files``.

        Returns:
            list[CaseAttachment]: Metadata to record once the timeline entry exists.
        """

        stored: list[CaseAttachment] = []
        try:
            for name, extension, content_type, data in accepted:
                attachment_id = uuid4()
                key = case_attachment_key(case_id, attachment_id, extension)
                await self.storage.put_object(key, data, content_type)
                stored.append(
                    CaseAttachment(
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
                )
        except Exception as exc:
            await self.discard_files(stored)
            if isinstance(exc, DependencyUnavailableError):
                raise
            raise DependencyUnavailableError("No se pudieron guardar los archivos") from exc
        return stored

    async def discard_files(self, stored: list[CaseAttachment]) -> None:
        """Remove stored objects whose timeline entry could not be written.

        Args:
            stored (list[CaseAttachment]): Files from ``store_files``.

        Returns:
            None
        """

        for attachment in stored:
            try:
                await self.storage.delete_object(attachment.storage_key)
            except Exception:
                logger.warning("Could not remove orphan object %s", attachment.storage_key)

    async def _queue_reply(
        self,
        case: TrackingCase,
        text: str,
        recipient: str,
        notification_id: UUID,
        event: EventView,
        stored: list[CaseAttachment],
    ) -> Notification:
        subject, body = templates.client_reply(case, text)
        notification = Notification(
            id=notification_id,
            kind=CLIENT_REPLY,
            recipient_email=recipient,
            recipient_user_id=None,
            subject=subject,
            body=body,
            case_id=case.id,
            dedupe_key=f"{CLIENT_REPLY}:{event.event.id}",
            next_attempt_at=self.clock(),
            payload={
                "event_id": str(event.event.id),
                "file_ids": [str(a.id) for a in stored],
                "graph_id": await self.emails.latest_inbound_graph_id(case.id),
            },
        )
        await self.notifications.enqueue(notification)
        return notification
