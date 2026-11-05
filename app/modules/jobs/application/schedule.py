"""When the daily fallback of the jobs runs next."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_run_at(now: datetime, at: time, zone: ZoneInfo) -> datetime:
    """The next moment strictly after ``now`` when the local clock reads ``at``.

    Args:
        now (datetime): Aware current instant.
        at (time): Local time of the daily run.
        zone (ZoneInfo): Timezone of ``at``.

    Returns:
        datetime: The next run, in UTC.
    """

    local = now.astimezone(zone)
    candidate = datetime.combine(local.date(), at, tzinfo=zone)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=zone)
    return candidate.astimezone(UTC)
