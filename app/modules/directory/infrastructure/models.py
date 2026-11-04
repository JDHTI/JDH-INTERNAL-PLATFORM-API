"""Tables of the employee directory."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base

__all__ = [
    "DirectoryContactScheduleModel",
    "DirectoryEmployeeModel",
    "DirectorySiteModel",
    "DirectorySyncRunModel",
]

_TZ = DateTime(timezone=True)


class DirectorySiteModel(Base):
    """A physical location of the company."""

    __tablename__ = "directory_sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    address: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DirectoryEmployeeModel(Base):
    """A person of the directory, synced from Entra or added by hand."""

    __tablename__ = "directory_employees"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) = (entra_object_id IS NULL)",
            name="ck_directory_employees_identity_pair",
        ),
        CheckConstraint(
            "visibility IN ('auto', 'shown', 'hidden')",
            name="ck_directory_employees_visibility",
        ),
        Index("ix_directory_employees_site_id", "site_id"),
        Index("ix_directory_employees_department", "department"),
        Index("ix_directory_employees_manager_id", "manager_id"),
        Index("ix_directory_employees_hire_date", "hire_date"),
        Index("ix_directory_employees_birth_date", "birth_date"),
        Index("ix_directory_employees_full_name", "full_name"),
        Index("ix_directory_employees_public", "is_active", "visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    entra_object_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    job_title: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    department: Mapped[str | None] = mapped_column(String(200))
    office_location: Mapped[str | None] = mapped_column(String(200))
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("directory_sites.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    account_enabled: Mapped[bool | None] = mapped_column(Boolean)
    user_type: Mapped[str | None] = mapped_column(String(20))
    auto_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, server_default="auto")
    birth_date: Mapped[date | None] = mapped_column(Date)
    hire_date: Mapped[date | None] = mapped_column(Date)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("directory_employees.id", ondelete="SET NULL")
    )
    is_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    dwh_synced_at: Mapped[datetime | None] = mapped_column(_TZ)
    photo_key: Mapped[str | None] = mapped_column(String(500))
    photo_thumb_key: Mapped[str | None] = mapped_column(String(500))
    photo_etag: Mapped[str | None] = mapped_column(String(200))
    photo_content_type: Mapped[str | None] = mapped_column(String(100))
    photo_updated_at: Mapped[datetime | None] = mapped_column(_TZ)
    mobile_phone: Mapped[str | None] = mapped_column(String(30))
    landline_phone: Mapped[str | None] = mapped_column(String(30))
    show_mobile_in_directory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    synced_at: Mapped[datetime | None] = mapped_column(_TZ)
    created_at: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    site: Mapped[DirectorySiteModel | None] = relationship(lazy="raise")
    manager: Mapped[DirectoryEmployeeModel | None] = relationship(
        remote_side=[id], lazy="raise", foreign_keys=[manager_id]
    )


class DirectoryContactScheduleModel(Base):
    """One contact window of a person (weekday and time range)."""

    __tablename__ = "directory_contact_schedules"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "day", "start_time", name="uq_directory_contact_schedules_slot"
        ),
        CheckConstraint(
            "day IN ('LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES', 'SABADO', 'DOMINGO')",
            name="ck_directory_contact_schedules_day",
        ),
        CheckConstraint("end_time > start_time", name="ck_directory_contact_schedules_range"),
        Index("ix_directory_contact_schedules_employee_id", "employee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("directory_employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class DirectorySyncRunModel(Base):
    """One directory sync; a partial unique index keeps one running at most."""

    __tablename__ = "directory_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual', 'scheduled')", name="ck_directory_sync_runs_trigger"
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="ck_directory_sync_runs_status"
        ),
        Index("ix_directory_sync_runs_started_at", "started_at"),
        Index(
            "uq_directory_sync_runs_running",
            "status",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ)
    counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(String(320))
