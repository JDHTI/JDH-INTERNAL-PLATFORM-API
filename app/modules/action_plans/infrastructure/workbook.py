"""The P.04.F.02 workbook: read the 2026 matrix and write the matrix back in its layout.

Reading goes straight to the sheet XML: the real workbook carries a pivot cache of about
184 MB that makes openpyxl take minutes. Each action of the sheet «Base datos» spans
several rows with the same code (column E): the first one carries the header and every row
may add an immediate correction (U–X), a cause (Y–Z) and an activity with its follow-up
(AA–AJ).
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modules.action_plans.domain.entities import (
    ACTION_TYPES,
    METHODS,
    ORIGINS,
    RATINGS,
    STATUSES,
    ActionPlan,
)

SHEET = "Base datos"
HEADER_ROW = 6
_EXCEL_EPOCH = date(1899, 12, 30)
_CELL = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)

COLUMNS = [
    ("A", "Fecha\nmm-dd-aa"),
    ("B", "MES"),
    ("C", "NÚMERO"),
    ("D", "INTENTO\nAntecedentes"),
    ("E", "CODIGO/ CONSECUTIVO"),
    ("F", "ORIGEN"),
    ("G", "PROCEDENCIA"),
    ("H", "TIPO DE ACCION"),
    ("I", "CALIFICACIÓN"),
    ("J", "IDENTIFICA/NIT O CEDULA"),
    ("K", "IDENTIFICA/ NOMBRE"),
    ("L", "EMPRESA/ CARGO"),
    ("M", "CIUDAD"),
    ("N", "QUIEN REGISTRA\n(o elabora) LA ACCIÓN"),
    ("O", "CARGO\n(Quien registra o elabora la acción)"),
    ("P", "FECHA DE REGISTRO:\nElaboración"),
    ("Q", "REQUISITO INCUMPLIDO"),
    ("R", "DESCRIPCIÓN DEL HALLAZGO"),
    ("S", "REGISTRO FOTOGRAFICO"),
    ("T", "ANTECEDENTES?\nEs intento"),
    ("U", "CORRECCIÓN INMEDIATA\nAcciones (Actividades)"),
    ("V", "RESPONSABLE (S)\nCorrección Inmediata"),
    ("W", "FECHA LIMITE:\nCorrección Inmediata"),
    ("X", "EVIDENCIAS\n(Si aplica)"),
    ("Y", "MÉTODO DE ANALISIS"),
    ("Z", "CAUSAS"),
    ("AA", "PLAN DE ACCIÓN\nAcciones Correctivas y Preventivas (actividades)"),
    ("AB", "RESPONSABLE (S)\nPlan de Acción"),
    ("AC", "FECHA LIMITE:\nPlan de Acción"),
    ("AD", "EVIDENCIAS\n(Si aplica)"),
    ("AE", "RESPONSABLE (S)\nSeguimiento"),
    ("AF", "FECHA LIMITE:\nSeguimiento"),
    ("AG", "ACCION REALIZADA?"),
    ("AH", "ESTADO"),
    ("AI", "EFICAZ?"),
    ("AJ", "DESCRIPCIÓN EFICACIA"),
]
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


# --- Reading ---


@dataclass
class SheetRow:
    """One row of «Base datos», by column letter."""

    number: int
    cells: dict[str, str]

    def get(self, column: str) -> str | None:
        """The cell text, stripped; ``None`` when empty."""

        value = (self.cells.get(column) or "").strip()
        return value or None


@dataclass
class ImportedRow:
    """A correction, a cause or an activity read from one row."""

    kind: str
    values: dict


@dataclass
class ImportedAction:
    """One action of the matrix, as read (before it becomes an ``ActionPlan``)."""

    code: str
    header: dict
    rows: list[ImportedRow] = field(default_factory=list)
    source_rows: list[int] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml").decode("utf8")
    except KeyError:
        return []
    return [
        _unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", item)))
        for item in re.findall(r"<si>(.*?)</si>", xml, re.S)
    ]


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("_x000D_", "")
    )


def _sheet_path(archive: zipfile.ZipFile, name: str) -> str:
    workbook = archive.read("xl/workbook.xml").decode("utf8")
    relations = archive.read("xl/_rels/workbook.xml.rels").decode("utf8")
    for sheet_name, rel_id in re.findall(
        r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', workbook
    ):
        if _unescape(sheet_name) == name:
            target = re.search(rf'Id="{rel_id}"[^>]*Target="([^"]+)"', relations) or re.search(
                rf'Target="([^"]+)"[^>]*Id="{rel_id}"', relations
            )
            if target:
                path = target.group(1).lstrip("/")
                return path if path.startswith("xl/") else f"xl/{path}"
    raise ValueError(f"El libro no tiene la hoja «{name}»")


def read_rows(content: bytes, sheet: str = SHEET) -> list[SheetRow]:
    """The rows of a sheet as text, by column letter (shared strings resolved).

    Raises:
        ValueError: Not an .xlsx or without the sheet.
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("El archivo no es un libro de Excel (.xlsx)") from exc
    strings = _strings(archive)
    xml = archive.read(_sheet_path(archive, sheet)).decode("utf8")
    rows: list[SheetRow] = []
    for match in re.finditer(r'<row [^>]*r="(\d+)"[^>]*>(.*?)</row>', xml, re.S):
        cells: dict[str, str] = {}
        for cell in _CELL.finditer(match.group(2)):
            column, attrs, inner = cell.group(1), cell.group(3), cell.group(4) or ""
            value = re.search(r"<v>([^<]*)</v>", inner)
            inline = re.search(r"<t[^>]*>([^<]*)</t>", inner)
            if 't="s"' in attrs and value:
                cells[column] = strings[int(value.group(1))]
            elif inline:
                cells[column] = _unescape(inline.group(1))
            elif value:
                cells[column] = _unescape(value.group(1))
        if cells:
            rows.append(SheetRow(int(match.group(1)), cells))
    return rows


