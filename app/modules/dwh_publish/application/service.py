"""Send the outbox to the warehouse: batches, retries with backoff, failures set aside.

Rules:

- A batch is one warehouse transaction per topic; it either lands whole or not at all.
- **Warehouse unavailable** (network, timeouts, a lock, tables or grants not ready): the
  events wait ``unavailable_retry_seconds`` and no attempt is charged; the worker pauses
  (circuit breaker) instead of insisting.
- **Data rejected**: the batch is split and each event is tried alone, so one bad record
  does not hold the rest. A rejected event is retried with exponential backoff and jitter;
  after ``max_attempts`` it is set aside as ``fallido`` until a person retries it.
- Writes are idempotent (upsert by id guarded by version), so a retry never duplicates.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.errors import InvalidInputError
from app.modules.dwh_publish.domain.entities import (
    FailedItem,
    OutboxItem,
    PublishResult,
    ReconcileResult,
    TopicStatus,
)
from app.modules.dwh_publish.domain.ports import (
    DwhRejected,
    DwhUnavailable,
    OutboxRepository,
    Topic,
    WarehouseWriter,
)

RECONCILE_CHUNK = 1000


@dataclass(frozen=True)
class PublishSettings:
    """How the publisher behaves."""

    enabled: bool = False
    batch_size: int = 1000
    lease_seconds: float = 300.0
    max_attempts: int = 12
    backoff_base_seconds: float = 30.0
    backoff_cap_seconds: float = 3600.0
    unavailable_retry_seconds: float = 120.0
    poll_seconds: float = 10.0
    breaker_failures: int = 3
    breaker_seconds: float = 300.0


@dataclass
class PublishState:
    """What the worker last did, for the status screen (one per process)."""

    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    consecutive_unavailable: int = 0
    paused_until: datetime | None = None
    last_reconcile_at: datetime | None = None
    last_reconcile: list[ReconcileResult] = field(default_factory=list)


@dataclass(frozen=True)
class PublishOverview:
    """The publication as a person sees it."""

    enabled: bool
    configured: bool
    state: PublishState
    topics: list[TopicStatus]


def backoff(attempts: int, base: float, cap: float, rand: Callable[[], float]) -> float:
    """Seconds before the next attempt: doubles each time, capped, with jitter (50-100 %)."""

    return min(cap, base * (2 ** max(attempts, 0))) * (0.5 + rand() / 2)


class DwhPublishService:
    """Publish, report and repair the outbox."""

    def __init__(
        self,
        repository: OutboxRepository,
        writer: WarehouseWriter,
        topics: Mapping[str, Topic],
        settings: PublishSettings,
        state: PublishState,
        configured: bool,
        clock: Callable[[], datetime] | None = None,
        rand: Callable[[], float] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (OutboxRepository): The outbox.
            writer (WarehouseWriter): Writer transactions on the warehouse.
            topics (Mapping[str, Topic]): Topic name -> how it lands in the warehouse.
            settings (PublishSettings): Batches, retries and pauses.
            state (PublishState): The worker's shared state.
            configured (bool): Whether the writer connection has a host and a role.
            clock (Callable[[], datetime] | None): Current instant, for tests.
            rand (Callable[[], float] | None): Random in [0, 1), for tests.
        """

        self.repository = repository
        self.writer = writer
        self.topics = dict(topics)
        self.settings = settings
        self.state = state
        self.configured = configured
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rand = rand or random.random

    # ------------------------------------------------------------ publishing

    def paused(self) -> bool:
        """Whether the circuit breaker holds the worker."""

        until = self.state.paused_until
        return until is not None and self._clock() < until

    async def publish_once(self) -> PublishResult:
        """Send one batch of due events.

        Returns:
            PublishResult: What happened; ``unavailable`` when the warehouse was not reachable.
        """

        now = self._clock()
        self.state.last_run_at = now
        items = await self.repository.claim(
            self.settings.batch_size, self.settings.lease_seconds, now
        )
        if not items:
            return PublishResult()
        published = retried = failed = 0
        groups: dict[str, list[OutboxItem]] = {}
        for item in items:
            groups.setdefault(item.topic, []).append(item)
        for position, (name, group) in enumerate(groups.items()):
            topic = self.topics.get(name)
            if topic is None:
                await self._reject(group, f"Tema sin publicador: {name}")
                failed += len(group)
                continue
            try:
                await self._write(topic, group)
            except DwhUnavailable as exc:
                rest = [item.id for _, items_ in list(groups.items())[position:] for item in items_]
                await self._unavailable(rest, str(exc))
                return PublishResult(
                    claimed=len(items),
                    published=published,
                    retried=retried,
                    failed=failed,
                    unavailable=True,
                )
            except DwhRejected:
                done, again, dead = await self._one_by_one(topic, group)
                published, retried, failed = published + done, retried + again, failed + dead
                continue
            await self.repository.mark_published([item.id for item in group], self._clock())
            published += len(group)
        self._healthy()
        return PublishResult(
            claimed=len(items), published=published, retried=retried, failed=failed
        )

    async def _write(self, topic: Topic, items: Sequence[OutboxItem]) -> None:
        async with self.writer.transaction() as connection:
            await topic.write(connection, items)

    async def _one_by_one(self, topic: Topic, items: Sequence[OutboxItem]) -> tuple[int, int, int]:
        published = retried = failed = 0
        for position, item in enumerate(items):
            try:
                await self._write(topic, [item])
            except DwhUnavailable as exc:
                await self._unavailable([other.id for other in items[position:]], str(exc))
                return published, retried, failed
            except DwhRejected as exc:
                if await self._reject([item], str(exc)):
                    failed += 1
                else:
                    retried += 1
                continue
            await self.repository.mark_published([item.id], self._clock())
            published += 1
        return published, retried, failed

    async def _reject(self, items: Sequence[OutboxItem], error: str) -> bool:
        """Charge an attempt; True when the events ran out of attempts."""

        self.state.last_error = error
        dead = [item.id for item in items if item.attempts + 1 >= self.settings.max_attempts]
        if dead:
            await self.repository.mark_failed(dead, error)
        for item in items:
            if item.id in dead:
                continue
            wait = backoff(
                item.attempts,
                self.settings.backoff_base_seconds,
                self.settings.backoff_cap_seconds,
                self._rand,
            )
            await self.repository.mark_retry(
                [item.id], error, self._clock() + timedelta(seconds=wait), charge=True
            )
        return bool(dead)

    async def _unavailable(self, ids: Sequence[int], error: str) -> None:
        self.state.last_error = error
        self.state.consecutive_unavailable += 1
        wait = timedelta(seconds=self.settings.unavailable_retry_seconds)
        await self.repository.mark_retry(ids, error, self._clock() + wait, charge=False)
        if self.state.consecutive_unavailable >= self.settings.breaker_failures:
            self.state.paused_until = self._clock() + timedelta(
                seconds=self.settings.breaker_seconds
            )

    def _healthy(self) -> None:
        self.state.consecutive_unavailable = 0
        self.state.paused_until = None
        self.state.last_success_at = self._clock()

    async def release(self, items: Sequence[OutboxItem]) -> None:
        """Give leases back (worker stopping)."""

        await self.repository.release([item.id for item in items])

    # -------------------------------------------------------------- repairing

    async def reconcile(self) -> list[ReconcileResult]:
        """Queue again every record the warehouse lacks or holds at an older version.

        Returns:
            list[ReconcileResult]: One per topic.

        Raises:
            DwhUnavailable: If the warehouse cannot be read.
        """

        results: list[ReconcileResult] = []
        for name, topic in self.topics.items():
            async with self.writer.transaction() as connection:
                remote = await topic.remote_versions(connection)
            local = await topic.local_versions()
            queued = await self.repository.pending_versions(name)
            behind = [
                entity_id
                for entity_id, version in local.items()
                if remote.get(entity_id, 0) < version and queued.get(entity_id, 0) < version
            ]
            sent = 0
            for start in range(0, len(behind), RECONCILE_CHUNK):
                events = await topic.snapshots(behind[start : start + RECONCILE_CHUNK])
                sent += await self.repository.add(events)
            results.append(ReconcileResult(name, len(local), len(remote), sent))
        self.state.last_reconcile_at = self._clock()
        self.state.last_reconcile = results
        return results

    # ---------------------------------------------- the screen (administrators)

    async def overview(self) -> PublishOverview:
        """State of the publication."""

        return PublishOverview(
            enabled=self.settings.enabled,
            configured=self.configured,
            state=self.state,
            topics=await self.repository.status(),
        )

    async def failed(self, limit: int = 100) -> list[FailedItem]:
        """Events out of attempts, newest first."""

        if not 1 <= limit <= 500:
            raise InvalidInputError("El límite debe estar entre 1 y 500")
        return await self.repository.failed(limit)

    async def retry(self, ids: Sequence[int] | None) -> int:
        """Put failed events back in the queue (all without ``ids``); lifts the breaker."""

        self.state.paused_until = None
        self.state.consecutive_unavailable = 0
        return await self.repository.retry_failed(list(ids) if ids else None, self._clock())

    async def reconcile_now(self) -> list[ReconcileResult]:
        """Run the reconciliation now."""

        if not self.configured:
            raise InvalidInputError("La conexión de escritura al DWH no está configurada")
        try:
            return await self.reconcile()
        except DwhUnavailable as exc:
            raise InvalidInputError(f"No se pudo leer el DWH: {exc}") from exc


__all__ = [
    "DwhPublishService",
    "PublishOverview",
    "PublishSettings",
    "PublishState",
    "backoff",
]
