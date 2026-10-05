import asyncio
import json
import time
import uuid
from datetime import date

from app.infrastructure.realtime import SYNC_REQUIRED, RealtimeBroker
from app.modules.projects.application.dto import CreateProjectCommand, FolderCommand
from app.modules.projects.application.live import area_topic

# --- Live service over the in-memory channel ---


async def test_join_announces_presence_and_leaving_clears_it(live, channel, ti) -> None:
    area, ana, luis, _ = ti
    topic = area_topic(area.id)

    async with live.join(area.id, ana) as first:
        async with live.join(area.id, luis) as second:
            members = await second.members()
            assert {m["email"] for m in members} == {ana.email, luis.email}
            # Ana's subscription sees Luis arrive.
            await first.next_message()  # her own presence announcement
            arrival = await first.next_message()
            assert arrival["type"] == "presence"
            assert len(arrival["members"]) == 2
        assert [m["email"] for m in await channel.list_presence(topic)] == [ana.email]
    assert await channel.list_presence(topic) == []


async def test_focus_updates_presence_once_per_change(live, channel, ti) -> None:
    area, ana, *_ = ti
    folder_id = uuid.uuid4()

    async with live.join(area.id, ana) as session:
        before = len(channel.events("presence"))
        await session.focus(str(folder_id), None)
        await session.focus(str(folder_id), None)
        await session.focus("not-a-uuid", "../../etc")

        announcements = channel.events("presence")[before:]
        assert len(announcements) == 2
        assert announcements[0]["members"][0]["folder_id"] == str(folder_id)
        assert announcements[1]["members"][0]["folder_id"] is None


async def test_service_writes_reach_live_sessions(projects, live, ti) -> None:
    area, ana, luis, _ = ti

    async with live.join(area.id, luis) as session:
        view = await projects.create_folder("ti", FolderCommand(name="Redes"), ana)
        await projects.create_project(
            "ti",
            CreateProjectCommand(folder_id=view.folder.id, name="VPN", start_date=date.today()),
            ana,
        )
        received = [await session.next_message() for _ in range(3)]

    assert [m["type"] for m in received] == ["presence", "folder.created", "project.created"]
    assert received[2]["data"]["name"] == "VPN"


# --- Redis broker against a minimal fake client ---


class FakePubSub:
    def __init__(self, redis) -> None:
        self.redis = redis
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pattern = None

    async def psubscribe(self, pattern):
        self.pattern = pattern.rstrip("*")
        self.redis.pubsubs.append(self)

    async def get_message(self, timeout=None):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except TimeoutError:
            return None

    async def aclose(self):
        if self in self.redis.pubsubs:
            self.redis.pubsubs.remove(self)


class FakeRedis:
    def __init__(self) -> None:
        self.pubsubs: list[FakePubSub] = []
        self.hashes: dict[str, dict[str, str]] = {}

    def pubsub(self, ignore_subscribe_messages=True):
        return FakePubSub(self)

    async def publish(self, channel, data):
        for pubsub in self.pubsubs:
            if channel.startswith(pubsub.pattern):
                pubsub.queue.put_nowait({"type": "pmessage", "channel": channel, "data": data})

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key, seconds):
        return True

    async def hdel(self, key, *fields):
        for field in fields:
            self.hashes.get(key, {}).pop(field, None)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


class FakeRedisClient:
    def __init__(self) -> None:
        self.client = FakeRedis()

    def key(self, value):
        return f"test:{value}"


async def _until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_broker_routes_messages_to_topic_subscribers() -> None:
    redis = FakeRedisClient()
    broker = RealtimeBroker(redis)
    try:
        async with (
            broker.subscribe("projects:area:1") as one,
            broker.subscribe("projects:area:2") as two,
        ):
            await _until(lambda: redis.client.pubsubs)
            await broker.publish("projects:area:1", {"type": "project.created", "n": 1})
            await broker.publish("projects:area:2", {"type": "project.created", "n": 2})

            assert (await asyncio.wait_for(one.get(), 1))["n"] == 1
            assert (await asyncio.wait_for(two.get(), 1))["n"] == 2
    finally:
        await broker.close()


async def test_slow_subscriber_gets_a_single_resync() -> None:
    redis = FakeRedisClient()
    broker = RealtimeBroker(redis, queue_size=2)
    try:
        async with broker.subscribe("t") as subscription:
            for n in range(5):
                subscription.deliver({"n": n})
            assert await subscription.get() == SYNC_REQUIRED
    finally:
        await broker.close()


async def test_presence_drops_expired_entries() -> None:
    redis = FakeRedisClient()
    broker = RealtimeBroker(redis)
    await broker.touch_presence("t", "alive", {"email": "ana@jdh.com.co"}, ttl_seconds=30)
    key = redis.key("realtime-presence:t")
    redis.client.hashes[key]["dead"] = json.dumps({"email": "x", "expires_at": time.time() - 1})

    assert await broker.list_presence("t") == [{"email": "ana@jdh.com.co"}]
    assert "dead" not in redis.client.hashes[key]
