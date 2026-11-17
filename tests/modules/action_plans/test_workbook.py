"""The P.04.F.02 workbook: reading the 2026 matrix (layout, dates, codes) and exporting it."""

import io
from datetime import date

import openpyxl
import pytest

from app.modules.action_plans.application.importer import import_matrix
from app.modules.action_plans.infrastructure.workbook import (
    cause_category,
    excel_date,
    group_actions,
    read_rows,
    write_matrix,
)
from tests.modules.action_plans.fakes import Plans


def _matrix(rows: list[dict]) -> bytes:
    """A workbook with the P.04 layout: titles above, headers on row 6, data from row 7."""

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Base datos"
    sheet["D2"] = "SISTEMA INTEGRADO DE GESTIÓN"
    sheet["A6"], sheet["E6"] = "Fecha", "CODIGO/ CONSECUTIVO"
    for index, values in enumerate(rows, start=7):
        for column, value in values.items():
            sheet[f"{column}{index}"] = value
    # A pivot table sheet like the real one: it must be ignored.
    book.create_sheet("Indicador")["C4"] = "Acciones Correctivas"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


_FIRST = {
    "A": 46051,
    "C": 121,
    "D": 1,
    "E": "SNC-290126-121-DIS-1",
    "F": "SNC",
    "G": "DIS",
    "H": "Correctiva",
    "I": "REAL",
    "J": "900304940",
    "K": "Jessika Moreno",
    "L": "Tour Vacation Hoteles",
    "M": "Funza ",
    "N": "Cristian Garzón",
    "O": "Analista de Calidad",
    "P": 46052,
    "R": "No conformidad en la entrega de pollo",
    "S": "NO",
    "T": "NO",
    "U": "Reunir a las partes interesadas",
    "V": "Camilo Jaramillo",
    "W": 46051,
    "Y": "Método de las 5M",
    "Z": "Porqué: a la Mano de Obra: el personal no conoce los requisitos",
    "AA": "Capacitar al personal",
    "AB": "Cristian Garzón",
    "AC": 46055,
    "AD": "P.10.F.05 Lista de Asistencia",
    "AE": "Camilo Jaramillo",
    "AF": 46081,
    "AG": "SI",
    "AH": "CERRADA",
    "AI": "SI",
    "AJ": "Se capacitó al personal",
}
_SECOND = {
    "A": 46051,
    "C": 121,
    "E": "SNC-290126-121-DIS-",  # «INTENTO» left empty: same action, first attempt
    "F": "SNC",
    "G": "DIS",
    "H": "Correctiva",
    "Y": "Método de las 5M",
    "Z": "Porqué: a la Maquinaria: rotuladora mal configurada",
    "AA": "Estandarizar la rotuladora",
    "AC": "30-02-2026",
    "AF": "17-oct-26",
    "AG": "NO",
    "AH": "EN PROCESO",
    "AI": "PTE",
}
_OTHER = {
    "A": 46209,
    "C": 168,
    "D": 1,
    "E": "AE-060726-168-ALM-1",
    "F": "AE",
    "G": "ALM",
    "H": "Correctiva",
    "I": "REAL",
    "R": "Hallazgo de auditoría",
    "AA": "Ajustar el procedimiento",
    "AC": "02/18/2026",
}


def test_dates_come_as_serials_day_first_month_first_or_with_the_month_name() -> None:
    assert excel_date("46051") == (date(2026, 1, 29), None)
    assert excel_date("18-02-2026")[0] == date(2026, 2, 18)
    assert excel_date("02/18/2026")[0] == date(2026, 2, 18)
    assert excel_date("17-oct-26")[0] == date(2026, 10, 17)
    assert excel_date("30-02-2026") == (None, "30-02-2026")
    assert excel_date("En Proceso") == (None, "En Proceso")


def test_a_cause_takes_the_category_it_names() -> None:
    assert cause_category("1. Porqué: a la Mano de Obra\n* El personal…") == "mano_obra"
    assert cause_category("Porqué: a los Métodos: manual") == "metodos"
    assert cause_category("5. Porqué: a la Materia Prima") == "materiales"
    assert cause_category("Sin categoría") == "otra"


def test_rows_group_by_action_and_a_lost_attempt_joins_the_first() -> None:
    actions = group_actions(read_rows(_matrix([_FIRST, _SECOND, _OTHER])))

    assert [a.code for a in actions] == ["SNC-290126-121-DIS-1", "AE-060726-168-ALM-1"]
    first = actions[0]
    assert first.source_rows == [7, 8]
    kinds = [row.kind for row in first.rows]
    assert kinds == ["correction", "cause", "activity", "cause", "activity"]
    second_activity = first.rows[-1].values
    assert second_activity["done"] is False and second_activity["effective"] == "pendiente"
    assert second_activity["follow_up_due_on"] == date(2026, 10, 17)
    assert first.issues == ["Fila 8: fecha «30-02-2026» en AC no válida"]


@pytest.mark.asyncio
async def test_the_import_creates_each_action_once() -> None:
    plans = Plans()
    content = _matrix([_FIRST, _SECOND, _OTHER])

    dry = await import_matrix(content, plans, apply=False)
    applied = await import_matrix(content, plans, apply=True)
    again = await import_matrix(content, plans, apply=True)

    assert dry.created == applied.created == ["SNC-290126-121-DIS-1", "AE-060726-168-ALM-1"]
    assert plans.plans and len(plans.plans) == 2
    assert again.created == [] and len(again.existing) == 2
    plan = next(p for p in plans.plans.values() if p.number == 121)
    assert plan.city == "Funza" and plan.analysis_method == "5m"
    assert [c.category for c in plan.causes] == ["mano_obra", "maquinaria"]
    assert [a.done for a in plan.activities] == [True, False]
    assert plan.status == "en_proceso" and plan.effectiveness == "pendiente"
    assert plan.legacy["rows"] == [7, 8] and plan.legacy["issues"]
    assert {e.event_type for e in plans.events} == {"imported"}


@pytest.mark.asyncio
async def test_an_action_with_an_unknown_process_is_skipped_and_reported() -> None:
    plans = Plans()
    report = await import_matrix(_matrix([{**_OTHER, "G": "XYZ"}]), plans, apply=True)

    assert report.created == [] and "XYZ" in report.skipped[0]


@pytest.mark.asyncio
async def test_the_export_writes_the_p04_layout() -> None:
    plans = Plans()
    await import_matrix(_matrix([_FIRST, _SECOND, _OTHER]), plans, apply=True)
    ordered = sorted(plans.plans.values(), key=lambda p: (p.reported_on, p.number))

    sheet = openpyxl.load_workbook(io.BytesIO(write_matrix(ordered)))["Base datos"]

    assert sheet["AH2"].value == "Código: P.04.F.02"
    assert sheet["E6"].value == "CODIGO/ CONSECUTIVO"
    first = [c.value for c in sheet[7]]
    assert first[4] == "SNC-290126-121-DIS-1" and first[8] == "REAL"
    assert first[20] == "Reunir a las partes interesadas" and first[24] == "Método de las 5M"
    assert [c.value for c in sheet[8]][32:35] == ["NO", "EN PROCESO", "PTE"]
    assert sheet[9][4].value == "AE-060726-168-ALM-1"
