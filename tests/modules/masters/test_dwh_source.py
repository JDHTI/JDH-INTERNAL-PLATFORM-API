"""The live reads of a master's ``consumo`` view and the cache of what was read."""

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.masters.domain.entities import MasterSpec
from app.modules.masters.domain.ports import WarehouseUnavailable
from app.modules.masters.infrastructure.cache import MasterReadCache
from app.modules.masters.infrastructure.dwh_source import WarehouseMasterReader

SPEC = MasterSpec(
    master_id=uuid4(),
    slug="clientes",
    schema="consumo",
    relation="clientes",
    key="cliente_key",
    company="cia",
    code="nit",
    name="razon_social",
    active="estado",
    columns=("cliente_key", "cia", "nit", "razon_social", "estado", "ciudad"),
)


class FakeConnection:
    def __init__(self, answers=None, fail=False) -> None:
        self.answers = list(answers or [])
        self.fail = fail
        self.queries: list[tuple[str, tuple, float | None]] = []

    async def fetch(self, query, *args, timeout=None):
        self.queries.append((query, args, timeout))
        if self.fail:
            raise DataProviderUnavailable("Data provider query failed") from (
                asyncpg.UndefinedTableError('relation "consumo.clientes" does not exist')
            )
        return self.answers.pop(0) if self.answers else []


async def test_a_page_searches_code_and_name_and_tags_the_query() -> None:
    connection = FakeConnection([[{"cliente_key": "1", "nit": "900"}]])
    reader = WarehouseMasterReader(connection, query_timeout=7)

    rows = await reader.page(SPEC, "50%_off\\", False, 100, 25)

    ((sql, args, timeout),) = connection.queries
    assert sql.startswith("/* plataforma:maestros:clientes */ SELECT ")
    assert 'FROM "consumo"."clientes"' in sql
    assert '"nit"::text ILIKE $1 ESCAPE \'\\\' OR "razon_social"::text ILIKE $1' in sql
    assert "upper(btrim(\"estado\"::text)) = 'I'" in sql
    assert sql.endswith(
        'ORDER BY "nit" ASC NULLS LAST, "razon_social" ASC NULLS LAST, "cliente_key" ASC '
        "LIMIT $2 OFFSET $3"
    )
    assert args == ("%50\\%\\_off\\\\%", 25, 100)
    assert timeout == 7 and rows == [{"cliente_key": "1", "nit": "900"}]


async def test_counting_with_a_boolean_flag_or_without_one() -> None:
    connection = FakeConnection([[{"total": 4}], [{"total": 0}]])
    reader = WarehouseMasterReader(connection)
    boolean = MasterSpec(**{**SPEC.__dict__, "active_is_boolean": True})
    no_flag = MasterSpec(**{**SPEC.__dict__, "active": None})

    assert await reader.count(boolean, None, True) == 4
    assert await reader.count(no_flag, None, True) == 0

    assert connection.queries[0][0].endswith('WHERE "estado" IS TRUE')
    assert connection.queries[1][0].endswith("WHERE FALSE")


async def test_a_failure_is_reported_with_its_cause() -> None:
    reader = WarehouseMasterReader(FakeConnection(fail=True))

    with pytest.raises(WarehouseUnavailable, match="does not exist"):
        await reader.count(SPEC, None, None)


async def test_the_sequences_are_remembered_briefly() -> None:
    run = uuid4()
    ready = datetime(2026, 11, 5, 8, 42, tzinfo=UTC)
    row = {
        "secuencia": "identidad",
        "ultima_corrida_id": run,
        "ultima_datos_listos": ready,
        "en_curso": True,
    }
    connection = FakeConnection([[row], [row]])
    now = [0.0]
    reader = WarehouseMasterReader(connection, freshness_seconds=30, clock=lambda: now[0])

    state = (await reader.freshness(["identidad"]))["identidad"]
    assert (state.corrida_id, state.data_ready_at, state.reloading) == (run, ready, True)
    now[0] = 29
    await reader.freshness(["identidad"])
    assert len(connection.queries) == 1
    await reader.freshness(["identidad"], refresh=True)
    assert len(connection.queries) == 2
    assert connection.queries[0][1] == (["identidad"],)

    # Monitoring unreadable: the last known state, even if old; nothing when unknown.
    connection.fail = True
    now[0] = 500
    assert (await reader.freshness(["identidad"]))["identidad"].corrida_id == run
    assert await reader.freshness(["reporte_ventas"]) == {}


def test_the_cache_keeps_the_most_recent_entries() -> None:
    cache = MasterReadCache(max_entries=2)
    run = uuid4()

    cache.put("a", run, 1)
    cache.put("b", run, 2)
    assert cache.get("a").value == 1  # "a" is now the most recent
    cache.put("c", None, 3)

    assert cache.get("b") is None
    assert (cache.get("a").version, cache.get("c").value) == (run, 3)
    assert len(cache) == 2
