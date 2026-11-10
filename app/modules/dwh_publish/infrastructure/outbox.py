"""Queue events in the caller's own transaction (the transactional outbox).

A module calls :func:`enqueue` with the session that writes its change, before the commit:
the change and its event are stored together or not at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.modules.dwh_publish.domain.entities import OutboxEvent
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel

# Rows per INSERT, far below PostgreSQL's 32767 bind parameters (5 per row).
_CHUNK = 1000


async def enqueue(
    session: Any,
    events: Sequence[OutboxEvent],
    *,
    not_before: datetime | None = None,
    requeue: bool = False,
) -> None:
    """Add the events to the outbox inside ``session``'s transaction.

    A version already queued is left as it is (the same version is the same record), unless
    ``requeue``: the reconciliation found the warehouse without it, so it is sent again.

    Args:
        session (Any): The open SQLAlchemy session of the change.
        events (Sequence[OutboxEvent]): The versions to publish.
        not_before (datetime | None): Do not send them before this moment (a big load
            waits for the warehouse's quiet hours).
        requeue (bool): Put an already published or failed version back in the queue.
    """

    rows = [
        {
            "topic": event.topic,
            "entity_id": event.entity_id,
            "version": event.version,
            "payload": event.payload,
            **({"next_attempt_at": not_before} if not_before is not None else {}),
        }
        for event in events
    ]
    for start in range(0, len(rows), _CHUNK):
        statement = insert(DwhOutboxModel).values(rows[start : start + _CHUNK])
        if requeue:
            statement = statement.on_conflict_do_update(
                constraint="uq_dwh_outbox_version",
                set_={
                    "payload": statement.excluded.payload,
                    "status": "pendiente",
                    "attempts": 0,
                    "next_attempt_at": func.now(),
                    "locked_until": None,
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(constraint="uq_dwh_outbox_version")
        await session.execute(statement)


__all__ = ["enqueue"]
