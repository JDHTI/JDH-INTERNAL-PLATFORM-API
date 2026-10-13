"""Cleaning of the messy cells of ``1. MAESTRO APOYOS PROVEEDORES.xlsx``.

Pure functions, no workbook library: each takes the raw cell value and returns
the clean value plus the :class:`Issue` objects describing what had to be
guessed, so the importer never silently "fixes" data.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.supplier_supports.domain.entities import Issue

OPEN_ENDED = {
    "indefinido",
    "indefinida",
    "no definida",
    "no definido",
    "ufn",
    "n a",
    "na",
    "todos",
}
_DATE_PARTS = re.compile(r"^\D*(\d{1,2})\D+(\d{1,2})\D+(\d{4})\D*$")
_PERCENT = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*%")
_SKU_AT_START = re.compile(r"^\s*(\d{3,7})(?!\d)")
_NIT_IN_TEXT = re.compile(r"(?<!\d)(\d{6,10})(?!\d)")
_BUSINESS_UNITS = {
    "FOOD": "FOOD SERVICE",
    "FOOD SERVICE": "FOOD SERVICE",
    "RETAIL": "RETAIL",
    "MASIVO": "MASIVO",
    "SERVICIOS": "SERVICIOS",
    "TIENDAS": "TIENDAS",
}
_CHANNEL_ALIASES = {
    "MAYORISTAS": "MAYORISTAS Y DISTRIBUIDORES",
    "MAYORISTAS Y DISTRIBUIDORES": "MAYORISTAS Y DISTRIBUIDORES",
    "AUTOSERVICIOS": "AUTOSERVICIO",
    "CAJAS COMPESANCION": "CAJAS DE COMPENSACION",
    "HOTELES": "HOTELES Y CLUBES",
    "CLUBES": "HOTELES Y CLUBES",
}
_REGION_ALIASES = {"ANTIQOUIA": "ANTIOQUIA", "URABA": "URABA"}
_EVERYTHING = {"TODOS", "TODAS", "NACIONAL", "TODO", ""}


def fold(value: object) -> str:
    """Lower-case ASCII with single spaces; the key used to match headers and aliases."""

    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9%$]+", " ", text.lower()).strip()


def clean_text(value: object) -> str | None:
    """Collapse whitespace (NBSP included); ``None`` when blank or an Excel error."""

    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    if not text or text.startswith("#"):
        return None
    return text


def is_excel_error(value: object) -> bool:
    """Whether a cached cell holds an Excel error such as ``#REF!``."""

    return isinstance(value, str) and value.strip().startswith("#")


def clean_sku(value: object) -> str | None:
    """A SKU as text: ``8749.0`` becomes ``8749``; blanks and errors become ``None``."""

    if value is None or is_excel_error(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = clean_text(value)
    return None if text is None or text.upper() in {"NO ESTA", "N/A"} else text


def parse_date(value: object, field: str) -> tuple[date | None, list[Issue]]:
    """Parse a validity date.

    ``INDEFINIDO`` / ``UFN`` / blank mean open-ended (``None``, no issue). Real
    dates pass through. Text dates are read as day/month/year; a day that does
    not exist (31/09) is clamped to the last day of the month and flagged; text
    that cannot be read, and the 1900 placeholders Excel produces from empty
    formulas, give ``None`` and an issue.

    Args:
        value (object): Cell value.
        field (str): Field name, recorded in the issue.

    Returns:
        tuple[date | None, list[Issue]]: The date and what was guessed.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return None, []
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        if value.year <= 1900:
            return None, [Issue("fecha_invalida", f"fecha {value.isoformat()}", field)]
        return value, []
    text = str(value).replace("\xa0", " ").strip()
    if fold(text) in OPEN_ENDED:
        return None, []
    match = _DATE_PARTS.match(text)
    if match is None:
        return None, [Issue("fecha_invalida", text, field)]
    day, month, year = (int(part) for part in match.groups())
    if not 1 <= month <= 12 or not 1900 < year < 2200 or day < 1:
        return None, [Issue("fecha_invalida", text, field)]
    last = calendar.monthrange(year, month)[1]
    clean = re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text) is not None
    if day > last:
        return date(year, month, last), [Issue("fecha_invalida", f"{text} (ajustada)", field)]
    return date(year, month, day), [] if clean else [Issue("fecha_texto", text, field)]


def parse_number(value: object, field: str) -> tuple[Decimal | None, list[Issue]]:
    """Parse a number or a Colombian money text such as ``$ 131.974``.

    Args:
        value (object): Cell value.
        field (str): Field name, recorded in the issue.

    Returns:
        tuple[Decimal | None, list[Issue]]: The value and a ``valor_texto`` issue
        when it had to be read from text or could not be read at all.
    """

    if value is None or isinstance(value, bool):
        return None, []
    if isinstance(value, (int, float, Decimal)):
        # Excel stores 0.18 as 0.17999999999999994; the columns keep 6 decimals.
        return round(Decimal(str(value)), 6), []
    text = str(value).replace("\xa0", " ").strip()
    if not text or is_excel_error(text):
        return None, []
    digits = re.sub(r"[^\d,.\-]", "", text)
    if not digits or not re.search(r"\d", digits):
        return None, [Issue("valor_texto", text, field)]
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", digits):
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", ".")
    try:
        return Decimal(digits), []
    except InvalidOperation:
        return None, [Issue("valor_texto", text, field)]


def parse_fraction(value: object, field: str) -> tuple[Decimal | None, list[Issue]]:
    """Parse a percentage stored as a fraction (``0.045``) or as text (``4,5%``).

    Values above 1 are not percentages as fractions: they are rejected with an
    issue (the workbook has prices typed in margin columns).
    """

    if isinstance(value, str):
        found = _PERCENT.search(value)
        if found:
            number = Decimal(found.group(1).replace(",", ".")) / 100
            plain = re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*%\s*", value) is not None
            return number, [] if plain else [Issue("valor_texto", value.strip(), field)]
    number, issues = parse_number(value, field)
    if number is not None and not -1 <= number <= 1:
        return None, [Issue("valor_texto", f"{value} no es una fracción", field)]
    return number, issues


def parse_pp(value: object) -> Decimal | None:
    """``5pp`` is 5 % over cost, as a fraction (0.05); anything else is ``None``.

    A cell written like this is a discount over the supplier's whole portfolio, not over
    one product.
    """

    if not isinstance(value, str):
        return None
    found = re.fullmatch(r"\s*(-?\d+(?:[.,]\d+)?)\s*pp\s*", value, re.IGNORECASE)
    return Decimal(found.group(1).replace(",", ".")) / 100 if found else None


def parse_nit(value: object, *, check_digit: bool = False) -> str | None:
    """Digits of a client NIT; ``None`` for ``TODOS``, blanks and errors.

    Args:
        value (object): Cell value.
        check_digit (bool): The cell includes the verification digit, which is dropped.
    """

    if is_excel_error(value):
        return None
    nit = clean_nit(value)
    if nit is None:
        return None
    return nit[:-1] if check_digit and len(nit) > 1 else nit


def nits_in_text(value: object) -> list[str]:
    """NITs written inside free text such as ``800116299 - SUMMAR``."""

    return list(dict.fromkeys(_NIT_IN_TEXT.findall(str(value or "").replace(".", ""))))


def skus_in_text(value: object) -> list[str]:
    """SKUs that start a line of a multi-line cell (``8749Arroz Diana x 1Kg``)."""

    found = []
    for line in str(value or "").splitlines():
        match = _SKU_AT_START.match(line)
        if match:
            found.append(match.group(1))
    return list(dict.fromkeys(found))


def percent_in_text(value: object) -> Decimal | None:
    """First percentage written in a free-text cell (``6,38% de lista``) as a fraction."""

    found = _PERCENT.search(str(value or ""))
    return Decimal(found.group(1).replace(",", ".")) / 100 if found else None


def _split_scope(value: object) -> list[str]:
    parts = re.split(r"[\n/,;]|\s-|-\s", str(value or ""))
    return [part for part in (clean_text(part) for part in parts) if part]


def parse_regions(value: object) -> tuple[str, ...]:
    """Regions of a cell; ``TODOS`` / ``NACIONAL`` mean no restriction.

    Fixes the known typo ``ANTIQOUIA`` and splits ``ANTIOQUIA -CENTRO- COSTA``.
    """

    regions: list[str] = []
    for part in _split_scope(value):
        name = part.upper()
        if name in _EVERYTHING:
            return ()
        regions.append(_REGION_ALIASES.get(name, name))
    return tuple(dict.fromkeys(regions))


def parse_units_and_channels(value: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a business-unit/channel cell into ``(business_units, channels)``.

    ``FOOD`` is the business unit FOOD SERVICE; ``MAYORISTAS`` is the channel
    MAYORISTAS Y DISTRIBUIDORES. ``TODOS`` means no restriction. Anything else
    is kept upper-cased as a channel.
    """

    units: list[str] = []
    channels: list[str] = []
    for part in _split_scope(str(value or "").replace(" - ", "/")):
        name = part.upper()
        if name in _EVERYTHING:
            return (), ()
        if name.startswith("RETAIL"):
            units.append("RETAIL")
        elif name in _BUSINESS_UNITS:
            units.append(_BUSINESS_UNITS[name])
        else:
            channels.append(_CHANNEL_ALIASES.get(name, name))
    return tuple(dict.fromkeys(units)), tuple(dict.fromkeys(channels))


def parse_channels(value: object) -> tuple[str, ...]:
    """Channels of a CANAL column (HOTELES, CLUBES, CASAS DE ASEO...)."""

    names = []
    for part in _split_scope(value):
        name = part.upper()
        if name in _EVERYTHING:
            return ()
        names.append(_CHANNEL_ALIASES.get(name, name))
    return tuple(dict.fromkeys(names))


def authorizer_in_text(value: object) -> str | None:
    """Name after ``AUTORIZA`` in an observation (``AUTORIZA ISABEL QUIROGA``)."""

    word = r"[A-ZÁÉÍÓÚÑ]{3,}"
    found = re.search(rf"AUTORIZ[AÓO]\s+(?:POR\s+)?({word}(?: {word}){{0,2}})", str(value or ""))
    return clean_text(found.group(1)) if found else None


def flag_sequential_nits(rows: list[tuple[str | None, str | None]]) -> set[int]:
    """Positions whose NIT looks drag-filled from the row above.

    The workbook has runs such as ``860504410 ... 860504418`` under one company
    name. A run of three or more consecutive NITs with the same name is flagged
    from its second row on.

    Args:
        rows (list[tuple[str | None, str | None]]): ``(nit, name)`` per row, in sheet order.

    Returns:
        set[int]: Indexes of the suspicious rows.
    """

    flagged: set[int] = set()
    start = 0
    for index in range(1, len(rows) + 1):
        continues = (
            index < len(rows)
            and rows[index][0] is not None
            and rows[index - 1][0] is not None
            and rows[index][1] == rows[index - 1][1]
            and rows[index][1] is not None
            and int(rows[index][0]) == int(rows[index - 1][0]) + 1
        )
        if continues:
            continue
        if index - start >= 3:
            flagged.update(range(start + 1, index))
        start = index
    return flagged
