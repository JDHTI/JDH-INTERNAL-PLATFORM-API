"""The simulation of a rep master upload: the file against what the warehouse has."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.core.errors import InvalidInputError
from app.modules.commercial_budget.domain.vendor_master import (
    CurrentCoordinator,
    CurrentRep,
    normalize_unit,
    preview,
)
from app.modules.commercial_budget.infrastructure.vendor_file import read_vendor_master

REPS = ["N° DOCUMENTO", "NOMBRE VENDEDOR", "EQUIPO", "CLASE", "UNIDAD NEGOCIO", "REGIONAL",
        "COORDINADOR", "DIRECTOR", "CORREO"]  # fmt: skip


def workbook(reps: list[list], coordinators: list[list] | None = None) -> bytes:
    book = Workbook()
    book.remove(book.active)
    sheet = book.create_sheet("COMERCIALES JDH")
    sheet.append(REPS)
    for row in reps:
        sheet.append(row)
    coordinator = book.create_sheet("Coordinadores")
    coordinator.append(["CEDULA", "COORDINADOR", "EQUIPO", "C.O"])
    for row in coordinators or []:
        coordinator.append(row)
    directors = book.create_sheet("Directores")
    directors.append(["CEDULA", "DIRECTOR"])
    directors.append(["700", "Gomez Director"])
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


CURRENT = [
    CurrentRep("0101", "ANA", ("1001",), "JUNIOR/CALL MEDELLIN", "CALL CENTER", "ANTIOQUIA",
               "FOOD SERVICE"),
    CurrentRep("0102", "LUIS", ("1002",), "JUNIOR/CALL MEDELLIN", "CALL CENTER", "ANTIOQUIA",
               "FOOD SERVICE"),
    CurrentRep("0103", "EVA", ("01003",), "SENIOR MEDELLIN", "SENIOR", "ANTIOQUIA", "FOOD SERVICE"),
    CurrentRep("0104", "TOM", ("1004",), "ORIENTE", "ORIENTE", "ORIENTE", "MASIVO"),
    CurrentRep("0105", "SOL", ("1005", "1005"), "SENIOR MEDELLIN", "SENIOR", "ANTIOQUIA",
               "FOOD SERVICE"),
]  # fmt: skip
CENTERS = {"ACO URABA": "URABA"}
REGIONALS = {"ANTIOQUIA", "ORIENTE", "URABA"}


def run(content: bytes, recent=None):
    return preview(
        read_vendor_master(content),
        CURRENT,
        [CurrentCoordinator("900", "PEREZ COORD", "JUNIOR/CALL MEDELLIN")],
        centers=CENTERS,
        regionals=REGIONALS,
        recent=recent or {},
    )


def test_the_file_is_read_with_the_loaders_rules() -> None:
    content = workbook([["1001", "Ana  Ruiz", "junior medellin", "Junior", "Mix Food-Masivo",
                         "Antioquia", "Perez Coord", "N/A", "x@y.co"]])  # fmt: skip

    master = read_vendor_master(content)

    row = master.reps[0]
    assert (row.document, row.name, row.team) == ("1001", "ANA RUIZ", "JUNIOR MEDELLIN")
    assert row.business_unit == "FOOD SERVICE / MASIVO" and row.regional == "ANTIOQUIA"
    assert normalize_unit("Retail/Masivo") == "RETAIL / MASIVO"
    assert master.director_names == frozenset({"GOMEZ DIRECTOR"})


def test_a_file_without_a_required_column_or_sheet_is_refused() -> None:
    book = Workbook()
    book.active.title = "COMERCIALES JDH"
    book.active.append(["N° DOCUMENTO", "EQUIPO"])
    output = io.BytesIO()
    book.save(output)

    with pytest.raises(InvalidInputError, match="UNIDAD NEGOCIO|Falta la hoja"):
        read_vendor_master(output.getvalue())
    with pytest.raises(InvalidInputError, match="xlsx"):
        read_vendor_master(b"no es un excel")


def test_team_changes_new_and_emptied_teams() -> None:
    content = workbook(
        [
            ["1001", "Ana", "JUNIOR MEDELLIN", "JUNIOR", "FOOD SERVICE", "ANTIOQUIA", "", "", ""],
            ["1002", "Luis", "CALL CENTER MEDELLIN", "CALL CENTER", "FOOD SERVICE", "ANTIOQUIA",
             "", "", ""],
            ["1003", "Eva", "SENIOR MEDELLIN", "SENIOR", "FOOD SERVICE", "ACO URABA", "", "", ""],
            ["1004", "Tom", "ORIENTE", "ORIENTE", "MASIVO", "ORIENTE", "", "", ""],
        ],
        coordinators=[["900", "Perez Coord", "JUNIOR/CALL MEDELLIN", "ANTIOQUIA"]],
    )  # fmt: skip

    result = run(content, recent={"0105": 12, "0104": 3})

    teams = {(item.code, item.field): item for item in result.changes}
    assert teams[("0101", "team")].current == "JUNIOR/CALL MEDELLIN"
    assert teams[("0101", "team")].new == "JUNIOR MEDELLIN"
    assert teams[("0102", "team")].new == "CALL CENTER MEDELLIN"
    assert teams[("0101", "team_class")].new == "JUNIOR"
    # ACO URABA is an operation center: its regional, URABA.
    assert teams[("0103", "regional")].new == "URABA"
    # 1003 found without the ERP's leading zero.
    assert any("ceros" in item.reason for item in result.warnings)
    assert result.unchanged == 1 and result.changed_reps == 3
    assert result.new_teams == ["CALL CENTER MEDELLIN", "JUNIOR MEDELLIN"]
    assert result.emptied_teams == ["JUNIOR/CALL MEDELLIN"]
    # SOL sold and is not in the file: it keeps its team.
    assert [item.code for item in result.missing] == ["0105"]
    # The coordinator's team is no rep's team anymore: the link to his reps breaks.
    assert result.coordinators[0].team == "JUNIOR/CALL MEDELLIN"
    assert "conserva su equipo actual" in result.coordinators[0].reason
    counts = {item.team: (item.current, item.new) for item in result.teams}
    assert counts["SENIOR MEDELLIN"] == (2, 2) and counts["JUNIOR/CALL MEDELLIN"] == (2, 0)


def test_rejections_and_warnings_as_the_loader() -> None:
    content = workbook(
        [
            ["", "Sin documento", "ORIENTE", "", "MASIVO", "", "", "", ""],
            ["9999", "Nadie", "ORIENTE", "", "MASIVO", "ORIENTE", "", "", ""],
            ["1004", "Tom", "ORIENTE", "OTRA", "MASIVO", "MARTE", "Fantasma", "", ""],
            ["1004", "Tom otra vez", "ORIENTE", "ORIENTE", "MASIVO", "", "", "", ""],
        ]
    )

    result = run(content)

    assert [item.reason for item in result.rejected] == [
        "DOCUMENTO vacío",
        "El documento no es de ningún vendedor del ERP",
    ]
    reasons = " | ".join(item.reason for item in result.warnings)
    assert "MARTE" not in reasons or "VARIOS" in reasons
    assert "REGIONAL vacía" in reasons and "ya viene en la fila" in reasons
    assert "No está en las hojas Coordinadores ni Directores" in reasons
    # The last row of a repeated rep wins.
    regional = next(item for item in result.changes if item.field == "regional")
    assert regional.new == "VARIOS"
