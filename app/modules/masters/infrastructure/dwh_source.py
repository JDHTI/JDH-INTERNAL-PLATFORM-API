"""Read any declared master straight from its ``consumo`` view in the DWH.

Every statement starts with ``/* plataforma:maestros:<slug> */`` so the DWH team can
tell the platform's queries apart in ``pg_stat_activity`` while it shares ``svc_bi``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.masters.domain.entities import Freshness, MasterSpec
from app.modules.masters.domain.ports import Row, WarehouseUnavailable

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIKE_SPECIAL = re.compile(r"([\\%_])")


def _quoted(name: str) -> str:
    return f'"{name}"'


def _like(term: str) -> str:
    escaped = _LIKE_SPECIAL.sub(r"\\\1", term)
    return f"%{escaped}%"


def _where(spec: MasterSpec, query: str | None, is_active: bool | None, args: list[Any]) -> str:
    conditions: list[str] = []
    if query:
        args.append(_like(query))
        searched = [column for column in (spec.code, spec.name) if column] or [spec.key]
        matches = [f"{_quoted(column)}::text ILIKE ${len(args)} ESCAPE '\\'" for column in searched]
        conditions.append(f"({' OR '.join(matches)})")
    if is_active is not None:
        if spec.active is None:
            # A master without an active flag has no active nor inactive records.
            conditions.append("FALSE")
        elif spec.active_is_boolean:
            conditions.append(f"{_quoted(spec.active)} IS {'TRUE' if is_active else 'FALSE'}")
        else:
            flag = "A" if is_active else "I"
            conditions.append(f"upper(btrim({_quoted(spec.active)}::text)) = '{flag}'")
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


class WarehouseMasterReader:
    """Count, page and download a master's view; read the state of the sequences.

    One instance serves the whole API: it caps how many master queries run at once on
    the DWH (two cores, shared with BI) and remembers the sequences' state briefly.
    """

    def __init__(
        self,
        connection: DataProviderConnection,
        query_timeout: float = 15.0,
        max_concurrent: int = 3,
        freshness_seconds: float = 30.0,
        monitoring_schema: str = "monitoreo",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the reader.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            query_timeout (float): Seconds for a count or a page.
            max_concurrent (int): Master queries running at once on the DWH.
            freshness_seconds (float): How long the sequences' state is reused.
            monitoring_schema (str): Schema of ``secuencias``.
            clock (Callable[[], float]): Monotonic seconds, for tests.

        Raises:
            ValueError: If the monitoring schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(monitoring_schema):
            raise ValueError("Invalid monitoring schema")
        self.connection = connection
        self.query_timeout = query_timeout
        self.freshness_seconds = freshness_seconds
        self.monitoring_schema = monitoring_schema
        self.clock = clock
        self._slots = asyncio.Semaphore(max(1, max_concurrent))
        self._freshness: dict[str, tuple[float, Freshness]] = {}

    async def _fetch(
        self, slug: str, sql: str, args: Sequence[Any], timeout: float
    ) -> Sequence[Any]:
        statement = f"/* plataforma:maestros:{slug} */ {sql}"
        async with self._slots:
            try:
                return await self.connection.fetch(statement, *args, timeout=timeout)
            except DataProviderUnavailable as exc:
                cause = exc.__cause__
                raise WarehouseUnavailable(f"{exc}: {cause}" if cause else str(exc)) from exc

    @staticmethod
    def _relation(spec: MasterSpec) -> str:
        return f"{_quoted(spec.schema)}.{_quoted(spec.relation)}"

    async def count(self, spec: MasterSpec, query: str | None, is_active: bool | None) -> int:
        """Rows matching the filters."""

        args: list[Any] = []
        sql = f"SELECT count(*) AS total FROM {self._relation(spec)}" + _where(
            spec, query, is_active, args
        )
        rows = await self._fetch(spec.slug, sql, args, self.query_timeout)
        return int(rows[0]["total"]) if rows else 0

    async def page(
        self,
        spec: MasterSpec,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> list[Row]:
        """Rows matching the filters, ordered by code, name and key."""

        args: list[Any] = []
        where = _where(spec, query, is_active, args)
        order = [f"{_quoted(c)} ASC NULLS LAST" for c in (spec.code, spec.name) if c]
        order.append(f"{_quoted(spec.key)} ASC")
        args.extend((limit, offset))
        sql = (
            f"SELECT {', '.join(_quoted(c) for c in spec.columns)} FROM {self._relation(spec)}"
            f"{where} ORDER BY {', '.join(order)} LIMIT ${len(args) - 1} OFFSET ${len(args)}"
        )
        rows = await self._fetch(spec.slug, sql, args, self.query_timeout)
        return [dict(row) for row in rows]

    async def freshness(
        self, sequences: Sequence[str], refresh: bool = False
    ) -> dict[str, Freshness]:
        """The state of these sequences; what is remembered when monitoring fails."""

        wanted = sorted(set(sequences))
        now = self.clock()
        remembered: dict[str, Freshness] = {}
        if not refresh:
            for name in wanted:
                stored = self._freshness.get(name)
                if stored is not None and now - stored[0] < self.freshness_seconds:
                    remembered[name] = stored[1]
        missing = [name for name in wanted if name not in remembered]
        if not missing:
            return remembered
        sql = (
            'SELECT "secuencia", "ultima_corrida_id", "ultima_datos_listos", "en_curso" '
            f'FROM {_quoted(self.monitoring_schema)}."secuencias" '
            'WHERE "secuencia" = ANY($1::text[])'
        )
        try:
            rows = await self._fetch("monitoreo", sql, [missing], self.query_timeout)
        except WarehouseUnavailable as exc:
            logger.warning("Masters: DWH sequences unreadable: %s", exc)
            return {name: self._freshness[name][1] for name in wanted if name in self._freshness}
        for row in rows:
            item = Freshness(
                sequence=row["secuencia"],
                corrida_id=row["ultima_corrida_id"],
                data_ready_at=row["ultima_datos_listos"],
                reloading=bool(row["en_curso"]),
            )
            self._freshness[item.sequence] = (now, item)
            remembered[item.sequence] = item
        return remembered
