"""In-memory ports for the data warehouse monitoring module."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.dwh_monitoring.domain.entities import LoadCycle
from app.modules.dwh_monitoring.domain.views import ViewSpec
from app.modules.identity.domain.principal import Principal

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)  # 10:00 in Bogota


def person(email: str = "ana@jdh.com.co", admin: bool = False) -> Principal:
    return Principal(user_id=uuid4(), email=email, display_name=email, is_platform_admin=admin)


TI_MEMBER = person("analista.ti@jdh.com.co")
OUTSIDER = person("ventas@jdh.com.co")


class FakeAccess:
    """TI members (and admins) manage the module."""

    def __init__(self, members: Sequence[str] = (TI_MEMBER.email,)) -> None:
        self.members = set(members)

    async def is_manager(self, principal: Principal) -> bool:
        return principal.is_platform_admin or principal.email in self.members


class FakeSource:
    """The warehouse views, as rows keyed by contract column name."""

    def __init__(self) -> None:
        self.views: dict[str, list[dict[str, Any]]] = {}
        self.down = False
        self.failing: set[str] = set()
        self.since: dict[str, datetime] = {}
        self.keys_read: dict[str, list[Any]] = {}

    async def check(self) -> None:
        if self.down:
            raise DataProviderUnavailable("Unable to connect to data provider")

    def _rows(self, view: ViewSpec) -> list[dict[str, Any]]:
        if view.name in self.failing:
            raise DataProviderUnavailable("Data provider query failed")
        return [
            {c: row.get(c) for c in view.source_columns} for row in self.views.get(view.name, [])
        ]

    async def read_all(self, view: ViewSpec) -> list[dict[str, Any]]:
        return self._rows(view)

    async def read_since(
        self, view: ViewSpec, since: datetime, batch_size: int
    ) -> AsyncIterator[list[dict[str, Any]]]:
        self.since[view.name] = since
        rows = sorted(
            (row for row in self._rows(view) if row["inicio"] >= since),
            key=lambda row: (row["inicio"], str(row[view.key[0]])),
        )
        for start in range(0, len(rows), batch_size):
            yield rows[start : start + batch_size]

    async def read_keys(self, view: ViewSpec, keys: Sequence[Any]) -> list[dict[str, Any]]:
        self.keys_read.setdefault(view.name, []).extend(keys)
        (key,) = view.key
        return [row for row in self._rows(view) if row[key] in set(keys)]


class FakeCopyRepository:
    """The local copy: one dict of rows per view, keyed by the view key."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple, dict[str, Any]]] = {}
        self.cycles: dict[UUID, LoadCycle] = {}

    def _table(self, view: ViewSpec) -> dict[tuple, dict[str, Any]]:
        return self.tables.setdefault(view.name, {})

    @staticmethod
    def _key(view: ViewSpec, row: dict[str, Any]) -> tuple:
        return tuple(row[name] for name in view.key)

    async def replace(self, view, rows, loaded_at) -> int:
        self.tables[view.name] = {
            self._key(view, row): {**row, "cargado_en": loaded_at} for row in rows
        }
        return len(rows)

    async def append(self, view, rows, loaded_at) -> int:
        table = self._table(view)
        added = 0
        for row in rows:
            key = self._key(view, row)
            if key not in table:
                table[key] = {**row, "cargado_en": loaded_at}
                added += 1
        return added

    async def upsert(self, view, rows, loaded_at) -> int:
        table = self._table(view)
        for row in rows:
            table[self._key(view, row)] = {**row, "cargado_en": loaded_at}
        return len(rows)

    async def latest_start(self, view) -> datetime | None:
        starts = [row["inicio"] for row in self._table(view).values()]
        return max(starts) if starts else None

    async def open_keys(self, view, loaded_before) -> list[Any]:
        return [
            row[view.key[0]]
            for row in self._table(view).values()
            if (row.get("estado") in ("running", "abandonada") or row.get("fin") is None)
            and row["cargado_en"] < loaded_before
        ]

    async def save_cycle(self, cycle: LoadCycle) -> None:
        self.cycles[cycle.id] = cycle

    async def last_cycle(self) -> LoadCycle | None:
        return max(self.cycles.values(), key=lambda c: c.started_at, default=None)


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def copy() -> FakeCopyRepository:
    return FakeCopyRepository()
