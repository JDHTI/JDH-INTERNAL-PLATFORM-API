"""PostgreSQL repository of the notifications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.notifications.domain.entities import (
    AudienceType,
    FeedItem,
    Notification,
    PushSubscription,
    Source,
    Status,
    Trigger,
)
from app.modules.notifications.infrastructure.models import (
    NotificationModel,
    NotificationRecipientModel,
    PushSubscriptionModel,
)

_N = NotificationModel
_R = NotificationRecipientModel
_P = PushSubscriptionModel


def _notification(model: NotificationModel) -> Notification:
    return Notification(
        id=model.id,
        source=Source(model.source),
        source_id=model.source_id,
        source_slug=model.source_slug,
        title=model.title,
        summary=model.summary,
        idempotency_key=model.idempotency_key,
        audience_type=AudienceType(model.audience_type),
        audience_values=list(model.audience_values or []),
        trigger=Trigger(model.trigger),
        status=Status(model.status),
        scheduled_for=model.scheduled_for,
        sent_at=model.sent_at,
        created_by=model.created_by,
        failure_reason=model.failure_reason,
        created_at=model.created_at,
    )


class SqlAlchemyNotificationRepository:
    """Notifications, recipients and push subscriptions; one transaction per call."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def find_by_key(
        self, source: str, source_id: str, idempotency_key: str
    ) -> Notification | None:
        """The notification already ingested with this key.

        Args:
            source (str): Source of the publication.
            source_id (str): Strapi document id.
            idempotency_key (str): Key of the publication.

        Returns:
            Notification | None: The notification, if any.
        """

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(_N).where(
                    _N.source == source,
                    _N.source_id == source_id,
                    _N.idempotency_key == idempotency_key,
                )
            )
            return _notification(model) if model else None

    async def create(self, notification: Notification) -> Notification:
        """Insert a notification.

        Args:
            notification (Notification): The notification.

        Returns:
            Notification: As stored.

        Raises:
            ConflictError: When the publication key already exists.
        """

        model = NotificationModel(
            id=notification.id,
            source=notification.source.value,
            source_id=notification.source_id,
            source_slug=notification.source_slug,
            title=notification.title,
            summary=notification.summary,
            idempotency_key=notification.idempotency_key,
            audience_type=notification.audience_type.value,
            audience_values=notification.audience_values,
            trigger=notification.trigger.value,
            status=notification.status.value,
            scheduled_for=notification.scheduled_for,
            sent_at=notification.sent_at,
            created_by=notification.created_by,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("La notificación ya existe") from exc
            await session.refresh(model)
            return _notification(model)

    async def add_recipients(self, notification_id: UUID, employee_ids: list[UUID]) -> int:
        """Give the notification to these employees.

        Args:
            notification_id (UUID): The notification.
            employee_ids (list[UUID]): The employees.

        Returns:
            int: How many were new.
        """

        if not employee_ids:
            return 0
        rows = [{"notification_id": notification_id, "employee_id": e} for e in employee_ids]
        async with self.database.session_factory() as session:
            result = await session.execute(
                insert(_R)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_notification_recipients_pair")
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def recipients(self, notification_id: UUID) -> list[UUID]:
        """Employees who received a notification.

        Args:
            notification_id (UUID): The notification.

        Returns:
            list[UUID]: Their identifiers.
        """

        async with self.database.session_factory() as session:
            return list(
                await session.scalars(
                    select(_R.employee_id).where(_R.notification_id == notification_id)
                )
            )

    async def due(self, now: datetime, limit: int) -> list[Notification]:
        """Scheduled notifications whose time has come, oldest first.

        Args:
            now (datetime): Current instant.
            limit (int): At most this many.

        Returns:
            list[Notification]: The notifications.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(_N)
                .where(_N.status == Status.PROGRAMADA.value, _N.scheduled_for <= now)
                .order_by(_N.scheduled_for)
                .limit(limit)
            )
            return [_notification(row) for row in rows]

    async def claim(self, notification_id: UUID, now: datetime) -> bool:
        """Mark a scheduled notification as sent.

        Args:
            notification_id (UUID): The notification.
            now (datetime): Sending instant.

        Returns:
            bool: ``False`` when it was no longer scheduled.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(_N)
                .where(_N.id == notification_id, _N.status == Status.PROGRAMADA.value)
                .values(status=Status.ENVIADA.value, sent_at=now)
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_status(
        self, notification_id: UUID, status: Status, reason: str | None = None
    ) -> None:
        """Change the status of a notification.

        Args:
            notification_id (UUID): The notification.
            status (Status): New status.
            reason (str | None): Why (for a cancellation).
        """

        async with self.database.session_factory() as session:
            await session.execute(
                update(_N)
                .where(_N.id == notification_id)
                .values(status=status.value, failure_reason=reason)
            )
            await session.commit()

    async def feed(
        self, employee_id: UUID, unread_only: bool, offset: int, limit: int
    ) -> tuple[list[FeedItem], int, int]:
        """A page of a person's notifications.

        Args:
            employee_id (UUID): The person.
            unread_only (bool): Only the unread ones.
            offset (int): Rows to skip.
            limit (int): Rows to return.

        Returns:
            tuple[list[FeedItem], int, int]: The page, the total and the unread count.
        """

        conditions = [_R.employee_id == employee_id]
        if unread_only:
            conditions.append(_R.read_at.is_(None))
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(_R).where(*conditions))
            unread = await session.scalar(
                select(func.count())
                .select_from(_R)
                .where(_R.employee_id == employee_id, _R.read_at.is_(None))
            )
            rows = await session.execute(
                select(_R, _N)
                .join(_N, _N.id == _R.notification_id)
                .where(*conditions)
                .order_by(_R.created_at.desc(), _R.id)
                .offset(offset)
                .limit(limit)
            )
            items = [
                FeedItem(
                    _notification(notification), recipient.read_at is not None, recipient.created_at
                )
                for recipient, notification in rows
            ]
            return items, int(total or 0), int(unread or 0)

    async def mark_read(
        self, employee_id: UUID, notification_id: UUID | None, now: datetime
    ) -> None:
        """Mark one notification (or all) as read for a person.

        Args:
            employee_id (UUID): The person.
            notification_id (UUID | None): The notification; ``None`` for all.
            now (datetime): Reading instant.
        """

        conditions = [_R.employee_id == employee_id, _R.read_at.is_(None)]
        if notification_id is not None:
            conditions.append(_R.notification_id == notification_id)
        async with self.database.session_factory() as session:
            await session.execute(update(_R).where(*conditions).values(read_at=now))
            await session.commit()

    async def save_subscription(
        self,
        employee_id: UUID,
        endpoint: str,
        p256dh: str,
        auth: str,
        expires_at: datetime | None,
        user_agent: str | None,
    ) -> None:
        """Register (or move to this person) a browser for push.

        Args:
            employee_id (UUID): The person.
            endpoint (str): Push service URL.
            p256dh (str): Browser public key.
            auth (str): Browser auth secret.
            expires_at (datetime | None): Expiration of the subscription.
            user_agent (str | None): The browser.
        """

        values = {
            "employee_id": employee_id,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "expires_at": expires_at,
            "user_agent": user_agent,
        }
        statement = insert(_P).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[_P.endpoint],
            set_={key: statement.excluded[key] for key in values if key != "endpoint"}
            | {"updated_at": func.now()},
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def delete_subscription(self, employee_id: UUID, endpoint: str) -> None:
        """Forget a browser of a person.

        Args:
            employee_id (UUID): The person.
            endpoint (str): Push service URL.
        """

        async with self.database.session_factory() as session:
            await session.execute(
                delete(_P).where(_P.employee_id == employee_id, _P.endpoint == endpoint)
            )
            await session.commit()

    async def subscriptions(self, employee_ids: list[UUID]) -> list[PushSubscription]:
        """The browsers of these employees.

        Args:
            employee_ids (list[UUID]): The employees.

        Returns:
            list[PushSubscription]: Their subscriptions.
        """

        if not employee_ids:
            return []
        async with self.database.session_factory() as session:
            rows = await session.scalars(select(_P).where(_P.employee_id.in_(employee_ids)))
            return [
                PushSubscription(row.id, row.employee_id, row.endpoint, row.p256dh, row.auth)
                for row in rows
            ]

    async def drop_subscription(self, subscription_id: UUID) -> None:
        """Forget a browser the push service no longer knows.

        Args:
            subscription_id (UUID): The subscription.
        """

        async with self.database.session_factory() as session:
            await session.execute(delete(_P).where(_P.id == subscription_id))
            await session.commit()
