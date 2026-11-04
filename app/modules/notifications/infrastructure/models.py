"""Tables of the notifications."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

__all__ = ["NotificationModel", "NotificationRecipientModel", "PushSubscriptionModel"]

_TZ = DateTime(timezone=True)


class NotificationModel(Base):
    """One notice for an audience; unique per Strapi publication key."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "source", "source_id", "idempotency_key", name="uq_notifications_source_key"
        ),
        Index("ix_notifications_status_scheduled_for", "status", "scheduled_for"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_slug: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    audience_type: Mapped[str] = mapped_column(String(20), nullable=False)
    audience_values: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(_TZ)
    sent_at: Mapped[datetime | None] = mapped_column(_TZ)
    created_by: Mapped[str | None] = mapped_column(String(255))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())


class NotificationRecipientModel(Base):
    """A notification in one employee's bell."""

    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("notification_id", "employee_id", name="uq_notification_recipients_pair"),
        Index("ix_notification_recipients_employee_read", "employee_id", "read_at"),
        Index("ix_notification_recipients_employee_created", "employee_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("directory_employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    read_at: Mapped[datetime | None] = mapped_column(_TZ)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())


class PushSubscriptionModel(Base):
    """A browser subscribed to Web Push, owned by one employee."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (Index("ix_push_subscriptions_employee_id", "employee_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("directory_employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(_TZ)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )
