"""Domain entities and validation for the site catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import InvalidInputError

_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ABBREVIATION_PATTERN = re.compile(r"^[A-Z]{2,4}$")
_OM_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

# Series of PQRSF cases filed without a site (P26GN0001); no site may take it.
GENERIC_SITE_ABBREVIATION = "GN"


@dataclass
class Site:
    """A PQRSF submission site.

    ``abbreviation`` is the site's part of every PQRSF consecutive (P26AC0001).
    ``om_code`` is its part of an improvement opportunity sent to a supplier (1181BOG);
    ``city`` and ``address`` are printed on that document.
    """

    id: UUID
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    abbreviation: str = ""
    city: str | None = None
    address: str | None = None
    om_code: str | None = None


def clean_site_code(value: str) -> str:
    """Normalize and validate the immutable site code."""

    code = "-".join(value.strip().lower().split())
    if not code or len(code) > 80 or not _CODE_PATTERN.fullmatch(code):
        raise InvalidInputError(
            "El código de la sede debe usar minúsculas, números y guiones (máximo 80 caracteres)"
        )
    return code


def clean_site_abbreviation(value: str) -> str:
    """Normalize and validate the letters a site puts in PQRSF consecutives."""

    abbreviation = value.strip().upper()
    if not _ABBREVIATION_PATTERN.fullmatch(abbreviation):
        raise InvalidInputError("La sigla de la sede debe tener de 2 a 4 letras, sin tildes")
    if abbreviation == GENERIC_SITE_ABBREVIATION:
        raise InvalidInputError(
            f"La sigla {GENERIC_SITE_ABBREVIATION} está reservada para casos sin sede"
        )
    return abbreviation


def clean_om_code(value: str | None) -> str | None:
    """Normalize the three letters a site puts in improvement opportunities (BOG)."""

    if value is None or not value.strip():
        return None
    code = value.strip().upper()
    if not _OM_CODE_PATTERN.fullmatch(code):
        raise InvalidInputError("El código de OM de la sede debe tener 3 letras, sin tildes")
    return code


def clean_site_text(value: str | None, label: str, limit: int) -> str | None:
    """Normalize an optional one-line text; blank clears it."""

    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > limit:
        raise InvalidInputError(f"{label} admite máximo {limit} caracteres")
    return text


def clean_site_name(value: str) -> str:
    """Normalize a display name and reject blank or oversized values."""

    name = " ".join(value.split())
    if not name:
        raise InvalidInputError("El nombre de la sede no puede estar vacío")
    if len(name) > 150:
        raise InvalidInputError("El nombre de la sede admite máximo 150 caracteres")
    return name
