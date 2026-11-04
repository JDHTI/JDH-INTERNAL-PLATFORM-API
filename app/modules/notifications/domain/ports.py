"""Contracts the notifications use cases need from the outside."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.notifications.domain.entities import (
    FeedItem,
    Notification,
    PushSubscription,
    Status,
)


class NotificationRepository(Protocol):
    """Notifications, their recipients and the push subscriptions."""

    async def find_by_key(
        self, source: str, source_id: str, idempotency_key: str
    ) -> Notification | None:
        """The notification already ingested with this key."""

    async def create(self, notification: Notification) -> Notification:
        """Insert a notification; ``ConflictError`` when the key already exists."""

    async def add_recipients(self, notification_id: UUID, employee_ids: list[UUID]) -> int:
        """Give the notification to these employees; returns how many were new."""

    async def recipients(self, notification_id: UUID) -> list[UUID]:
        """Employees who received a notification."""

    async def due(self, now: datetime, limit: int) -> list[Notification]:
        """Scheduled notifications whose time has come, oldest first."""

    async def claim(self, notification_id: UUID, now: datetime) -> bool:
        """Mark a scheduled notification as sent; ``False`` when someone else did."""

    async def set_status(
        self, notification_id: UUID, status: Status, reason: str | None = None
    ) -> None:
        """Change the status of a notification."""

    async def feed(
        self, employee_id: UUID, unread_only: bool, offset: int, limit: int
    ) -> tuple[list[FeedItem], int, int]:
        """A page of a person's notifications, the total and the unread count."""

    async def mark_read(
        self, employee_id: UUID, notification_id: UUID | None, now: datetime
    ) -> None:
        """Mark one notification (or all with ``None``) as read for a person."""

    async def save_subscription(
        self,
        employee_id: UUID,
        endpoint: str,
        p256dh: str,
        auth: str,
        expires_at: datetime | None,
        user_agent: str | None,
    ) -> None:
        """Register (or move to this person) a browser for push."""

    async def delete_subscription(self, employee_id: UUID, endpoint: str) -> None:
        """Forget a browser of a person."""

    async def subscriptions(self, employee_ids: list[UUID]) -> list[PushSubscription]:
        """The browsers of these employees."""

    async def drop_subscription(self, subscription_id: UUID) -> None:
        """Forget a browser the push service no longer knows (404, 410)."""


class Directory(Protocol):
    """Employees, from the directory module (implemented by ``DirectoryService``)."""

    async def employee_id_of(self, principal: Principal) -> UUID | None:
        """The caller's employee."""

    async def audience(self, kind: str, values: list[str]) -> list[UUID]:
        """Active employees of an audience."""

    async def audience_catalog(self) -> tuple[list[Any], list[tuple[str, int]]]:
        """Sites and departments with their sizes."""


class PushSender(Protocol):
    """Web Push to one browser."""

    @property
    def public_key(self) -> str | None:
        """VAPID public key, or ``None`` when push is off."""

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int | None:
        """Send; returns the HTTP status on failure (``None`` when delivered)."""
