"""Live channel of an area's projects: change events and who is looking.

One topic per area. Every write in ``ProjectService`` publishes here, and each
open WebSocket is a presence member that says which folder or project it is
viewing. Presence is per connection: the same person in two tabs shows twice,
and the client groups by ``user_id``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from app.core.logging import logger
from app.modules.identity.domain.principal import Principal
from app.modules.projects.domain.ports import LiveChannel, LiveSubscription


def area_topic(area_id: UUID) -> str:
    return f"projects:area:{area_id}"


def _uuid_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


class LiveSession:
    """One connection's membership in an area's live channel."""

    def __init__(
        self,
        live: ProjectLiveService,
        area_id: UUID,
        principal: Principal,
        subscription: LiveSubscription,
    ) -> None:
        self.live = live
        self.area_id = area_id
        self.principal = principal
        self.subscription = subscription
        self.connection_id = uuid.uuid4().hex
        self.folder_id: str | None = None
        self.project_id: str | None = None

    def member(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "user_id": str(self.principal.user_id),
            "email": self.principal.email,
            "display_name": self.principal.display_name,
            "folder_id": self.folder_id,
            "project_id": self.project_id,
        }

    async def next_message(self) -> dict[str, Any]:
        """Wait for the next event of the area.

        Returns:
            dict[str, Any]: A change, a presence update or ``sync.required``.
        """

        return await self.subscription.get()

    async def members(self) -> list[dict[str, Any]]:
        """Everyone connected to the area right now.

        Returns:
            list[dict[str, Any]]: Presence members.
        """

        return await self.live.channel.list_presence(area_topic(self.area_id))

    async def heartbeat(self) -> None:
        """Keep this connection listed as present.

        Returns:
            None
        """

        await self.live.channel.touch_presence(
            area_topic(self.area_id), self.connection_id, self.member(), self.live.presence_ttl
        )

    async def focus(self, folder_id: Any, project_id: Any) -> None:
        """Announce what this connection is viewing; malformed ids count as none.

        Args:
            folder_id (Any): Folder on screen, if any.
            project_id (Any): Project open, if any.

        Returns:
            None
        """

        folder, project = _uuid_or_none(folder_id), _uuid_or_none(project_id)
        if (folder, project) == (self.folder_id, self.project_id):
            await self.heartbeat()
            return
        self.folder_id, self.project_id = folder, project
        await self.heartbeat()
        await self.live.announce_presence(self.area_id)


class ProjectLiveService:
    """Publish project changes and run live sessions over a ``LiveChannel``."""

    def __init__(self, channel: LiveChannel, presence_ttl: int = 45) -> None:
        """Create the service.

        Args:
            channel (LiveChannel): Cross-process topics with presence.
            presence_ttl (int): Seconds a connection stays listed without a
                heartbeat; clients ping well inside it.
        """

        self.channel = channel
        self.presence_ttl = presence_ttl

    async def publish(self, area_id: UUID, event: dict[str, Any]) -> None:
        """Send a change event to everyone watching the area.

        Args:
            area_id (UUID): The area.
            event (dict[str, Any]): JSON-ready event.

        Returns:
            None
        """

        await self.channel.publish(area_topic(area_id), event)

    async def announce_presence(self, area_id: UUID) -> None:
        """Broadcast the area's current member list.

        Args:
            area_id (UUID): The area.

        Returns:
            None
        """

        topic = area_topic(area_id)
        members = await self.channel.list_presence(topic)
        await self.channel.publish(
            topic, {"type": "presence", "area_id": str(area_id), "members": members}
        )

    @asynccontextmanager
    async def join(self, area_id: UUID, principal: Principal) -> AsyncIterator[LiveSession]:
        """Subscribe to an area and appear in its presence until the context ends.

        The caller must already be authorized for the area.

        Args:
            area_id (UUID): The area.
            principal (Principal): The connected user.

        Returns:
            AsyncIterator[LiveSession]: The session, cleaned up on exit.
        """

        topic = area_topic(area_id)
        async with self.channel.subscribe(topic) as subscription:
            session = LiveSession(self, area_id, principal, subscription)
            await session.heartbeat()
            await self.announce_presence(area_id)
            try:
                yield session
            finally:
                try:
                    await self.channel.drop_presence(topic, session.connection_id)
                    await self.announce_presence(area_id)
                except Exception as exc:  # noqa: BLE001 - the entry expires on its own
                    logger.warning("Could not clear presence %s: %s", session.connection_id, exc)
