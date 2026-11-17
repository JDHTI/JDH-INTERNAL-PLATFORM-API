"""Bulk load of supports from an Excel or CSV file.

Any supplier's layout can be read: headers are matched to fields by the general template's
names and aliases, plus the mapping saved in the supplier's profile and an optional one sent
with the request. Defaults come from the profile. Every row goes through the same checks as a
support created by hand, and the load is all or nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.jobs.application.schedule import next_run_at
from app.modules.supplier_supports.application import cells
from app.modules.supplier_supports.application import normalize as n
from app.modules.supplier_supports.application.profiles import (
    SupportProfileService,
    normalize_column_map,
    normalize_defaults,
)
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain import fields, overlap, vocabulary
from app.modules.supplier_supports.domain.bulk import (
    CREATE,
    UNCHANGED,
    UPDATE,
    BulkReport,
    MissingSupport,
    PreviewRow,
    RowIssue,
    merge,
    same_support,
    support_key,
)
from app.modules.supplier_supports.domain.entities import LoadPlan, Support, SupportData
from app.modules.supplier_supports.domain.overlap import OVERLAP_ACTIONS, Supersede, overlaps
from app.modules.supplier_supports.domain.ports import SupportFileReader
from app.modules.supplier_supports.domain.profiles import Profile

HEADER_SCAN_ROWS = 15
MIN_HEADER_FIELDS = 2
MAX_REPORTED_ERRORS = 500
PREVIEW_ROWS = 50
# Read first: later fields need to know the calc method (how ``supplier_value`` is typed).
_CHOICE_FIELDS = tuple(spec.name for spec in fields.FIELD_SPECS if spec.type == fields.CHOICE)
# "Tipo de apoyo" written as its label ("% sobre costo") instead of its code.
_CALC_LABELS = {
    **{n.fold(label): code for label, code in vocabulary.CALC_METHOD_ALIASES.items()},
    **{n.fold(label): code for code, label in vocabulary.CALC_METHOD_LABELS.items()},
}


_KIND_LABELS = {n.fold(label): code for code, label in vocabulary.KIND_LABELS.items()}
# The «Validación» column of a download with data: Sí keeps (and updates) the support, No
# expires it, blank leaves the row alone. Not a field of the support.
VALIDATION = "validation"
_VALIDATION_HEADERS = ("Validación", "Validacion proveedor", "Validado proveedor")
_YES = {"si", "s", "x", "ok", "yes"}
_NO = {"no", "n"}
# Fields whose change makes an updated row check crossings again.
_SCOPE = (
    "kind",
    "customer_nit",
    "customer_branch_id",
    "customer_group",
    "product_sku",
    "supplier_sku",
    "warehouse",
    "regionals",
    "channels",
    "business_units",
    "valid_from",
    "valid_to",
    "is_enabled",
)


class _BadCell(ValueError):
    """A cell that cannot be read as what its column holds."""


@dataclass(frozen=True)
class _Row:
    """A support read from the file: its sheet row, supplier, the fields it gives and its
    «Validación» (``si``/``no``; ``None`` without the column). A business group's row
    becomes one per customer (``from_group``)."""

    number: int
    data: SupportData
    supplier: str | None
    given: frozenset[str]
    verdict: str | None = None
    from_group: bool = False


def _verdict(raw: Any) -> tuple[str | None, str | None]:
    """``(si | no | None, problem)`` of a «Validación» cell."""

    if _blank(raw):
        return None, None
    text = n.fold(raw)
    if text in _YES:
        return "si", None
    if text in _NO:
        return "no", None
    return None, f"Validación: escribe Sí o No (o déjala vacía), no {raw!r}"


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _header_lookup(*column_maps: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Folded header -> field. The template's names first; each saved mapping overrides."""

    lookup: dict[str, str] = {n.fold(text): VALIDATION for text in _VALIDATION_HEADERS}
    for spec in fields.FIELD_SPECS:
        for text in (spec.label, spec.name, *spec.aliases):
            lookup.setdefault(n.fold(text), spec.name)
    for column_map in column_maps:
        for name, headers in column_map.items():
            for header in headers:
                lookup[n.fold(header)] = name
    return lookup


