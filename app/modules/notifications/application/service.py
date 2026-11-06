"""Use cases of the notifications: the bell, read marks, Web Push and Strapi's ingestion.

Rules (the intranet's, kept as they were):
- Strapi sends one ingestion per publication. The key ``(source, source_id,
  idempotency_key)`` makes a retry harmless; republishing with a new key notifies again.
- Without ``programadaPara`` the notification goes out at once: recipients are fixed then
  (the audience as it is now) and push leaves right after. With it, the dispatch loop
  sends it when its time comes; more than 24 hours late it is cancelled, never sent late.
- An event with ``recordatorioDiasAntes`` also schedules a reminder at 08:00 (local time)
  that many days before ``fechaEvento``, unless that moment already passed.
- A person without an employee (a guest, the dev user) has an empty bell.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.notifications.domain.entities import (
    AudienceType,
    Feed,
    IngestCommand,
    IngestResult,
    Notification,
    Source,
    Status,
    Trigger,
)
from app.modules.notifications.domain.ports import Directory, NotificationRepository, PushSender

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 50
LATE_LIMIT = timedelta(hours=24)
DISPATCH_BATCH = 50
PUSH_BATCH = 25
REMINDER_HOUR = time(8, 0)


class NotificationService:
    """The bell of the general view and the ingestion from Strapi."""

    def __init__(
        self,
        repository: NotificationRepository,
        directory: Directory,
        push: PushSender,
        timezone: str = "America/Bogota",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (NotificationRepository): Notifications and subscriptions.
            directory (Directory): Employees and audiences.
            push (PushSender): Web Push.
            timezone (str): Zone of the reminders' 08:00.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.repository = repository
        self.directory = directory
        self.push = push
        self.zone = ZoneInfo(timezone)
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ the bell

    async def feed(
        self, principal: Principal, page: int = 1, page_size: int = 10, unread_only: bool = False
    ) -> Feed:
        """A page of the caller's notifications, newest first.

        Args:
            principal (Principal): The caller.
            page (int): From 1.
            page_size (int): 1 to ``MAX_PAGE_SIZE``.
            unread_only (bool): Only the unread ones.

        Returns:
            Feed: The page, the total and how many are unread.
        """

        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página desde 1 y tamaño entre 1 y {MAX_PAGE_SIZE}")
        employee_id = await self.directory.employee_id_of(principal)
        if employee_id is None:
            return Feed([], 0, page, page_size, 0)
        items, total, unread = await self.repository.feed(
            employee_id, unread_only, (page - 1) * page_size, page_size
        )
        return Feed(items, total, page, page_size, unread)

    async def unread_count(self, principal: Principal) -> int:
        """How many notifications the caller has not read.

        Args:
            principal (Principal): The caller.

        Returns:
            int: The count.
        """

        return (await self.feed(principal, 1, 1)).unread

    async def mark_read(self, principal: Principal, notification_id: UUID | None) -> None:
        """Mark one notification, or all of them, as read for the caller.

        Args:
            principal (Principal): The caller.
            notification_id (UUID | None): The notification; ``None`` for all.
        """

        employee_id = await self.directory.employee_id_of(principal)
        if employee_id is not None:
            await self.repository.mark_read(employee_id, notification_id, self.clock())

    # ------------------------------------------------------------------ push

    def public_key(self) -> str | None:
        """VAPID public key the browser subscribes with, or ``None`` when push is off."""

        return self.push.public_key

    async def subscribe(
        self,
        principal: Principal,
        endpoint: str,
        p256dh: str,
        auth: str,
        expires_at: datetime | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Register the caller's browser for push.

        Args:
            principal (Principal): The caller.
            endpoint (str): Push service URL of the browser.
            p256dh (str): Browser public key.
            auth (str): Browser auth secret.
            expires_at (datetime | None): When the browser drops the subscription.
            user_agent (str | None): The browser, for support.
        """

        if not endpoint.startswith("https://"):
            raise InvalidInputError("El endpoint de push debe ser https")
        employee_id = await self.directory.employee_id_of(principal)
        if employee_id is not None:
            await self.repository.save_subscription(
                employee_id, endpoint, p256dh, auth, expires_at, (user_agent or "")[:300] or None
            )

    async def unsubscribe(self, principal: Principal, endpoint: str) -> None:
        """Forget one of the caller's browsers.

        Args:
            principal (Principal): The caller.
            endpoint (str): Push service URL of the browser.
        """

        employee_id = await self.directory.employee_id_of(principal)
        if employee_id is not None:
            await self.repository.delete_subscription(employee_id, endpoint)

    # ------------------------------------------------------------------ ingestion

    async def ingest(self, command: IngestCommand) -> IngestResult:
        """Turn a Strapi publication into a notification (and maybe a reminder).

        Args:
            command (IngestCommand): What was published, for whom and when.

        Returns:
            IngestResult: The notification, whether it was a retry, its recipients.

        Raises:
            InvalidInputError: Missing audience values, a reminder outside an event, or
                a schedule in the past.
        """

        self._validate(command)
        existing = await self.repository.find_by_key(
            command.source.value, command.source_id, command.idempotency_key
        )
        if existing is not None:
            return IngestResult(existing.id, existing.status, True, None, None)
        recipients = None
        if command.audience_type is not AudienceType.TODOS:
            # Fails before creating anything when the audience has bad identifiers.
            await self.directory.audience(command.audience_type.value, command.audience_values)
        now = self.clock()
        immediate = command.scheduled_for is None
        notification = Notification(
            id=uuid.uuid4(),
            source=command.source,
            source_id=command.source_id,
            source_slug=command.slug,
            title=command.title,
            summary=command.summary,
            idempotency_key=command.idempotency_key,
            audience_type=command.audience_type,
            audience_values=list(command.audience_values),
            trigger=Trigger.PUBLICACION if immediate else Trigger.PROGRAMADA,
            status=Status.ENVIADA if immediate else Status.PROGRAMADA,
            scheduled_for=command.scheduled_for,
            sent_at=now if immediate else None,
            created_by=f"strapi:{command.published_by}" if command.published_by else None,
        )
        try:
            notification = await self.repository.create(notification)
        except ConflictError:
            # A concurrent retry won the race: answer with the one it created.
            winner = await self.repository.find_by_key(
                command.source.value, command.source_id, command.idempotency_key
            )
            if winner is None:
                raise
            return IngestResult(winner.id, winner.status, True, None, None)
        if immediate:
            recipients = await self._deliver(notification)
        reminder = await self._reminder(command, now)
        return IngestResult(notification.id, notification.status, False, recipients, reminder)

    async def audiences(self) -> dict[str, Any]:
        """Sites and areas Strapi's editors can aim at, with their sizes.

        Returns:
            dict[str, Any]: ``sedes`` and ``areas``.
        """

        sites, departments = await self.directory.audience_catalog()
        return {
            "sedes": [
                {"id": str(s.id), "nombre": s.name, "empleados": s.employee_count} for s in sites
            ],
            "areas": [{"nombre": name, "empleados": count} for name, count in departments],
        }

    # ------------------------------------------------------------------ platform notices

    async def ring(
        self, user_ids: list[UUID], key: str, title: str, summary: str, path: str
    ) -> None:
        """Put a notice from a platform module in these users' bells, with Web Push.

        Used by PQRSF for news on a case. Users without an employee (not synced, a
        guest) are skipped; the key makes a repeated call harmless.

        Args:
            user_ids (list[UUID]): Platform users to tell.
            key (str): Idempotency key of this notice.
            title (str): Headline (up to 200 characters).
            summary (str): One line (up to 500 characters).
            path (str): UI path the notice opens.
        """

        employees = await self.directory.employee_ids_of_users(user_ids)
        if not employees:
            return
        await self.ingest(
            IngestCommand(
                source=Source.PQRSF,
                source_id=path[:255],
                title=title[:200],
                idempotency_key=key[:255],
                audience_type=AudienceType.EMPLEADOS,
                audience_values=[str(employee) for employee in employees],
                slug=path[:255],
                summary=summary[:500],
            )
        )

    # ------------------------------------------------------------------ dispatch

    async def dispatch_due(self) -> tuple[int, int]:
        """Send the scheduled notifications whose time has come.

        Returns:
            tuple[int, int]: How many were sent and how many were cancelled (too late).
        """

        now = self.clock()
        sent = cancelled = 0
        for notification in await self.repository.due(now, DISPATCH_BATCH):
            if notification.scheduled_for and now - notification.scheduled_for > LATE_LIMIT:
                await self.repository.set_status(
                    notification.id, Status.CANCELADA, "Vencida: más de 24 h de retraso"
                )
                cancelled += 1
                continue
            if not await self.repository.claim(notification.id, now):
                continue
            await self._deliver(notification)
            sent += 1
        return sent, cancelled

    async def _deliver(self, notification: Notification) -> int:
        employees = await self.directory.audience(
            notification.audience_type.value, notification.audience_values
        )
        added = await self.repository.add_recipients(notification.id, employees)
        try:
            await self._push(notification, employees)
        except Exception:
            # Push is a courtesy: the bell already has the notification.
            logger.warning("Push of notification %s failed", notification.id, exc_info=True)
        return added

    async def _push(self, notification: Notification, employees: list[UUID]) -> None:
        if self.push.public_key is None or not employees:
            return
        subscriptions = await self.repository.subscriptions(employees)
        payload = {
            "id": str(notification.id),
            "titulo": notification.title,
            "cuerpo": (notification.summary or "")[:120],
            "href": notification.href,
            "icono": "/icons/jdh-192.png",
        }
        for start in range(0, len(subscriptions), PUSH_BATCH):
            batch = subscriptions[start : start + PUSH_BATCH]
            statuses = await asyncio.gather(*(self.push.send(sub, payload) for sub in batch))
            for subscription, status in zip(batch, statuses, strict=True):
                if status in (404, 410):
                    await self.repository.drop_subscription(subscription.id)

    async def _reminder(self, command: IngestCommand, now: datetime) -> UUID | None:
        if command.reminder_days_before is None or command.event_date is None:
            return None
        day = command.event_date - timedelta(days=command.reminder_days_before)
        moment = datetime.combine(day, REMINDER_HOUR, tzinfo=self.zone).astimezone(UTC)
        if moment <= now:
            return None
        key = f"{command.idempotency_key}:recordatorio"
        existing = await self.repository.find_by_key(command.source.value, command.source_id, key)
        if existing is not None:
            return existing.id
        reminder = await self.repository.create(
            Notification(
                id=uuid.uuid4(),
                source=command.source,
                source_id=command.source_id,
                source_slug=command.slug,
                title=command.title,
                summary=command.summary,
                idempotency_key=key,
                audience_type=command.audience_type,
                audience_values=list(command.audience_values),
                trigger=Trigger.RECORDATORIO,
                status=Status.PROGRAMADA,
                scheduled_for=moment,
                created_by=f"strapi:{command.published_by}" if command.published_by else None,
            )
        )
        return reminder.id

    def _validate(self, command: IngestCommand) -> None:
        if command.audience_type is not AudienceType.TODOS and not command.audience_values:
            raise InvalidInputError(
                f"audiencia.valores es requerido cuando audiencia.tipo = "
                f"{command.audience_type.value}"
            )
        if command.reminder_days_before is not None:
            if command.source is not Source.EVENTO:
                raise InvalidInputError("recordatorioDiasAntes solo aplica cuando fuente = EVENTO")
            if command.event_date is None:
                raise InvalidInputError("fechaEvento es requerido con recordatorioDiasAntes")
        if command.scheduled_for is not None and command.scheduled_for <= self.clock():
            raise InvalidInputError("programadaPara debe ser una fecha futura")
