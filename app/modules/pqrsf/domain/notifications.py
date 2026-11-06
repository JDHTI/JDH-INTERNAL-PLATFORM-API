"""PQRSF notifications: queued emails, organization-wide timers and their rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from uuid import UUID

from app.core.errors import InvalidInputError

# Kinds of notification.
ASSIGNMENT = "assignment"
# «There is news on your case»: an email from the client or a supplier joined it.
CASE_NEWS = "case_news"
DAILY_SUMMARY = "daily_summary"
OVERDUE_REMINDER = "overdue_reminder"
# A partial reply to the client written from a case comment.
CLIENT_REPLY = "client_reply"
# The automatic thank-you to whoever sent a suggestion or a compliment.
FEEDBACK_ACK = "feedback_ack"
# The acknowledgment with the radicado to whoever filed a request on the public form.
REQUEST_ACK = "request_ack"
# The certificates a customer asked for on the public form, attached from the document manager.
DOCUMENT_DELIVERY = "document_delivery"
# A message to a supplier written from a case (request, response, improvement, notice).
SUPPLIER_MESSAGE = "supplier_message"

# Statuses of a queued email.
PENDING = "pending"
SENT = "sent"
FAILED = "failed"

# The scheduled jobs; their names are also the keys of the settings and of the daily runs.
JOBS = (DAILY_SUMMARY, OVERDUE_REMINDER)
# A job that was due but the API was down still runs when it comes back within this
# window; later than that the day is skipped instead of sending a stale report.
GRACE = timedelta(hours=2)
# Waits before each retry of a failed send; after the last one it stays ``failed``.
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1


@dataclass
class Notification:
    """An email waiting to be sent from the PQRSF mailbox."""

    id: UUID
    kind: str
    recipient_email: str
    recipient_user_id: UUID | None
    subject: str
    body: str
    case_id: UUID | None
    # Makes a scheduled email idempotent: the same key is never queued twice.
    dedupe_key: str | None
    status: str = PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None
    # What a client reply needs beyond text: the comment event, the files to attach and
    # the message of the client's conversation to answer inside of.
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JobSchedule:
    """When one timer fires each business day (local time) and whether it is on."""

    enabled: bool
    at: time


@dataclass(frozen=True)
class NotificationSettings:
    """The organization-wide timers. Defaults: summary at 17:30, reminder at 07:30."""

    daily_summary: JobSchedule = field(default_factory=lambda: JobSchedule(True, time(17, 30)))
    overdue_reminder: JobSchedule = field(default_factory=lambda: JobSchedule(True, time(7, 30)))

    def schedule(self, job: str) -> JobSchedule:
        """The schedule of a job by name."""

        return getattr(self, job)

    def to_json(self) -> dict:
        """Serializable form kept in the settings table."""

        return {
            job: {"enabled": self.schedule(job).enabled, "time": format_time(self.schedule(job).at)}
            for job in JOBS
        }

    @classmethod
    def from_json(cls, value: dict | None) -> NotificationSettings:
        """Read the stored form; a missing job or field keeps its default."""

        defaults = cls()
        value = value or {}
        parsed = {}
        for job in JOBS:
            stored = value.get(job) or {}
            base = defaults.schedule(job)
            parsed[job] = JobSchedule(
                bool(stored.get("enabled", base.enabled)),
                parse_time(stored["time"]) if "time" in stored else base.at,
            )
        return cls(**parsed)


def parse_time(value: str) -> time:
    """Parse ``HH:MM`` (24 hours).

    Raises:
        InvalidInputError: The text is not a valid time of day.
    """

    try:
        hours, minutes = value.strip().split(":")
        if len(hours) != 2 or len(minutes) != 2:
            raise ValueError
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError("La hora debe tener el formato HH:MM (24 horas)") from exc


def format_time(value: time) -> str:
    """``HH:MM`` text of a time of day."""

    return f"{value.hour:02d}:{value.minute:02d}"


def retry_after(attempts: int, now: datetime) -> datetime | None:
    """When to try again after ``attempts`` failures, or ``None`` when it should give up."""

    if attempts >= MAX_ATTEMPTS:
        return None
    return now + RETRY_DELAYS[attempts - 1]
