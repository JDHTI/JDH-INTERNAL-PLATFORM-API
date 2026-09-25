"""Document management entities and their pure rules.

Depth is fixed at area / folder / file: folders have no parent.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

from app.core.errors import InvalidInputError

MAX_TAGS = 20
MAX_TAG_LENGTH = 50

_HEX_COLOR = re.compile(r"#[0-9a-f]{6}")

# mimetypes does not know every Office format on every platform.
_CONTENT_TYPES = {
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
}


@dataclass
class DocumentFolder:
    id: UUID
    area_id: UUID
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Document:
    """Index entry of one object in the bucket; search runs on these fields."""

    id: UUID
    area_id: UUID
    folder_id: UUID
    name: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    description: str | None
    tags: list[str]
    uploaded_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class TagColor:
    """Color of a tag inside an area; every document of the area shows it."""

    area_id: UUID
    tag: str
    color: str
    updated_by: str
    updated_at: datetime


def clean_text(value: str, field: str, max_length: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidInputError(f"El {field} no puede estar vacío")
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El {field} admite máximo {max_length} caracteres")
    return cleaned


def normalize_tags(raw: list[str] | str | None) -> list[str]:
    """Lowercase, deduplicated tags in the order given.

    Accepts a list or comma-separated text (the multipart form sends text).
    """

    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else raw
    tags: list[str] = []
    for item in items:
        tag = " ".join(str(item).split()).lower()
        if not tag or tag in tags:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise InvalidInputError(f"Cada etiqueta admite máximo {MAX_TAG_LENGTH} caracteres")
        tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise InvalidInputError(f"Máximo {MAX_TAGS} etiquetas por documento")
    return tags


def normalize_tag(raw: str) -> str:
    """One tag under the same rules as ``normalize_tags``, so colors match them."""

    tags = normalize_tags([raw])
    if not tags:
        raise InvalidInputError("La etiqueta no puede estar vacía")
    return tags[0]


def normalize_color(raw: str) -> str:
    """Lowercase ``#rrggbb``; shorthand and named colors are rejected."""

    color = raw.strip().lower()
    if not _HEX_COLOR.fullmatch(color):
        raise InvalidInputError("El color debe ser hexadecimal de seis dígitos, p. ej. #1d4ed8")
    return color


def split_filename(filename: str | None) -> tuple[str, str]:
    """Return (base name without client path, lowercase extension)."""

    base = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    if not base or "." not in base.strip("."):
        raise InvalidInputError("El archivo debe tener nombre y extensión")
    return base[:255], base.rsplit(".", 1)[1].lower()


def content_type_for(extension: str) -> str:
    return (
        _CONTENT_TYPES.get(extension)
        or mimetypes.types_map.get(f".{extension}")
        or "application/octet-stream"
    )


def storage_key_for(area_id: UUID, folder_id: UUID, document_id: UUID, extension: str) -> str:
    """Object key built from identifiers only: renaming never touches the bucket."""

    return f"areas/{area_id}/{folder_id}/{document_id}.{extension}"


def download_filename(document: Document) -> str:
    """Visible name plus its real extension, e.g. "Plantilla cartera.xlsx"."""

    if document.name.lower().endswith(f".{document.extension}"):
        return document.name
    return f"{document.name}.{document.extension}"
