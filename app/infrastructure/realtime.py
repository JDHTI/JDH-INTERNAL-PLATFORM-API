"""Live fan-out of JSON messages across API processes, backed by Redis.

Each process keeps one pattern subscription and hands every message to the
local subscribers of its topic, so a change made through any worker reaches
every open connection. Redis pub/sub does not replay: when a subscriber falls
behind or the subscription drops, it receives ``{"type": "sync.required"}``
and the client refetches.

Presence lives in one Redis hash per topic. Entries carry their own expiry,
so members of a process that died disappear once their heartbeat lapses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from app.core.logging import logger
from app.infrastructure.redis import RedisClient

SYNC_REQUIRED: dict[str, Any] = {"type": "sync.required"}

_RECONNECT_DELAYS = (0.5, 1, 2, 5)
_POLL_SECONDS = 1.0


class Subscription:
    """Messages of one topic for one local consumer."""

    def __init__(self, topic: str, queue_size: int) -> None:
        self.topic = topic
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)

    def deliver(self, message: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            # A slow consumer gets one resync marker instead of a partial history.
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(SYNC_REQUIRED)

    async def get(self) -> dict[str, Any]:
        """Wait for the next message.

        Returns:
            dict[str, Any]: The message as published.
        """

        return await self._queue.get()


class RealtimeBroker:
    """Publish, subscribe and track presence on named topics."""

    def __init__(self, redis: RedisClient, namespace: str = "realtime", queue_size: int = 256):
        """Create the broker; no network call happens until first use.

        Args:
            redis (RedisClient): Shared Redis client.
            namespace (str): Prefix of every channel and presence key.
            queue_size (int): Messages buffered per subscriber before it must resync.
        """

        self.redis = redis
        self.namespace = namespace
        self.queue_size = queue_size
        self._subscribers: dict[str, set[Subscription]] = {}
        self._reader: asyncio.Task[None] | None = None

    def _channel(self, topic: str) -> str:
        return self.redis.key(f"{self.namespace}:{topic}")

    def _presence_key(self, topic: str) -> str:
        return self.redis.key(f"{self.namespace}-presence:{topic}")

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Send a message to every subscriber of the topic in every process.

        Args:
            topic (str): Topic name, e.g. ``projects:area:<id>``.
            message (dict[str, Any]): JSON-serializable message.

        Returns:
            None
        """

        await self.redis.client.publish(self._channel(topic), json.dumps(message, default=str))

    @contextlib.asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[Subscription]:
        """Receive the topic's messages while the context is open.

        Args:
            topic (str): Topic name.

        Returns:
            AsyncIterator[Subscription]: The subscription, removed on exit.
        """

        subscription = Subscription(topic, self.queue_size)
        self._subscribers.setdefault(topic, set()).add(subscription)
        self._ensure_reader()
        try:
            yield subscription
        finally:
            listeners = self._subscribers.get(topic)
            if listeners is not None:
                listeners.discard(subscription)
                if not listeners:
                    self._subscribers.pop(topic, None)

    def _ensure_reader(self) -> None:
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_forever(), name="realtime-reader")

    def _dispatch(self, topic: str, message: dict[str, Any]) -> None:
        for subscription in tuple(self._subscribers.get(topic, ())):
            subscription.deliver(message)

    def _resync_everyone(self) -> None:
        for listeners in tuple(self._subscribers.values()):
            for subscription in tuple(listeners):
                subscription.deliver(SYNC_REQUIRED)

    async def _read_forever(self) -> None:
        prefix = self._channel("")
        attempt = 0
        while True:
            pubsub = self.redis.client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(f"{prefix}*")
                if attempt:
                    # Whatever was published while disconnected is lost.
                    self._resync_everyone()
                attempt = 0
                while True:
                    # An explicit timeout returns None on silence; listen() would
                    # fall back to the client's short socket timeout and raise.
                    raw = await pubsub.get_message(timeout=_POLL_SECONDS)
                    if raw is None or raw.get("type") != "pmessage":
                        continue
                    channel = raw["channel"]
                    try:
                        message = json.loads(raw["data"])
                    except (TypeError, ValueError):
                        logger.warning("Dropped malformed realtime message on %s", channel)
                        continue
                    self._dispatch(channel[len(prefix) :], message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the reader alive
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                attempt += 1
                logger.error("Realtime subscription lost (%s); retrying in %ss", exc, delay)
                await asyncio.sleep(delay)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    # --- Presence ---

    async def touch_presence(
        self, topic: str, member_id: str, data: dict[str, Any], ttl_seconds: int
    ) -> None:
        """Register or refresh a member of the topic.

        Args:
            topic (str): Topic name.
            member_id (str): One connection; a user may hold several.
            data (dict[str, Any]): What other members see about it.
            ttl_seconds (int): Seconds the entry survives without another touch.

        Returns:
            None
        """

        key = self._presence_key(topic)
        entry = json.dumps({**data, "expires_at": time.time() + ttl_seconds}, default=str)
        await self.redis.client.hset(key, member_id, entry)
        await self.redis.client.expire(key, ttl_seconds * 4)

    async def drop_presence(self, topic: str, member_id: str) -> None:
        """Remove a member of the topic.

        Args:
            topic (str): Topic name.
            member_id (str): The connection.

        Returns:
            None
        """

        await self.redis.client.hdel(self._presence_key(topic), member_id)

    async def list_presence(self, topic: str) -> list[dict[str, Any]]:
        """List the live members of the topic, pruning expired ones.

        Args:
            topic (str): Topic name.

        Returns:
            list[dict[str, Any]]: Member data, without the expiry stamp.
        """

        key = self._presence_key(topic)
        now = time.time()
        members, stale = [], []
        for member_id, raw in (await self.redis.client.hgetall(key)).items():
            try:
                entry = json.loads(raw)
            except (TypeError, ValueError):
                stale.append(member_id)
                continue
            if entry.pop("expires_at", 0) < now:
                stale.append(member_id)
            else:
                members.append(entry)
        if stale:
            await self.redis.client.hdel(key, *stale)
        return members

    async def close(self) -> None:
        """Stop the reader; open subscriptions simply stop receiving."""

        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None


__all__ = ["SYNC_REQUIRED", "RealtimeBroker", "Subscription"]
