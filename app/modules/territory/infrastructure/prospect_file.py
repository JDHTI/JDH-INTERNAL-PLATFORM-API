"""Read the monthly prospects workbook: one sheet per zone, one row per prospect.

The coordinates come from ``Latitud``/``Longitud`` columns when the file has them, and
otherwise from the hyperlink of the ``Mapa`` cell (``…/maps?q=6.28,-75.56``): the cell text is
just "Ver mapa". That is why the workbook is opened in normal mode: read-only mode does not
expose hyperlinks.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import date, datetime
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from app.core.errors import InvalidInputError
from app.modules.territory.domain.contacts import (
    clean_website,
    coordinates_from_link,
    parse_emails,
    parse_phones,
)
from app.modules.territory.domain.entities import (
    ParsedFile,
    Point,
    ProspectData,
    RowIssue,
    SheetSummary,
)
from app.modules.territory.domain.vocabulary import (
    APPROXIMATE,
    EXACT,
    MISSING,
    SHARED_POINT_THRESHOLD,
    in_colombia,
)

# Sheets of the consolidated file that are not prospects.
IGNORED_SHEETS = frozenset({"consolidado", "descartados", "resumen", "criterios", "instrucciones"})
MAX_ROWS = 20_000
# Header -> field. Headers are compared without accents, case or extra spaces.
_COLUMNS: dict[str, str] = {
    "id": "external_id",
    "establecimiento": "name",
    "nombre comercial": "name",
    "categoria": "category",
    "cliente antiguo": "former",
    "ultima compra": "last_purchase",
    "direccion": "address",
    "sector": "sector",
    "barrio": "sector",
    "ubicacion": "area_status",
    "telefono / whatsapp": "phone",
    "telefono": "phone",
    "whatsapp": "phone",
    "celular": "phone",
    "correo": "email",
    "correo electronico": "email",
    "email": "email",
    "pagina web": "website",
    "sitio web": "website",
    "web": "website",
    "mapa": "map",
    "latitud": "latitude",
    "lat": "latitude",
    "longitud": "longitude",
    "lon": "longitude",
    "lng": "longitude",
    "observacion": "observation",
    "observaciones": "observation",
    "zona": "zone",
}
_AREA_STATUS = {
    "dentro del area": "inside",
    "fuera del area": "outside",
    "por ubicar": "unlocated",
}
_YES = frozenset({"si", "s", "x", "yes", "true", "1", "verdadero"})


def normalize(value: Any) -> str:
    """Lowercase text without accents and with single spaces."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().lower()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _month(value: Any) -> date | None:
    if isinstance(value, datetime | date):
        return date(value.year, value.month, 1)
    text = _text(value)
    if not text:
        return None
    match = re.match(r"^(\d{4})[-/](\d{1,2})", text)
    if match and 1 <= int(match.group(2)) <= 12:
        return date(int(match.group(1)), int(match.group(2)), 1)
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    return float(text)


def _header(sheet: Worksheet) -> tuple[int, dict[str, int]] | None:
    """The header row (1-based) and field -> column index, if the sheet has prospects."""

    for row_number, row in enumerate(sheet.iter_rows(min_row=1, max_row=5, values_only=True), 1):
        columns: dict[str, int] = {}
        for index, cell in enumerate(row):
            field = _COLUMNS.get(normalize(cell))
            if field and field not in columns:
                columns[field] = index
        if "external_id" in columns and "name" in columns:
            return row_number, columns
    return None


