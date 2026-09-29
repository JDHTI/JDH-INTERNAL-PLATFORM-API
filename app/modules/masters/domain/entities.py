"""Domain entities and validation for the shared master catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.errors import NotFoundError

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class MasterSummary:
    """A master and how many records it currently holds."""

    id: UUID
    slug: str
    name: str
    description: str | None
    source_system: str
    source_object: str | None
    grain: str | None
    natural_key: list[str]
    record_count: int
    last_synced_at: datetime | None


@dataclass
class MasterField:
    """One declared column of a master."""

    name: str
    label: str
    data_type: str
    role: str
    is_nullable: bool
    is_pii: bool
    references: str | None


@dataclass
class MasterDetail:
    """A master summary together with its declared structure."""

    summary: MasterSummary
    fields: list[MasterField] = field(default_factory=list)


@dataclass
class MasterRecord:
    """A normalized master record; source specifics live in ``attributes``."""

    id: UUID
    company_id: int | None
    code: str | None
    name: str | None
    is_active: bool | None
    source_key: str
    attributes: dict[str, Any]
    synced_at: datetime | None


@dataclass
class MasterRecordPage:
    """One page of records with the unpaged total."""

    items: list[MasterRecord]
    total: int
    offset: int
    limit: int


def clean_master_slug(value: str) -> str:
    """Normalize a master slug or raise not found when it cannot exist."""

    slug = value.strip().lower()
    if not slug or len(slug) > 80 or not _SLUG_PATTERN.fullmatch(slug):
        raise NotFoundError("Maestro no encontrado")
    return slug
