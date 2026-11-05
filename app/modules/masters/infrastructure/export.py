"""Write a master's records to an Excel workbook, one column per declared field."""

from __future__ import annotations

import io
from collections.abc import Sequence

import openpyxl

from app.modules.masters.domain.entities import MasterDetail, MasterRecord

# Columns the record keeps outside ``attributes``, by field role.
_ROLE_VALUES = {
    "key": lambda record: record.source_key,
    "company": lambda record: record.company_id,
    "code": lambda record: record.code,
    "name": lambda record: record.name,
}
# Excel rejects text that starts like a formula when opened by someone else's macro.
_FORMULA_START = ("=", "+", "-", "@")


def _cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return f"'{value}"
    return value


def master_workbook(detail: MasterDetail, records: Sequence[MasterRecord]) -> bytes:
    """The records as an ``.xlsx`` with the master's field labels as headers.

    Args:
        detail (MasterDetail): The master and the fields to include.
        records (Sequence[MasterRecord]): The rows.

    Returns:
        bytes: The workbook.
    """

    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet(title=detail.summary.name[:31] or "Maestro")
    fields = detail.fields
    sheet.append([field.label for field in fields] + ["Activo", "Sincronizado"])
    for record in records:
        row: list[object] = []
        for field in fields:
            if field.role == "active":
                row.append(record.attributes.get(field.name, "A" if record.is_active else "I"))
                continue
            getter = _ROLE_VALUES.get(field.role)
            value = getter(record) if getter else record.attributes.get(field.name)
            row.append(_cell(value))
        row.append("Sí" if record.is_active else "No" if record.is_active is False else None)
        row.append(record.synced_at.replace(tzinfo=None) if record.synced_at else None)
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