def _find_header(
    rows: Sequence[Sequence[Any]], lookup: Mapping[str, str]
) -> tuple[int, dict[str, list[int]]] | None:
    """The row (among the first ones) with the most recognized headers."""

    best: tuple[int, dict[str, list[int]]] | None = None
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        positions: dict[str, list[int]] = {}
        for column, cell in enumerate(row):
            if not _blank(cell) and (name := lookup.get(n.fold(cell))):
                positions.setdefault(name, []).append(column)
        if len(positions) >= MIN_HEADER_FIELDS and (best is None or len(positions) > len(best[1])):
            best = (index, positions)
    return best


def _default_value(name: str, value: Any) -> Any:
    """A stored (JSON-safe) default as the typed value the support holds."""

    kind = fields.FIELDS[name].type
    if kind == fields.FRACTION:
        try:
            return Decimal(str(value))
        except InvalidOperation:  # pragma: no cover - validated when the profile is saved
            return None
    if kind == fields.LIST:
        return tuple(value)
    return value


def _convert(name: str, raw: Any, calc: str | None, values: dict[str, Any]) -> Any:
    """One cell as the value of its field.

    Raises:
        _BadCell: If the cell cannot be read; the message says why.
    """

    spec = fields.FIELDS[name]
    if spec.type == fields.CHOICE:
        chosen = cells.choice(raw, spec.choices)
        if chosen is None and name == "calc_method":
            chosen = _CALC_LABELS.get(n.fold(raw))
        if chosen is None and name == "kind":
            chosen = _KIND_LABELS.get(n.fold(raw))
        if chosen is None:
            shown = {
                "calc_method": vocabulary.CALC_METHOD_LABELS.values(),
                "kind": [vocabulary.KIND_LABELS[kind] for kind in vocabulary.ACTIVE_KINDS],
            }.get(name, spec.choices)
            raise _BadCell(f"valor no válido: {raw!r} (válidos: {', '.join(shown)})")
        return chosen
    if spec.type == fields.TEXT:
        text = n.clean_text(raw)
        if text and spec.max_length and len(text) > spec.max_length:
            raise _BadCell(f"supera {spec.max_length} caracteres")
        return text
    if spec.type == fields.SKU:
        if n.is_excel_error(raw):
            raise _BadCell(f"la celda tiene un error de Excel ({str(raw).strip()})")
        return n.clean_sku(raw)
    if spec.type == fields.NIT:
        if n.is_excel_error(raw):
            raise _BadCell(f"la celda tiene un error de Excel ({str(raw).strip()})")
        nit = n.parse_nit(raw)
        if nit is None and n.fold(raw) not in n.OPEN_ENDED:
            raise _BadCell(f"NIT no válido: {raw!r}")
        return nit
    if spec.type == fields.DATE:
        if name == "valid_to" and n.fold(raw).startswith("hasta"):
            values["end_condition"] = n.clean_text(raw)
            return None
        parsed, issues = n.parse_date(cells.date_cell(raw), name)
        if any(issue.code != "fecha_texto" for issue in issues):
            raise _BadCell(f"fecha no válida: {raw!r} (usa dd/mm/aaaa o aaaa-mm-dd)")
        return parsed
    if spec.type == fields.FRACTION or (
        name == "supplier_value" and calc in fields.PERCENT_METHODS
    ):
        try:
            return cells.percent(raw)
        except ValueError as exc:
            raise _BadCell(str(exc)) from exc
    if spec.type == fields.NUMBER:
        number, issues = n.parse_number(raw, name)
        if issues or number is None:
            raise _BadCell(f"no es un número: {raw!r}")
        return number
    if name == "regionals":
        return n.parse_regions(raw)
    if name == "channels":
        return n.parse_channels(raw)
    units, channels = n.parse_units_and_channels(raw)
    if channels:
        values["channels"] = tuple(dict.fromkeys((*values.get("channels", ()), *channels)))
    return units


