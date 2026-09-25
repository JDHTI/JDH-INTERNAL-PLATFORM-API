"""Domain entities and validation for the site catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import InvalidInputError

_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Site:
    """A PQRSF submission site."""

    id: UUID
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def clean_site_code(value: str) -> str:
    """Normalize and validate the immutable site code."""

    code = "-".join(value.strip().lower().split())
    if not code or len(code) > 80 or not _CODE_PATTERN.fullmatch(code):
        raise InvalidInputError(
            "El código de la sede debe usar minúsculas, números y guiones (máximo 80 caracteres)"
        )
    return code


def clean_site_name(value: str) -> str:
    """Normalize a display name and reject blank or oversized values."""

    name = " ".join(value.split())
    if not name:
        raise InvalidInputError("El nombre de la sede no puede estar vacío")
    if len(name) > 150:
        raise InvalidInputError("El nombre de la sede admite máximo 150 caracteres")
    return name
