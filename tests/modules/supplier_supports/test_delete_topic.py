"""The DWH delete of a purged support goes through the DWH team's function, never DELETE."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.modules.dwh_publish.domain.entities import OutboxItem
from app.modules.supplier_supports.infrastructure.publication import (
    DELETE_TOPIC,
    DeleteTopic,
    build_topics,
    delete_event,
)


class Connection:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return [{"filas_borradas": 1, "filas_historial": 1}]


async def test_each_delete_calls_the_function_with_its_version() -> None:
    support_id, when = uuid4(), datetime(2026, 11, 17, 15, tzinfo=UTC)
    event = delete_event(support_id, 4, "purga", when)
    connection = Connection()

    await DeleteTopic().write(
        connection, [OutboxItem(1, DELETE_TOPIC, support_id, 4, event.payload, 0)]
    )

    query, args = connection.calls[0]
    assert "plataforma.borrar_apoyo_proveedor" in query and "DELETE" not in query.upper()
    assert args == (support_id, 4, "purga", when)
    assert await DeleteTopic().remote_versions(connection) == {}


def test_the_delete_topic_is_published() -> None:
    supports, loads = SimpleNamespace(name="a"), SimpleNamespace(name="b")

    topics = build_topics(supports, loads, DeleteTopic())  # type: ignore[arg-type]

    assert set(topics) == {"a", "b", DELETE_TOPIC}
