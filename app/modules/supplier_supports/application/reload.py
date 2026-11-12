"""Reload the supports from the supplier files, matching each row to the stored support by key.

The suppliers send their tables again (``Apoyos Proveedor - X.xlsx``, one per supplier) with
rows added, changed and gone. Each row is read with the workbook adapters (the per-supplier
layout and quirks) and, unlike a manual bulk load, never blocks the rest: an unknown NIT or SKU
leaves that support ``pendiente_revision`` with its issue, as the original import did.

Then, by key (:func:`~app.modules.supplier_supports.domain.bulk.support_key`):

- a new key creates the support;
- a known key updates it with the fields the table carries (an approved JDH contribution is
  never replaced), or leaves it as it is;
- a stored support of the same table that the file no longer brings is reported missing and,
  with ``expire_missing``, closed the day before.

Each file is one bulk load (``supplier_support_loads``): all of its tables are written in one
transaction, every touched support points at the load and is published to the DWH.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.supplier_supports.application.service import (
    PrefetchedMasters,
    SupplierSupportService,
)
from app.modules.supplier_supports.domain.bulk import merge, same_support, support_key
from app.modules.supplier_supports.domain.entities import (
    Issue,
    LoadPlan,
    LoadResult,
    Support,
    SupportData,
)

RELOAD_ACTOR = "importacion"
_DEFAULTS = {item.name: item.default for item in fields(SupportData)}
# The files' cost and list price are lookups of their own product sheet: the platform takes
# the cost from the DWH, so they only seed a new support's reference, never update one.
REFERENCE_FIELDS = frozenset({"reference_cost", "reference_list_price"})


@dataclass(frozen=True)
class ReloadRow:
    """One row of a supplier table, as the workbook adapter read it."""

    data: SupportData
    supplier_code: str | None
    # The fields the table carries (its columns, fixed values and hooks).
    provided: frozenset[str]


@dataclass(frozen=True)
class ReloadTable:
    """One Excel table of a supplier file."""

    label: str  # "sheet/table", the ``source`` of its supports
    rows: tuple[ReloadRow, ...]


@dataclass
class TableReport:
    """What a table does to the master."""

    label: str
    rows: int = 0
    create: int = 0
    update: int = 0
    unchanged: int = 0
    missing: int = 0
    expire: int = 0
    flagged: int = 0
    repeated: int = 0
    issues: list[tuple[str | None, str | None, Issue]] = field(default_factory=list)
    # How many updates change each field (to tell real changes from noise).
    changed_fields: Counter[str] = field(default_factory=Counter)


@dataclass
class FileReport:
    """What a supplier file does, table by table; ``load_id`` once applied."""

    file: str
    tables: list[TableReport]
    plan: LoadPlan | None = None
    result: LoadResult | None = None


def provided_fields(data: SupportData, mapped: Sequence[str]) -> frozenset[str]:
    """The mapped columns plus every field the adapter set to something not default."""

    given = {name for name in mapped}
    given.update(name for name, default in _DEFAULTS.items() if getattr(data, name) != default)
    return frozenset(given - {"source", "source_key", "issues", "status"})


class SupportReloadService:
    """Plan and write the reload of supplier files."""

    def __init__(
        self,
        supports: SupplierSupportService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            supports (SupplierSupportService): Resolves masters and reads/writes supports.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.supports = supports
        self._clock = clock or (lambda: datetime.now(UTC))

    async def plan(
        self, path: Path, tables: Sequence[ReloadTable], *, expire_missing: bool
    ) -> FileReport:
        """Decide what one supplier file creates, updates, leaves and closes.

        Nothing is written.
        """

        rows = [row for table in tables for row in table.rows]
        masters = PrefetchedMasters(self.supports.masters)
        await masters.load(
            {code.strip() for row in rows if (code := row.supplier_code) and code.strip()},
            {sku.strip() for row in rows if (sku := row.data.product_sku) and sku.strip()},
            {nit for row in rows if (nit := clean_nit(row.data.customer_nit))},
        )
        resolved: list[tuple[str, ReloadRow, SupportData]] = []
        for table in tables:
            for row in table.rows:
                resolved.append((table.label, row, await self._resolve(row, masters)))

        supplier_ids = {data.commercial_supplier_id for _, _, data in resolved} - {None}
        stored = await self.supports.repository.live_for_suppliers(sorted(supplier_ids, key=str))
        by_key: dict[tuple[Any, ...], Support] = {}
        for support in sorted(stored, key=lambda item: (item.version, item.updated_at)):
            by_key[support_key(support.data)] = support

        reports = {table.label: TableReport(table.label) for table in tables}
        creates: list[tuple[UUID, SupportData]] = []
        updates: list[tuple[UUID, int, SupportData]] = []
        seen_keys: set[tuple[Any, ...]] = set()
        matched: set[UUID] = set()
        for label, row, data in resolved:
            report = reports[label]
            report.rows += 1
            for issue in data.issues:
                report.issues.append((data.customer_nit, data.product_sku, issue))
            if data.issues:
                report.flagged += 1
            key = support_key(data)
            if key in seen_keys:
                # Same key twice in the file: the first one wins, the rest are reported.
                report.repeated += 1
                report.issues.append(
                    (data.customer_nit, data.product_sku, Issue("duplicado", "misma llave"))
                )
                continue
            seen_keys.add(key)
            match = by_key.get(key)
            if match is None:
                creates.append((uuid4(), data))
                report.create += 1
                continue
            matched.add(match.id)
            merged = merge(match.data, data, row.provided - REFERENCE_FIELDS)
            if same_support(merged, match.data):
                report.unchanged += 1
                continue
            updates.append((match.id, match.version, merged))
            report.update += 1
            report.changed_fields.update(
                name
                for name in _DEFAULTS
                if name not in ("source", "source_key")
                and getattr(merged, name) != getattr(match.data, name)
            )

        today = self.supports.calendar.today()
        last_day = today - timedelta(days=1)
        expirations: list[tuple[UUID, int, date]] = []
        missing = 0
        for support in stored:
            report = reports.get(support.data.source or "")
            if report is None or support.id in matched:
                continue
            if support.data.valid_to is not None and support.data.valid_to < today:
                continue
            report.missing += 1
            missing += 1
            started = support.data.valid_from is None or support.data.valid_from <= last_day
            if expire_missing and started:
                expirations.append((support.id, support.version, last_day))
                report.expire += 1

        content = path.read_bytes()
        plan = LoadPlan(
            load_id=uuid4(),
            file_name=path.name,
            file_sha256=hashlib.sha256(content).hexdigest(),
            supplier_code=_single({row.supplier_code for row in rows}),
            sheet=", ".join(table.label for table in tables)[:300],
            rows_read=len(rows),
            creates=tuple(creates),
            updates=tuple(updates),
            expirations=tuple(expirations),
            unchanged=sum(report.unchanged for report in reports.values()),
            missing=missing,
        )
        return FileReport(path.name, list(reports.values()), plan)

    async def apply(self, report: FileReport) -> FileReport:
        """Write a planned file in one transaction (nothing when it changes nothing)."""

        plan = report.plan
        if plan is None or not (plan.creates or plan.updates or plan.expirations):
            return report
        report.result = await self.supports.repository.apply_load(plan, RELOAD_ACTOR, self._clock())
        return report

    async def _resolve(self, row: ReloadRow, masters: PrefetchedMasters) -> SupportData:
        """Bind a row to the masters without failing: problems become issues."""

        service = self.supports
        prepared = await service._resolve(
            service._normalize(row.data),
            supplier_code=row.supplier_code,
            strict=False,
            masters=masters,
        )
        issues = list(prepared.issues)
        if prepared.commercial_supplier_id is None and prepared.kind != "excepcion_margen":
            if not any(issue.code == "proveedor_sin_cruce" for issue in issues):
                issues.append(Issue("proveedor_sin_cruce", row.supplier_code, "supplier"))
            prepared = replace(prepared, kind="excepcion_margen")
        return replace(
            prepared,
            issues=tuple(issues),
            status="pendiente_revision" if issues else "activo",
            source_key=None,
        )


def _single(values: set[str | None]) -> str | None:
    found = {value for value in values if value}
    return found.pop() if len(found) == 1 else None


__all__ = [
    "FileReport",
    "ReloadRow",
    "ReloadTable",
    "SupportReloadService",
    "TableReport",
    "provided_fields",
]
