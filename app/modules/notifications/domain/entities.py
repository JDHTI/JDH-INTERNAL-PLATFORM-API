"""Notifications of the general view: what Strapi publishes reaches each employee."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class Source(StrEnum):
    """What a notification is about (the intranet's values)."""

    NOTICIA = "NOTICIA"
    EVENTO = "EVENTO"
    CERTIFICADO = "CERTIFICADO"


class AudienceType(StrEnum):
    """Who receives a notification."""

    TODOS = "TODOS"
    SEDE = "SEDE"
    AREA = "AREA"
    EMPLEADOS = "EMPLEADOS"


class Trigger(StrEnum):
    """Why a notification exists."""

    PUBLICACION = "PUBLICACION"
    PROGRAMADA = "PROGRAMADA"
    RECORDATORIO = "RECORDATORIO"


class Status(StrEnum):
    """Where a notification is in its life."""

    PROGRAMADA = "PROGRAMADA"
    ENVIADA = "ENVIADA"
    CANCELADA = "CANCELADA"


@dataclass
class Notification:
    """One notice sent (or to send) to an audience."""

    id: UUID
    source: Source
    source_id: str
    title: str
    idempotency_key: str
    audience_type: AudienceType
    status: Status
    trigger: Trigger
    source_slug: str | None = None
    summary: str | None = None
    audience_values: list[str] = field(default_factory=list)
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    created_by: str | None = None
    failure_reason: str | None = None
    created_at: datetime | None = None

    @property
    def href(self) -> str:
        """Where the notification takes the person in the general view."""

        return href_for(self.source, self.source_slug)


@dataclass(frozen=True)
class FeedItem:
    """A notification as one person sees it."""

    notification: Notification
    read: bool
    received_at: datetime


@dataclass(frozen=True)
class Feed:
    """A page of a person's notifications."""

    items: list[FeedItem]
    total: int
    page: int
    page_size: int
    unread: int


@dataclass(frozen=True)
class IngestCommand:
    """What Strapi sends when something is published (the intranet's contract)."""

    source: Source
    source_id: str
    title: str
    idempotency_key: str
    audience_type: AudienceType
    audience_values: list[str] = field(default_factory=list)
    slug: str | None = None
    summary: str | None = None
    scheduled_for: datetime | None = None
    reminder_days_before: int | None = None
    event_date: date | None = None
    published_by: str | None = None


@dataclass(frozen=True)
class IngestResult:
    """What the ingestion did."""

    id: UUID
    status: Status
    duplicate: bool
    recipients: int | None
    reminder_id: UUID | None


@dataclass(frozen=True)
class PushSubscription:
    """A browser that receives Web Push for one employee."""

    id: UUID
    employee_id: UUID
    endpoint: str
    p256dh: str
    auth: str


def href_for(source: Source, slug: str | None) -> str:
    """The page of the general view a notification opens.

    Args:
        source (Source): What it is about.
        slug (str | None): Strapi slug of the news or event.

    Returns:
        str: A path of the UI.
    """

    if source is Source.NOTICIA:
        return f"/noticias/{slug}" if slug else "/noticias"
    if source is Source.EVENTO:
        return f"/eventos/{slug}" if slug else "/eventos"
    return "/perfil"