class SupportBulkService:
    """Simulate and apply the load of many supports from one file."""

    def __init__(
        self,
        supports: SupplierSupportService,
        profiles: SupportProfileService,
        reader: SupportFileReader,
        max_upload_bytes: int = 10 * 1024 * 1024,
        max_rows: int = 2000,
        quiet_hours: QuietHours | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            supports (SupplierSupportService): Checks and saves the supports.
            profiles (SupportProfileService): Per-supplier defaults and column mapping.
            reader (SupportFileReader): Reads the uploaded file.
            max_upload_bytes (int): Largest file accepted.
            max_rows (int): Most rows a file may hold.
            quiet_hours (QuietHours | None): When a big load may reach the DWH.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.supports = supports
        self.profiles = profiles
        self.reader = reader
        self.max_upload_bytes = max_upload_bytes
        self.max_rows = max_rows
        self.quiet_hours = quiet_hours
        self._clock = clock or (lambda: datetime.now(UTC))

    async def template_profile(self, supplier_code: str | None) -> Profile | None:
        """The profile whose layout the template should follow, if the supplier has one."""

        return await self.profiles.find(supplier_code) if supplier_code else None

    async def load(
        self,
        principal: Principal,
        filename: str,
        content: bytes,
        *,
        apply: bool = False,
        supplier_code: str | None = None,
        sheet: str | None = None,
        defaults: Mapping[str, Any] | None = None,
        column_map: Mapping[str, Any] | None = None,
        expire_missing: bool = False,
        on_overlap: str | None = None,
        groups: bool = False,
    ) -> BulkReport:
        """Check every row of the file and, with ``apply`` and no errors, write them all.

        Each row is matched to the stored support with its key
        (:func:`~app.modules.supplier_supports.domain.bulk.support_key`): a new key creates a
        support, a known one updates it (only the fields the file brings) or leaves it as it
        is. Stored supports of the same suppliers and classes that the file no longer brings
        are reported as missing and, with ``expire_missing``, closed the day before the load.

        Args:
            principal (Principal): The caller; must be a manager.
            filename (str): Name of the uploaded file.
            content (bytes): The file.
            apply (bool): Save the supports; otherwise only simulate.
            supplier_code (str | None): Commercial supplier of every row without its own code
                column; also selects the profile (defaults, sheet and column mapping).
            sheet (str | None): Sheet to read; the profile's, then the first one with headers.
            defaults (Mapping[str, Any] | None): Defaults for this load, over the profile's.
            column_map (Mapping[str, Any] | None): Field -> headers for this load, over the
                profile's.
            expire_missing (bool): Close the supports in force that the file no longer brings.
            on_overlap (str | None): What happens to a stored support a row crosses
                (``vencer_anterior`` / ``anular_anterior``); without it, a crossing is an error.
            groups (bool): The business-group upload: every row names a group (no NIT) and
                becomes one support per active customer of the group.

        A «Validación» column (the download with data) decides each row: Sí loads it, No
        expires the stored support yesterday with the row's observation, blank skips it.

        Returns:
            BulkReport: The simulation or the result.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If the file or the options cannot be used at all.
        """

        if not await self.supports.can_manage(principal):
            raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")
        if len(content) > self.max_upload_bytes:
            raise InvalidInputError(
                f"El archivo supera el máximo de {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        code = n.clean_text(supplier_code)
        profile = await self.profiles.find(code) if code else None
        effective_defaults = {
            **(profile.data.defaults if profile else {}),
            **normalize_defaults(defaults or {}),
        }
        saved_map = profile.data.column_map if profile else {}
        sent_map = normalize_column_map(column_map or {})
        lookup = _header_lookup(saved_map, sent_map)

        found = self._locate(
            filename, content, sheet or (profile.data.sheet if profile else None), lookup
        )
        title, rows, header_row, positions = found
        built = self._rows(rows, header_row, positions)
        if not built:
            raise InvalidInputError("El archivo no tiene filas de apoyos debajo de los encabezados")
        if len(built) > self.max_rows:
            raise InvalidInputError(
                f"El archivo tiene {len(built)} filas; el máximo por carga es {self.max_rows}"
            )

        if on_overlap is not None and on_overlap not in OVERLAP_ACTIONS:
            raise InvalidInputError(f"on_overlap inválido (válidos: {', '.join(OVERLAP_ACTIONS)})")
        if groups and "customer_group" not in positions:
            raise InvalidInputError("La subida por grupo empresarial necesita la columna Grupo")
        has_validation = VALIDATION in positions
        today = self.supports.calendar.today()
        last_day = today - timedelta(days=1)

        issues: list[RowIssue] = []
        rows_in: list[_Row] = []
        skipped = 0
        for number, row_cells in built:
            verdict = None
            if has_validation:
                verdict, problem = _verdict(row_cells.pop(VALIDATION, None))
                if problem is not None:
                    issues.append(RowIssue(number, VALIDATION, problem))
                    continue
                if verdict is None:
                    skipped += 1  # a blank «Validación» leaves the row alone
                    continue
            support, row_supplier, problems, given = self._support(
                number, row_cells, effective_defaults, code, filename
            )
            issues.extend(problems)
            if support is not None and not problems:
                if groups and support.customer_nit:
                    issues.append(
                        RowIssue(number, "customer_nit", "En la subida por grupo deja el NIT vacío")
                    )
                    continue
                rows_in.append(_Row(number, support, row_supplier, given, verdict))
        rows_in, group_issues = await self._expand_groups(rows_in, groups)
        issues.extend(group_issues)

        checked = await self.supports.check_many(
            principal, [(row.data, row.supplier) for row in rows_in]
        )
        accepted: dict[int, _Row] = {}
        for index, (row, result) in enumerate(zip(rows_in, checked, strict=True)):
            if result.error is not None or result.data is None:
                issues.append(RowIssue(row.number, result.field, result.error or "Apoyo no válido"))
            else:
                accepted[index] = replace(row, data=result.data)
        issues.extend(self._repeated(accepted))

        stored = await self.supports.live_for_suppliers(
            principal,
            sorted(
                {row.data.commercial_supplier_id for row in accepted.values()} - {None},
                key=str,
            ),
        )
        by_key: dict[tuple[Any, ...], Support] = {}
        for support in sorted(stored, key=lambda item: (item.version, item.updated_at)):
            if support.internal_request_id is not None:
                continue  # an internal request's support is never matched by a file
            by_key[support_key(support.data)] = support
        actions: dict[int, tuple[str, Support | None, SupportData]] = {}
        retirements: dict[UUID, Supersede] = {}
        for index, row in accepted.items():
            item, number = row.data, row.number
            match = by_key.get(support_key(item))
            if row.verdict == "no":
                # The supplier did not validate it: the stored support expires yesterday.
                if match is None:
                    issues.append(
                        RowIssue(number, VALIDATION, "Validación No: no hay un apoyo guardado así")
                    )
                else:
                    note = item.notes or "No validado por el proveedor"
                    retirements[match.id] = Supersede(match.id, match.version, last_day, note)
                continue
            if match is None:
                actions[index] = (CREATE, None, item)
                continue
            merged = merge(match.data, item, row.given)
            if same_support(merged, match.data):
                actions[index] = (UNCHANGED, match, match.data)
                continue
            try:
                self.supports.validate(merged)
            except InvalidInputError as exc:
                issues.append(RowIssue(number, getattr(exc, "field", None), exc.detail))
                continue
            actions[index] = (UPDATE, match, merged)

        flagged = {issue.row_number for issue in issues}
        crossing_issues, crossed = self._crossings(
            accepted,
            {i: action for i, action in actions.items() if accepted[i].number not in flagged},
            stored,
            set(retirements),
            on_overlap,
        )
        issues.extend(crossing_issues)
        for item in crossed:
            retirements.setdefault(item.support_id, item)

        bad_rows = {issue.row_number for issue in issues}
        valid = {
            index: action
            for index, action in actions.items()
            if accepted[index].number not in bad_rows
        }
        issues.sort(key=lambda issue: issue.row_number)

        matched = {support.id for _, support, _ in valid.values() if support is not None}
        matched |= set(retirements)
        classes = {(row.data.commercial_supplier_id, row.data.kind) for row in accepted.values()}
        missing = [
            support
            for support in stored
            if support.id not in matched
            # Supports born from an internal request never come in a supplier's file.
            and support.internal_request_id is None
            and (support.data.commercial_supplier_id, support.data.kind) in classes
            and (support.data.valid_to is None or support.data.valid_to >= today)
        ]
        expirations = (
            [
                support
                for support in missing
                if (support.data.valid_from is None or support.data.valid_from <= last_day)
            ]
            if expire_missing
            else []
        )
        expiring = {support.id for support in expirations}

        creates = [(uuid4(), item) for action, _, item in valid.values() if action == CREATE]
        updates = [
            (support.id, support.version, item)
            for action, support, item in valid.values()
            if action == UPDATE and support is not None
        ]
        unchanged = sum(1 for action, _, _ in valid.values() if action == UNCHANGED)
        writes = len(creates) + len(updates) + len(expirations) + len(retirements)

        blocked = bool(issues)
        created: list[UUID] = []
        updated: list[UUID] = []
        expired: list[UUID] = []
        retired: list[UUID] = []
        load_id = None
        not_before = (
            self.quiet_hours.not_before(writes, self._clock()) if self.quiet_hours else None
        )
        if apply and not blocked and (writes or unchanged):
            result = await self.supports.apply_load(
                principal,
                LoadPlan(
                    load_id=uuid4(),
                    file_name=filename.strip(),
                    file_sha256=hashlib.sha256(content).hexdigest(),
                    supplier_code=code,
                    sheet=title,
                    rows_read=len(built),
                    creates=tuple(creates),
                    updates=tuple(updates),
                    expirations=tuple(
                        (support.id, support.version, last_day) for support in expirations
                    ),
                    unchanged=unchanged,
                    missing=len(missing),
                    publish_not_before=not_before,
                    retirements=tuple(retirements.values()),
                ),
            )
            load_id = result.load_id
            created, updated, expired, retired = (
                list(result.created),
                list(result.updated),
                list(result.expired),
                list(result.retired),
            )
        with_conflicts = 0
        for support_id in created[:PREVIEW_ROWS]:
            if (await self.supports.get(support_id)).has_conflicts:
                with_conflicts += 1

        return BulkReport(
            applied=load_id is not None,
            blocked=blocked,
            sheet=title,
            header_row=header_row + 1,
            rows_read=len(built),
            valid=len(valid),
            invalid=len(bad_rows),
            errors=issues[:MAX_REPORTED_ERRORS],
            errors_truncated=len(issues) > MAX_REPORTED_ERRORS,
            preview=[
                self._preview(
                    accepted[index].number, item, accepted[index].supplier, action, support
                )
                for index, (action, support, item) in list(valid.items())[:PREVIEW_ROWS]
            ],
            to_create=len(creates),
            to_update=len(updates),
            unchanged=unchanged,
            missing=len(missing),
            to_expire=len(expirations),
            missing_preview=[
                MissingSupport(
                    support_id=support.id,
                    customer_nit=support.data.customer_nit,
                    product_sku=support.data.product_sku,
                    valid_from=support.data.valid_from,
                    valid_to=support.data.valid_to,
                    expires=support.id in expiring,
                )
                for support in missing[:PREVIEW_ROWS]
            ],
            updated=updated,
            expired=expired,
            load_id=load_id,
            publish_not_before=not_before,
            mapped_columns={
                name: [str(rows[header_row][column]).strip() for column in columns]
                for name, columns in positions.items()
            },
            ignored_columns=[
                str(cell).strip()
                for column, cell in enumerate(rows[header_row])
                if not _blank(cell) and all(column not in cols for cols in positions.values())
            ],
            profile_used=profile is not None,
            created=created,
            with_conflicts=with_conflicts,
            to_retire=len(retirements),
            retired=retired,
            skipped=skipped,
            group_supports=sum(1 for row in accepted.values() if row.from_group),
        )

    async def _expand_groups(
        self, rows: Sequence[_Row], required: bool
    ) -> tuple[list[_Row], list[RowIssue]]:
        """A row with a business group and no NIT becomes one support per customer of the
        group (its active NITs in the customer master), each with the group's name."""

        names = sorted(
            {row.data.customer_group for row in rows if row.data.customer_group}
            if required
            else {
                row.data.customer_group
                for row in rows
                if row.data.customer_group and not row.data.customer_nit
            }
        )
        members = await self.supports.group_members(names) if names else {}
        by_upper = {name.upper(): items for name, items in members.items()}
        expanded: list[_Row] = []
        issues: list[RowIssue] = []
        for row in rows:
            group = row.data.customer_group
            if not group or row.data.customer_nit:
                if required:
                    issues.append(RowIssue(row.number, "customer_group", "Falta el grupo"))
                else:
                    expanded.append(row)
                continue
            customers = by_upper.get(group.upper())
            if not customers:
                issues.append(
                    RowIssue(
                        row.number,
                        "customer_group",
                        f"El grupo empresarial {group} no tiene clientes activos en el maestro",
                    )
                )
                continue
            given = row.given | {"customer_nit", "customer_name"}
            for nit, name in customers:
                data = replace(row.data, customer_nit=nit, customer_name=name)
                expanded.append(replace(row, data=data, given=given, from_group=True))
        return expanded, issues

    @staticmethod
    def _crossings(
        accepted: Mapping[int, _Row],
        actions: Mapping[int, tuple[str, Support | None, SupportData]],
        stored: Sequence[Support],
        retiring: set[UUID],
        on_overlap: str | None,
    ) -> tuple[list[RowIssue], list[Supersede]]:
        """Rules v2 in a load: a created support, or an updated one whose scope or dates
        change, cannot cross a live support of the same class and scope.

        Against a stored support: an error, or with ``on_overlap`` the plan that expires or
        voids it. Against another row of the file: always an error.
        """

        updated = {support.id for _, support, _ in actions.values() if support is not None}
        index: dict[tuple[Any, ...], list[tuple[str, Any, SupportData]]] = {}

        def bucket(data: SupportData) -> tuple[Any, ...]:
            return (data.kind, data.commercial_supplier_id, data.customer_nit, data.product_sku)

        for support in stored:
            if support.id not in updated and support.id not in retiring:
                index.setdefault(bucket(support.data), []).append(("stored", support, support.data))
        issues: list[RowIssue] = []
        plan: list[Supersede] = []
        for position, (action, match, data) in actions.items():
            if action == UNCHANGED:
                continue
            row = accepted[position]
            checks = action == CREATE or (
                match is not None
                and any(getattr(data, name) != getattr(match.data, name) for name in _SCOPE)
            )
            candidates = index.setdefault(bucket(data), [])
            if checks:
                rows = [
                    other
                    for kind, other, other_data in candidates
                    if kind == "row" and overlaps(data, other_data)
                ]
                if rows:
                    issues.append(
                        RowIssue(
                            row.number,
                            None,
                            f"Se cruza con la fila {rows[0]} (misma clase, cliente, referencia "
                            "y vigencia)",
                        )
                    )
                found = [
                    other
                    for kind, other, other_data in candidates
                    if kind == "stored" and overlaps(data, other_data)
                ]
                if found and on_overlap is None:
                    first = found[0].data
                    issues.append(
                        RowIssue(
                            row.number,
                            "valid_from",
                            f"Se cruza con un apoyo vigente ({first.valid_from or 'sin inicio'} a "
                            f"{first.valid_to or 'indefinido'}): elige vencer o anular el anterior",
                        )
                    )
                elif found:
                    plan.extend(overlap.supersede(data, found, on_overlap or "", row.data.notes))
            candidates.append(("row", row.number, data))
        return issues, plan

    # ----------------------------------------------------------------- reading

    def _locate(
        self, filename: str, content: bytes, sheet: str | None, lookup: Mapping[str, str]
    ) -> tuple[str, list[list[Any]], int, dict[str, list[int]]]:
        sheets = self.reader.read(filename.strip(), content, sheet)
        first_row: list[Any] = []
        best: dict[bool, tuple[str, list[list[Any]], int, dict[str, list[int]]]] = {}
        # The Excel table whose header names the most fields (the supports table, not the
        # product lookup beside it); whole sheets only when no table matches. A sheet title
        # cannot hold "/", so "sheet / table" marks a table.
        for title, rows in sheets:
            if not first_row and rows:
                first_row = rows[0]
            is_table = " / " in title
            current = best.get(is_table)
            if (found := _find_header(rows, lookup)) is not None and (
                current is None or len(found[1]) > len(current[3])
            ):
                best[is_table] = (title, rows, found[0], found[1])
        if best:
            return best.get(True) or best[False]
        seen = ", ".join(str(cell).strip() for cell in first_row if not _blank(cell))
        raise InvalidInputError(
            "No se reconocieron las columnas del archivo"
            + (f" (encabezados leídos: {seen})" if seen else "")
            + ". Usa la plantilla, indica el mapeo de columnas o configura el perfil del proveedor"
        )

    @staticmethod
    def _rows(
        rows: Sequence[Sequence[Any]], header_row: int, positions: Mapping[str, list[int]]
    ) -> list[tuple[int, dict[str, Any]]]:
        """Non-empty rows below the header as ``field -> first non-blank cell``."""

        built: list[tuple[int, dict[str, Any]]] = []
        for offset, row in enumerate(rows[header_row + 1 :], start=header_row + 2):
            row_cells: dict[str, Any] = {}
            for name, columns in positions.items():
                for column in columns:
                    value = row[column] if column < len(row) else None
                    if not _blank(value):
                        row_cells[name] = value
                        break
            if row_cells:
                built.append((offset, row_cells))
        return built

    # ------------------------------------------------------------------ a row

    def _support(
        self,
        number: int,
        row_cells: Mapping[str, Any],
        defaults: Mapping[str, Any],
        supplier_code: str | None,
        filename: str,
    ) -> tuple[SupportData | None, str | None, list[RowIssue], frozenset[str]]:
        """The support of one row, its supplier, its problems and the fields it gives.

        A cell that reads as "nothing" (``INDEFINIDO`` in the end date) still counts as given:
        it clears that field when the row updates a stored support.
        """

        values: dict[str, Any] = {}
        problems: list[RowIssue] = []

        def read(name: str) -> None:
            # The calc method may come from the profile, not from a column of the file.
            calc = values.get("calc_method") or defaults.get("calc_method")
            try:
                values[name] = _convert(name, row_cells[name], calc, values)
            except _BadCell as exc:
                problems.append(RowIssue(number, name, f"{fields.FIELDS[name].label}: {exc}"))

        for name in _CHOICE_FIELDS:
            if name in row_cells:
                read(name)
        for name in row_cells:
            if name not in _CHOICE_FIELDS and name != "supplier_code":
                read(name)
        for name, value in defaults.items():
            if values.get(name) in (None, ()) and name not in row_cells:
                values[name] = _default_value(name, value)

        row_supplier = n.clean_text(row_cells.get("supplier_code")) or supplier_code
        if row_supplier is None:
            problems.append(
                RowIssue(
                    number,
                    "supplier_code",
                    "Falta el proveedor: elígelo en la carga o agrega la columna Cod. Proveedor",
                )
            )
        if not values.get("calc_method") and "calc_method" not in row_cells:
            problems.append(
                RowIssue(
                    number,
                    "calc_method",
                    "Falta el tipo de apoyo: agrégalo como columna o como valor por defecto "
                    "del perfil",
                )
            )
        if (
            values.get("calc_method") == "margen_minimo"
            and values.get("min_margin_pct") is None
            and values.get("supported_margin_pct") is not None
        ):
            # The short template has one margin column: for a minimum margin it is that margin.
            values["min_margin_pct"] = values["supported_margin_pct"]
        if not values.get("kind") and "kind" not in row_cells and values.get("calc_method"):
            values["kind"] = _default_kind(values)
        given = frozenset(name for name in values if name != "supplier_code")
        if problems:
            return None, row_supplier, problems, given
        values = {name: value for name, value in values.items() if value is not None}
        values["source"] = f"carga masiva: {filename.strip()}"[:300]
        return SupportData(**values), row_supplier, problems, given

    @staticmethod
    def _repeated(accepted: Mapping[int, _Row]) -> list[RowIssue]:
        """Rows with the same key as an earlier one of the same file (they would fight over
        the same support)."""

        seen: dict[tuple[Any, ...], int] = {}
        issues: list[RowIssue] = []
        for row in accepted.values():
            key = support_key(row.data)
            if key in seen:
                issues.append(
                    RowIssue(
                        row.number,
                        None,
                        f"Fila repetida: misma llave (proveedor, NIT, SKU, acuerdo y fecha "
                        f"inicio) que la fila {seen[key]}",
                    )
                )
            else:
                seen[key] = row.number
        return issues

    @staticmethod
    def _preview(
        number: int,
        item: SupportData,
        supplier_code: str | None,
        action: str,
        support: Support | None,
    ) -> PreviewRow:
        return PreviewRow(
            row_number=number,
            supplier_code=supplier_code,
            kind=item.kind,
            calc_method=item.calc_method,
            program=item.program,
            customer_nit=item.customer_nit,
            product_sku=item.product_sku,
            supplier_value=item.supplier_value,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            action=action,
            support_id=support.id if support is not None else None,
        )


def _default_kind(values: Mapping[str, Any]) -> str:
    """The class of a row that does not say it: a minimum margin is a margin exception; with a
    client it is a client tactical support; otherwise an activity for everyone."""

    if values.get("calc_method") == "margen_minimo":
        return "excepcion_margen"
    return "tactico_cliente" if values.get("customer_nit") else "actividad_temporal"


@dataclass(frozen=True)
class QuietHours:
    """When the warehouse is quiet (no ETL): big loads are published to it then."""

    start: time
    end: time
    zone: ZoneInfo
    # Loads writing more supports than this wait for the quiet hours.
    defer_over: int

    def not_before(self, writes: int, now: datetime) -> datetime | None:
        """``None`` (publish now) for a small load or inside the window; else its next start."""

        if writes <= self.defer_over:
            return None
        local = now.astimezone(self.zone)
        clock = local.time()
        inside = (
            self.start <= clock < self.end
            if self.start < self.end
            else clock >= self.start or clock < self.end
        )
        if inside:
            return None
        return next_run_at(now, self.start, self.zone)


__all__ = ["QuietHours", "SupportBulkService"]