class SpreadsheetProspectReader:
    """Prospects of an ``.xlsx`` workbook, one sheet per zone."""

    def read(self, content: bytes) -> ParsedFile:
        """Read every prospect sheet of the workbook.

        Args:
            content (bytes): The ``.xlsx`` file.

        Returns:
            ParsedFile: Rows with their location quality, per-sheet counts and problems.

        Raises:
            InvalidInputError: When the file is not a workbook or has no prospect sheet.
        """

        try:
            book = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise InvalidInputError(
                "Formato no soportado: sube el archivo de prospectos en Excel (.xlsx)"
            ) from exc
        try:
            return self._read(book)
        finally:
            book.close()

    def _read(self, book: openpyxl.Workbook) -> ParsedFile:
        rows: list[tuple[str, int, ProspectData]] = []
        errors: list[RowIssue] = []
        sheets: list[str] = []
        read = [0]
        for sheet in book.worksheets:
            if normalize(sheet.title) in IGNORED_SHEETS:
                continue
            header = _header(sheet)
            if header is None:
                continue
            sheets.append(sheet.title)
            rows.extend(self._sheet_rows(sheet, *header, errors, read))
            if read[0] > MAX_ROWS:
                raise InvalidInputError(f"El archivo supera el máximo de {MAX_ROWS} prospectos")
        if not sheets:
            raise InvalidInputError(
                "El archivo no tiene hojas de prospectos: cada hoja de zona necesita las "
                "columnas ID y Establecimiento"
            )
        self._check_duplicates(rows, errors)
        data = self._mark_shared_points([row for _, _, row in rows])
        summaries = []
        for name in sheets:
            in_sheet = [
                item for (sheet, _, _), item in zip(rows, data, strict=True) if sheet == name
            ]
            located = sum(1 for item in in_sheet if item.location is not None)
            summaries.append(
                SheetSummary(
                    name=name,
                    rows=len(in_sheet),
                    with_location=located,
                    without_location=len(in_sheet) - located,
                )
            )
        return ParsedFile(rows=data, sheets=summaries, errors=errors, rows_read=read[0])

    def _sheet_rows(
        self,
        sheet: Worksheet,
        header_row: int,
        columns: dict[str, int],
        errors: list[RowIssue],
        read: list[int],
    ) -> list[tuple[str, int, ProspectData]]:
        found: list[tuple[str, int, ProspectData]] = []
        for cells in sheet.iter_rows(min_row=header_row + 1):
            row_number = cells[0].row

            def cell(field: str, cells: tuple[Any, ...] = cells) -> Any:
                index = columns.get(field)
                return cells[index] if index is not None and index < len(cells) else None

            def value(field: str) -> Any:
                found_cell = cell(field)
                return found_cell.value if found_cell is not None else None

            if all(item.value in (None, "") for item in cells):
                continue
            read[0] += 1
            external_id, name = _text(value("external_id")), _text(value("name"))
            problems: list[RowIssue] = []
            if not external_id:
                problems.append(RowIssue(sheet.title, row_number, "ID", "Falta el ID"))
            if not name:
                problems.append(
                    RowIssue(sheet.title, row_number, "Establecimiento", "Falta el nombre")
                )
            location = self._location(sheet.title, row_number, cell, value, problems)
            if problems:
                errors.extend(problems)
                continue
            phone_raw, email_raw = _text(value("phone")), _text(value("email"))
            website_cell = cell("website")
            website = clean_website(
                website_cell.hyperlink.target
                if website_cell is not None and website_cell.hyperlink
                else value("website")
            )
            data = ProspectData(
                external_id=external_id or "",
                source_zone=_text(value("zone")) or sheet.title.strip(),
                name=name or "",
                category=_text(value("category")),
                is_former_customer=normalize(value("former")) in _YES,
                last_purchase_month=_month(value("last_purchase")),
                address=_text(value("address")),
                sector=_text(value("sector")),
                area_status=_AREA_STATUS.get(normalize(value("area_status"))),
                phone_raw=phone_raw,
                email_raw=email_raw,
                phones=tuple(parse_phones(phone_raw)),
                emails=tuple(parse_emails(email_raw)),
                website=website,
                observation=_text(value("observation")),
                location=location,
                location_quality=EXACT if location else MISSING,
            )
            found.append((sheet.title, row_number, data))
        return found

    def _location(
        self, sheet: str, row_number: int, cell: Any, value: Any, problems: list[RowIssue]
    ) -> Point | None:
        latitude = longitude = None
        if value("latitude") not in (None, "") or value("longitude") not in (None, ""):
            try:
                latitude, longitude = _number(value("latitude")), _number(value("longitude"))
            except ValueError:
                problems.append(
                    RowIssue(sheet, row_number, "Latitud", "Latitud o longitud no es un número")
                )
                return None
            if latitude is None or longitude is None:
                problems.append(
                    RowIssue(sheet, row_number, "Latitud", "Falta la latitud o la longitud")
                )
                return None
        else:
            map_cell = cell("map")
            link = None
            if map_cell is not None:
                link = map_cell.hyperlink.target if map_cell.hyperlink else _text(map_cell.value)
            point = coordinates_from_link(link)
            if point is None:
                return None
            latitude, longitude = point
        if not in_colombia(latitude, longitude):
            problems.append(
                RowIssue(
                    sheet,
                    row_number,
                    "Mapa",
                    f"La ubicación ({latitude}, {longitude}) está fuera de Colombia",
                )
            )
            return None
        return Point(latitude=latitude, longitude=longitude)

    @staticmethod
    def _check_duplicates(
        rows: list[tuple[str, int, ProspectData]], errors: list[RowIssue]
    ) -> None:
        seen: dict[str, tuple[str, int]] = {}
        for sheet, row_number, data in rows:
            key = data.external_id.casefold()
            if key in seen:
                first_sheet, first_row = seen[key]
                errors.append(
                    RowIssue(
                        sheet,
                        row_number,
                        "ID",
                        f"El ID {data.external_id} se repite (hoja {first_sheet}, fila "
                        f"{first_row})",
                    )
                )
            else:
                seen[key] = (sheet, row_number)

    @staticmethod
    def _mark_shared_points(rows: list[ProspectData]) -> list[ProspectData]:
        """Rows that share one point with many others are approximate."""

        def key(item: ProspectData) -> tuple[float, float] | None:
            if item.location is None:
                return None
            return (round(item.location.latitude, 6), round(item.location.longitude, 6))

        counts = Counter(key(item) for item in rows if item.location is not None)
        marked = []
        for item in rows:
            point = key(item)
            if point is not None and counts[point] >= SHARED_POINT_THRESHOLD:
                item = replace(item, location_quality=APPROXIMATE)
            marked.append(item)
        return marked


__all__ = ["IGNORED_SHEETS", "MAX_ROWS", "SpreadsheetProspectReader", "normalize"]
