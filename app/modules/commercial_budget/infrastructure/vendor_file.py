"""Reads a «Maestro Vendedores JDH» workbook as dwh-platform's loader does (no personal data
is kept: only documents, names, teams, regionals, units and supervisors)."""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

from openpyxl import load_workbook

from app.core.errors import InvalidInputError
from app.modules.commercial_budget.domain.vendor_master import (
    COORDINATORS_SHEET,
    DIRECTORS_SHEET,
    REPS_SHEET,
    CoordinatorRow,
    MasterFile,
    RepRow,
    normalize,
    normalize_unit,
)

_ALIASES = {"N° DOCUMENTO": "DOCUMENTO", "Nº DOCUMENTO": "DOCUMENTO"}
_REQUIRED = {
    REPS_SHEET: ("DOCUMENTO", "UNIDAD NEGOCIO"),
    COORDINATORS_SHEET: ("CEDULA",),
    DIRECTORS_SHEET: ("CEDULA",),
}


def _sheet(workbook, name: str) -> list[dict[str, Any]]:
    if name not in workbook.sheetnames:
        raise InvalidInputError(f"Falta la hoja «{name}»")
    rows = workbook[name].iter_rows(values_only=True)
    header = [_ALIASES.get(normalize(cell) or "", normalize(cell)) for cell in next(rows, ())]
    present = [item for item in header if item]
    if len(present) != len(set(present)):
        raise InvalidInputError(f"{name}: hay encabezados repetidos")
    for column in _REQUIRED[name]:
        if column not in header:
            raise InvalidInputError(f"{name}: falta la columna {column}")
    found = []
    for number, values in enumerate(rows, start=2):
        if all(normalize(value) is None for value in values):
            continue
        record = {key: value for key, value in zip(header, values, strict=False) if key}
        record["_ROW"] = number
        found.append(record)
    return found


def read_vendor_master(content: bytes) -> MasterFile:
    """The rep, coordinator and director rows of the workbook.

    Raises:
        InvalidInputError: Not a workbook, a sheet or a required column missing, or
            repeated headers (the loader rejects the whole file then).
    """

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable file is the user's input
        raise InvalidInputError("No se pudo leer el archivo: no es un .xlsx válido") from exc
    try:
        reps = [
            RepRow(
                row=record["_ROW"],
                document=normalize(record.get("DOCUMENTO")),
                name=normalize(record.get("NOMBRE VENDEDOR")),
                team=normalize(record.get("EQUIPO")),
                team_class=normalize(record.get("CLASE")),
                regional=normalize(record.get("REGIONAL")),
                business_unit=normalize_unit(record.get("UNIDAD NEGOCIO")),
                coordinator=normalize(record.get("COORDINADOR")),
                director=normalize(record.get("DIRECTOR")),
            )
            for record in _sheet(workbook, REPS_SHEET)
        ]
        coordinators = [
            CoordinatorRow(
                row=record["_ROW"],
                document=normalize(record.get("CEDULA")),
                name=normalize(record.get("COORDINADOR")),
                team=normalize(record.get("EQUIPO")),
            )
            for record in _sheet(workbook, COORDINATORS_SHEET)
        ]
        directors = frozenset(
            name
            for record in _sheet(workbook, DIRECTORS_SHEET)
            if (name := normalize(record.get("DIRECTOR")))
        )
        return MasterFile(reps, coordinators, directors)
    finally:
        workbook.close()


def standardize_classes(content: bytes, teams: Mapping[str, str | None]) -> bytes:
    """The workbook with each rep row's CLASE set to its team's class in the master
    (2026-11-27), so dwh-platform never stores a class the master does not have (the
    template's formula has no value until Excel saves the file)."""

    try:
        workbook = load_workbook(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - any unreadable file is the user's input
        raise InvalidInputError("No se pudo leer el archivo: no es un .xlsx válido") from exc
    sheet = workbook[REPS_SHEET]
    header = [normalize(cell.value) for cell in sheet[1]]
    if "EQUIPO" not in header:
        return content
    team_column = header.index("EQUIPO") + 1
    if "CLASE" in header:
        class_column = header.index("CLASE") + 1
    else:
        class_column = len(header) + 1
        sheet.cell(1, class_column, "CLASE")
    for row in range(2, sheet.max_row + 1):
        team = normalize(sheet.cell(row, team_column).value)
        sheet.cell(row, class_column).value = teams.get(team) if team else None
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


__all__ = ["read_vendor_master", "standardize_classes"]
