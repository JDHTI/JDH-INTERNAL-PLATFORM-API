"""The metadata-driven sync of ``master_records`` and its warehouse source."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.core.errors import InvalidInputError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.masters.application.mapping import active_flag, company_id, record_row
from app.modules.masters.application.sync import MasterCatalogSync
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterField,
    MasterSummary,
    build_spec,
)
from app.modules.masters.infrastructure.dwh_source import WarehouseCatalogSource

NOW = datetime(2026, 11, 5, 8, tzinfo=UTC)


def _field(name: str, role: str = "attribute") -> MasterField:
    return MasterField(name, name, "text", role, True, False, None)


def _master(slug: str, source: str = "consumo.clientes", fields=None) -> MasterDetail:
    summary = MasterSummary(uuid4(), slug, slug, None, "siesa", source, None, [], 0, None)
    return MasterDetail(
        summary,
        fields
        or [
            _field("cliente_key", "key"),
            _field("cia", "company"),
            _field("nit", "code"),
            _field("razon_social", "name"),
            _field("estado", "active"),
            _field("ciudad"),
            _field("cupo"),
            _field("creado"),
        ],
    )


def test_the_spec_checks_identifiers_and_the_key() -> None:
    spec = build_spec(_master("clientes"))
    assert (spec.schema, spec.relation, spec.key) == ("consumo", "clientes", "cliente_key")
    assert spec.attribute_columns == ("ciudad", "cupo", "creado")

    with pytest.raises(ValueError, match="origen"):
        build_spec(_master("x", source="consumo.clientes; drop table users"))
    with pytest.raises(ValueError, match="identificador"):
        build_spec(_master("x", fields=[_field("cliente_key", "key"), _field("a b")]))
    with pytest.raises(ValueError, match="clave"):
        build_spec(_master("x", fields=[_field("nit", "code")]))


def test_rows_map_roles_and_make_attributes_json_safe() -> None:
    spec = build_spec(_master("clientes"))
    row = {
        "cliente_key": " 1|900|001 ",
        "cia": 1,
        "nit": "900123",
        "razon_social": "  Acme   SAS ",
        "estado": "I",
        "ciudad": "Cali",
        "cupo": Decimal("1500.50"),
        "creado": date(2026, 1, 2),
    }

    record = record_row(spec, row)

    assert record == {
        "source_key": "1|900|001",
        "company_id": 1,
        "code": "900123",
        "name": "Acme SAS",
        "is_active": False,
        "attributes": {"ciudad": "Cali", "cupo": 1500.5, "creado": "2026-01-02"},
    }
    assert record_row(spec, {**row, "cliente_key": "  "}) is None


def test_active_flags_and_company_codes() -> None:
    assert [active_flag(v) for v in ("A", "i", True, False, None, "X")] == [
        True,
        False,
        True,
        False,
        None,
        None,
    ]
    assert company_id(1) == 1 and company_id(-1) == -1
    assert company_id(70000) is None and company_id("x") is None


class FakeSource:
    def __init__(self, pages: dict[str, list[list[dict]]], failing: set[str] = frozenset()):
        self.pages = pages
        self.failing = failing

    async def records(self, spec, batch_size):
        if spec.slug in self.failing:
            raise DataProviderUnavailable("Data provider query failed") from RuntimeError(
                "permission denied for schema mart_maestro"
            )
        for page in self.pages.get(spec.slug, []):
            yield page


class FakeRepository:
    def __init__(self, definitions: list[MasterDetail]) -> None:
        self.definitions = definitions
        self.upserts: list[tuple[UUID, int]] = []
        self.deactivated: list[UUID] = []
        self.touched: list[UUID] = []

    async def list_definitions(self):
        return self.definitions

    async def upsert_records(self, master_id, rows, synced_at):
        self.upserts.append((master_id, len(rows)))
        return len(rows)

    async def deactivate_unseen(self, master_id, synced_at):
        self.deactivated.append(master_id)
        return 2

    async def touch_definition(self, master_id, synced_at):
        self.touched.append(master_id)


def _row(key: str) -> dict:
    return {"cliente_key": key, "cia": 1, "nit": key, "razon_social": key, "estado": "A"}


def _sync(source, repository) -> MasterCatalogSync:
    return MasterCatalogSync(source, repository, batch_size=2, clock=lambda: NOW)


async def test_pages_are_upserted_then_unseen_rows_deactivated() -> None:
    clientes = _master("clientes")
    repository = FakeRepository([clientes])
    source = FakeSource({"clientes": [[_row("a"), _row("b")], [_row("c"), {"cliente_key": None}]]})

    (report,) = await _sync(source, repository).run()

    assert (report.read, report.upserted, report.deactivated) == (4, 3, 2)
    assert repository.upserts == [(clientes.summary.id, 2), (clientes.summary.id, 1)]
    assert repository.touched == [clientes.summary.id] and report.error is None


async def test_an_empty_read_never_deactivates_nor_touches() -> None:
    repository = FakeRepository([_master("clientes")])

    (report,) = await _sync(FakeSource({}), repository).run()

    assert report.read == 0 and repository.deactivated == [] and repository.touched == []


async def test_a_failing_master_is_reported_and_the_others_go_on() -> None:
    terceros = _master("terceros", source="mart_maestro.dim_tercero")
    clientes = _master("clientes")
    repository = FakeRepository([terceros, clientes])
    source = FakeSource({"clientes": [[_row("a")]]}, failing={"terceros"})

    reports = await _sync(source, repository).run()

    assert [r.slug for r in reports] == ["terceros", "clientes"]
    assert "permission denied" in reports[0].error
    assert reports[1].error is None and reports[1].upserted == 1


async def test_dry_run_and_unknown_masters() -> None:
    repository = FakeRepository([_master("clientes")])
    source = FakeSource({"clientes": [[_row("a")]]})

    (report,) = await _sync(source, repository).run(apply=False)
    assert report.upserted == 1 and repository.upserts == [] and repository.touched == []

    with pytest.raises(InvalidInputError, match="productos"):
        await _sync(source, repository).run(only=["productos"])


class FakeConnection:
    def __init__(self, pages: list[list[dict]], missing: int = 0) -> None:
        self.pages = pages
        self.missing = missing
        self.calls: list[tuple] = []

    async def fetch(self, query, *args, timeout=None):
        self.calls.append((query, args, timeout))
        if self.missing:
            self.missing -= 1
            raise DataProviderUnavailable("Data provider query failed") from (
                asyncpg.UndefinedTableError('relation "consumo.clientes" does not exist')
            )
        return self.pages.pop(0) if self.pages else []


async def test_the_source_pages_by_key_with_quoted_identifiers() -> None:
    connection = FakeConnection([[_row("a"), _row("b")], [_row("c")]])
    source = WarehouseCatalogSource(connection, timeout=99)
    spec = build_spec(_master("clientes"))

    pages = [page async for page in source.records(spec, 2)]

    assert [len(page) for page in pages] == [2, 1]
    first, second = connection.calls
    assert 'FROM "consumo"."clientes" ORDER BY "cliente_key"' in first[0]
    assert first[1] == (2,) and first[2] == 99
    assert 'WHERE "cliente_key" > $1' in second[0] and second[1] == ("b", 2)


async def test_a_missing_view_is_retried_then_raised() -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    spec = build_spec(_master("clientes"))
    recovered = WarehouseCatalogSource(
        FakeConnection([[_row("a")]], missing=2), retries=3, retry_wait=5, sleep=sleep
    )
    assert [len(p) async for p in recovered.records(spec, 10)] == [1]
    assert waits == [5, 5]

    gone = WarehouseCatalogSource(FakeConnection([], missing=9), retries=1, sleep=sleep)
    with pytest.raises(DataProviderUnavailable):
        _ = [p async for p in gone.records(spec, 10)]
