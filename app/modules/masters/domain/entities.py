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


def is_master_slug(value: str) -> bool:
    """Whether the text has the shape of a master slug."""

    return bool(value) and len(value) <= 80 and _SLUG_PATTERN.fullmatch(value) is not None


# --- Sync from the warehouse ---

# Identifiers come from ``master_definitions`` / ``master_fields``, so they are checked
# before they reach a SQL statement.
_SOURCE_OBJECT = re.compile(r"^[a-z_]+\.[a-z_0-9]+$")
_FIELD_NAME = re.compile(r"^[a-z_0-9]+$")
_SINGLE_ROLES = ("key", "company", "code", "name", "active")


@dataclass(frozen=True)
class MasterSpec:
    """How to read one master from the warehouse and where each column lands."""

    master_id: UUID
    slug: str
    schema: str
    relation: str
    # The one ``role="key"`` column: unique, used for paging and as ``source_key``.
    key: str
    company: str | None
    code: str | None
    name: str | None
    active: str | None
    # Every declared field, in display order; the ones without a role go to attributes.
    columns: tuple[str, ...]

    @property
    def attribute_columns(self) -> tuple[str, ...]:
        """Columns stored in ``attributes``."""

        mapped = {self.key, self.company, self.code, self.name, self.active}
        return tuple(column for column in self.columns if column not in mapped)


def build_spec(detail: MasterDetail) -> MasterSpec:
    """Check a master's declaration and turn it into a read plan.

    Args:
        detail (MasterDetail): The master with its fields.

    Returns:
        MasterSpec: The plan.

    Raises:
        ValueError: When the source or a field is not a plain identifier, or the
            master does not declare exactly one key field.
    """

    summary = detail.summary
    source = summary.source_object or ""
    if not _SOURCE_OBJECT.fullmatch(source):
        raise ValueError(f"El maestro {summary.slug} no tiene un origen válido: {source!r}")
    by_role: dict[str, list[str]] = {}
    for item in detail.fields:
        if not _FIELD_NAME.fullmatch(item.name):
            raise ValueError(f"El campo {item.name!r} de {summary.slug} no es un identificador")
        by_role.setdefault(item.role, []).append(item.name)
    for role in _SINGLE_ROLES:
        if len(by_role.get(role, [])) > 1:
            raise ValueError(f"El maestro {summary.slug} declara más de un campo {role}")
    if len(by_role.get("key", [])) != 1:
        raise ValueError(f"El maestro {summary.slug} debe declarar un campo clave")
    schema, relation = source.split(".", 1)

    def single(role: str) -> str | None:
        names = by_role.get(role)
        return names[0] if names else None

    return MasterSpec(
        master_id=summary.id,
        slug=summary.slug,
        schema=schema,
        relation=relation,
        key=by_role["key"][0],
        company=single("company"),
        code=single("code"),
        name=single("name"),
        active=single("active"),
        columns=tuple(item.name for item in detail.fields),
    )


@dataclass
class MasterSyncReport:
    """What one sync did to one master."""

    slug: str
    read: int = 0
    upserted: int = 0
    deactivated: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Set when the master could not be read or written; the others still sync.
    error: str | None = None

    def as_detail(self) -> dict[str, Any]:
        """A JSON-ready summary for run logs."""

        return {
            "slug": self.slug,
            "read": self.read,
            "upserted": self.upserted,
            "deactivated": self.deactivated,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }
