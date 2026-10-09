"""PostgreSQL repository for the notification outbox, timers and job runs."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.pqrsf.domain.notifications import PENDING, Notification
from app.modules.pqrsf.infrastructure.models import JobRunModel, NotificationModel, SettingModel

SETTINGS_KEY = "notifications"


def _notification(model: NotificationModel) -> Notification:
    return Notification(
        id=model.id,
        kind=model.kind,
        recipient_email=model.recipient_email,
        recipient_user_id=model.recipient_user_id,
        subject=model.subject,
        body=model.body,
        case_id=model.case_id,
        dedupe_key=model.dedupe_key,
        status=model.status,
        attempts=model.attempts,
        next_attempt_at=model.next_attempt_at,
        sent_at=model.sent_at,
        error=model.error,
        created_at=model.created_at,
        payload=dict(model.payload or {}),
    )


class SqlAlchemyNotificationRepository:
    """Persist queued emails, the organization timers and the daily job runs."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def enqueue(self, notification: Notification) -> bool:
        """Queue an email unless its dedupe key is already queued.

        Returns:
            bool: ``False`` when the same scheduled email was queued before.
        """

        statement = (
            insert(NotificationModel)
            .values(
                id=notification.id,
                kind=notification.kind,
                recipient_email=notification.recipient_email,
                recipient_user_id=notification.recipient_user_id,
                subject=notification.subject,
                body=notification.body,
                case_id=notification.case_id,
                dedupe_key=notification.dedupe_key,
                status=notification.status,
                attempts=notification.attempts,
                next_attempt_at=notification.next_attempt_at,
                payload=notification.payload,
            )
            .on_conflict_do_nothing(constraint="pqrsf_notifications_dedupe_key_key")
            .returning(NotificationModel.id)
        )
        async with self.database.session_factory() as session:
            inserted = await session.scalar(statement)
            await session.commit()
        return inserted is not None

    async def claim_due(self, now: datetime, limit: int) -> list[Notification]:
        """Pending emails whose next attempt has come, oldest first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(NotificationModel)
                .where(
                    NotificationModel.status == PENDING,
                    NotificationModel.next_attempt_at <= now,
                )
                .order_by(NotificationModel.created_at)
                .limit(limit)
            )
            return [_notification(model) for model in rows]

    async def get_notification(self, notification_id: UUID) -> Notification | None:
        """One queued email by identifier."""

        async with self.database.session_factory() as session:
            model = await session.get(NotificationModel, notification_id)
            return _notification(model) if model else None

    async def list_for_case(self, case_id: UUID, kind: str) -> list[Notification]:
        """The emails of one kind queued for a case, oldest first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(NotificationModel)
                .where(NotificationModel.case_id == case_id, NotificationModel.kind == kind)
                .order_by(NotificationModel.created_at)
            )
            return [_notification(model) for model in rows]

    async def count_recent(self, kind: str, recipient_email: str, since: datetime) -> int:
        """Emails of one kind queued for an address since an instant."""

        async with self.database.session_factory() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(NotificationModel)
                .where(
                    NotificationModel.kind == kind,
                    func.lower(NotificationModel.recipient_email) == recipient_email.lower(),
                    NotificationModel.created_at >= since,
                )
            )
        return int(total or 0)

    async def save_notification(self, notification: Notification) -> None:
        """Persist the outcome of a send attempt."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(NotificationModel)
                .where(NotificationModel.id == notification.id)
                .values(
                    status=notification.status,
                    attempts=notification.attempts,
                    next_attempt_at=notification.next_attempt_at,
                    sent_at=notification.sent_at,
                    error=notification.error,
                )
            )
            await session.commit()

    async def get_settings(self) -> dict | None:
        """The stored timers, or ``None`` when they were never changed."""

        async with self.database.session_factory() as session:
            return await session.scalar(
                select(SettingModel.value).where(SettingModel.key == SETTINGS_KEY)
            )

    async def save_settings(self, value: dict, user_id: UUID) -> None:
        """Replace the timers."""

        statement = insert(SettingModel).values(key=SETTINGS_KEY, value=value, updated_by=user_id)
        statement = statement.on_conflict_do_update(
            index_elements=[SettingModel.key],
            set_={"value": value, "updated_by": user_id, "updated_at": func.now()},
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def claim_job_run(self, job: str, run_date: date) -> bool:
        """Record that a job runs on a date; ``False`` when it already did."""

        statement = (
            insert(JobRunModel)
            .values(job=job, run_date=run_date)
            .on_conflict_do_nothing(index_elements=[JobRunModel.job, JobRunModel.run_date])
            .returning(JobRunModel.job)
        )
        async with self.database.session_factory() as session:
            claimed = await session.scalar(statement)
            await session.commit()
        return claimed is not None

    async def release_job_run(self, job: str, run_date: date) -> None:
        """Forget a run that failed so the next tick can try again."""

        async with self.database.session_factory() as session:
            await session.execute(
                delete(JobRunModel).where(JobRunModel.job == job, JobRunModel.run_date == run_date)
            )
            await session.commit()