def excel_date(value: str | None) -> tuple[date | None, str | None]:
    """A date from a serial number or ``dd-mm-yyyy`` / ``dd/mm/yyyy`` text.

    Returns:
        tuple[date | None, str | None]: The date, or ``None`` and the text it could not read.
    """

    if not value:
        return None, None
    text = value.strip()
    try:
        serial = float(text)
    except ValueError:
        serial = None
    if serial is not None:
        if 20_000 < serial < 80_000:
            return _EXCEL_EPOCH + timedelta(days=int(serial)), None
        return None, text
    # Day first (how Colombia writes it), then month first: the column says «mm-dd-aa» and
    # some cells were typed that way («02/18/2026»).
    for pattern in (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%y",
        "%d/%m/%y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(text, pattern).date(), None
        except ValueError:
            continue
    # «17-oct-26»: the month by its Spanish abbreviation.
    named = re.fullmatch(r"(\d{1,2})[-/ ]([a-záéíóú]{3})[a-z]*[-/ ](\d{2}|\d{4})", _fold(text))
    if named and named.group(2) in _MONTHS:
        year = int(named.group(3))
        try:
            return date(
                year + 2000 if year < 100 else year,
                _MONTHS.index(named.group(2)) + 1,
                int(named.group(1)),
            ), None
        except ValueError:
            pass
    return None, text


def _method(text: str | None) -> str | None:
    if not text:
        return None
    folded = _fold(text)
    if "5m" in folded:
        return "5m"
    if "espina" in folded:
        return "espina_pescado"
    if "lluvia" in folded:
        return "lluvia_ideas"
    if "no hay fallo" in folded or "respuesta formal" in folded:
        return "sin_fallo"
    return None


def cause_category(text: str) -> str:
    """The 5M/6M category a cause names («Porqué: a la Mano de Obra …»)."""

    folded = _fold(text)[:120]
    for needle, category in (
        ("mano de obra", "mano_obra"),
        ("metodo", "metodos"),
        ("maquinaria", "maquinaria"),
        ("maquina", "maquinaria"),
        ("materia prima", "materiales"),
        ("material", "materiales"),
        ("medio ambiente", "medio_ambiente"),
        ("entorno", "medio_ambiente"),
        ("medicion", "medicion"),
    ):
        if needle in folded:
            return category
    return "otra"


def _choice(text: str | None, mapping: dict[str, str]) -> str | None:
    if not text:
        return None
    folded = _fold(text)
    return mapping.get(folded)


_RATING = {"real": "real", "potencial": "potencial", "oportunidad de mejora": "oportunidad_mejora"}
_TYPE = {"correctiva": "correctiva", "preventiva": "preventiva", "mejora": "mejora"}
_EFFECTIVE = {"si": "si", "no": "no", "pte": "pendiente", "pendiente": "pendiente"}


def group_actions(rows: Iterable[SheetRow]) -> list[ImportedAction]:
    """The actions of the matrix: rows grouped by code, each turned into its parts."""

    actions: dict[str, ImportedAction] = {}
    for row in rows:
        if row.number <= HEADER_ROW or not row.get("A") or not row.get("E"):
            continue
        code = row.get("E") or ""
        if code.endswith("-"):
            # A row whose «INTENTO» was left empty: the formula of column E lost the attempt.
            # It belongs to the first attempt of its action.
            code += row.get("D") or "1"
        action = actions.get(code)
        if action is None:
            action = actions[code] = ImportedAction(code=code, header={})
        action.source_rows.append(row.number)
        _header(action, row)
        _parts(action, row)
    return list(actions.values())


def _header(action: ImportedAction, row: SheetRow) -> None:
    header = action.header
    for column, name in (
        ("C", "number"),
        ("D", "attempt"),
        ("F", "origin"),
        ("G", "process"),
        ("H", "type"),
        ("I", "rating"),
        ("J", "reporter_nit"),
        ("K", "reporter_name"),
        ("L", "reporter_company_role"),
        ("M", "city"),
        ("N", "registered_by_name"),
        ("O", "registered_by_position"),
        ("Q", "unmet_requirement"),
        ("R", "finding"),
        ("S", "has_photos"),
        ("T", "has_history"),
        ("Y", "method"),
    ):
        if name not in header and row.get(column):
            header[name] = row.get(column)
    for column, name in (("A", "reported_on"), ("P", "registered_on")):
        if name not in header and row.get(column):
            value, bad = excel_date(row.get(column))
            if value:
                header[name] = value
            elif bad:
                action.issues.append(f"Fila {row.number}: fecha «{bad}» en {column} no válida")


def _row_date(action: ImportedAction, row: SheetRow, column: str) -> date | None:
    value, bad = excel_date(row.get(column))
    if bad:
        action.issues.append(f"Fila {row.number}: fecha «{bad}» en {column} no válida")
    return value


def _parts(action: ImportedAction, row: SheetRow) -> None:
    if row.get("U"):
        action.rows.append(
            ImportedRow(
                "correction",
                {
                    "description": row.get("U"),
                    "responsible_text": row.get("V"),
                    "due_on": _row_date(action, row, "W"),
                    "evidence_note": row.get("X"),
                },
            )
        )
    if row.get("Z"):
        action.rows.append(
            ImportedRow(
                "cause",
                {"description": row.get("Z"), "category": cause_category(row.get("Z") or "")},
            )
        )
    if any(row.get(c) for c in ("AA", "AB", "AC", "AD", "AE", "AF", "AG", "AI", "AJ")):
        done = (row.get("AG") or "").lower() == "si" or (
            not row.get("AG") and (row.get("AH") or "").lower() == "cerrada"
        )
        action.rows.append(
            ImportedRow(
                "activity",
                {
                    "description": row.get("AA") or "(Sin actividad descrita en la matriz)",
                    "responsible_text": row.get("AB"),
                    "due_on": _row_date(action, row, "AC"),
                    "evidence_note": row.get("AD"),
                    "follow_up_text": row.get("AE"),
                    "follow_up_due_on": _row_date(action, row, "AF"),
                    "done": done,
                    "effective": _choice(row.get("AI"), _EFFECTIVE) or "pendiente",
                    "effectiveness_note": row.get("AJ"),
                },
            )
        )


def header_choices(action: ImportedAction) -> tuple[str | None, str | None, str | None, str | None]:
    """``(origin, type, rating, method)`` keys of an imported header."""

    header = action.header
    origin = (header.get("origin") or "").strip().upper()
    action_type = _choice(header.get("type"), _TYPE)
    rating = _choice(header.get("rating"), _RATING)
    return (
        origin if origin in ORIGINS else None,
        action_type,
        rating,
        _method(header.get("method")),
    )


# --- Writing ---

# How the Excel writes the effectiveness (column AI).
_EFFECTIVE_CELL = {"si": "SI", "no": "NO", "pendiente": "PTE"}


def _yes(value: bool) -> str:
    return "SI" if value else "NO"


def write_matrix(plans: Iterable[ActionPlan]) -> bytes:
    """The matrix in the P.04.F.02 layout: one row per correction / cause / activity."""

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    bold = Font(bold=True)
    sheet["D2"], sheet["AH2"] = "SISTEMA INTEGRADO DE GESTIÓN", "Código: P.04.F.02"
    sheet["AH3"] = "Versión: 02"
    sheet["D4"], sheet["AH4"] = "SEGUIMIENTO PLAN DE ACCIÓN", "Fecha: 18-11-25"
    sheet["B5"], sheet["D5"] = "Fecha Actualización", datetime.now().date()
    for cell in ("D2", "D4", "AH2", "AH3", "AH4", "B5"):
        sheet[cell].font = bold
    fill = PatternFill("solid", fgColor="D9D9D9")
    for index, (column, title) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(HEADER_ROW, index, title)
        cell.font, cell.fill = bold, fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[get_column_letter(index)].width = 18 if column < "Q" else 28
    row_number = HEADER_ROW + 1
    for plan in plans:
        lines = max(len(plan.corrections), len(plan.causes), len(plan.activities), 1)
        for line in range(lines):
            values = {
                "A": plan.reported_on,
                "B": _MONTHS[plan.reported_on.month - 1],
                "C": plan.number,
                "D": plan.attempt,
                "E": plan.code,
                "F": plan.origin,
                "G": plan.process.code,
                "H": ACTION_TYPES[plan.action_type],
            }
            if line == 0:
                values.update(
                    {
                        "I": RATINGS[plan.rating].upper(),
                        "J": plan.reporter_nit,
                        "K": plan.reporter_name,
                        "L": plan.reporter_company_role,
                        "M": plan.city,
                        "N": plan.registered_by_name,
                        "O": plan.registered_by_position,
                        "P": plan.registered_on,
                        "Q": plan.unmet_requirement,
                        "R": plan.finding,
                        "S": _yes(plan.has_photos),
                        "T": _yes(plan.has_history),
                    }
                )
            if plan.analysis_method and plan.causes and line < len(plan.causes):
                values["Y"] = METHODS[plan.analysis_method]
            if line < len(plan.corrections):
                item = plan.corrections[line]
                values.update(
                    {
                        "U": item.description,
                        "V": item.responsible_text,
                        "W": item.due_on,
                        "X": item.evidence_note,
                    }
                )
            if line < len(plan.causes):
                values["Z"] = plan.causes[line].description
            if line < len(plan.activities):
                item = plan.activities[line]
                values.update(
                    {
                        "AA": item.description,
                        "AB": item.responsible_text,
                        "AC": item.due_on,
                        "AD": item.evidence_note,
                        "AE": item.follow_up_text,
                        "AF": item.follow_up_due_on,
                        "AG": _yes(item.done),
                        "AH": STATUSES["cerrada" if item.done else "en_proceso"].upper(),
                        "AI": _EFFECTIVE_CELL[item.effective],
                        "AJ": item.effectiveness_note,
                    }
                )
            for index, (column, _) in enumerate(COLUMNS, start=1):
                value = values.get(column)
                if value is None or value == "":
                    continue
                cell = sheet.cell(row_number, index, value)
                if isinstance(value, date):
                    cell.number_format = "dd/mm/yyyy"
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            row_number += 1
    sheet.freeze_panes = sheet.cell(HEADER_ROW + 1, 6)
    sheet.auto_filter.ref = f"A{HEADER_ROW}:AJ{max(row_number - 1, HEADER_ROW)}"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
