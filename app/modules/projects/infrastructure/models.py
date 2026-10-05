"""SQLAlchemy models owned by the projects module."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_COLORS = (
    "('default', 'gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red')"
)


class ProjectFolderModel(Base):
    __tablename__ = "project_folders"
    __table_args__ = (
        # Target of the composite FK that keeps a project in its folder's area.
        UniqueConstraint("area_id", "id", name="uq_project_folders_area_id_id"),
        Index("uq_project_folders_area_name_lower", "area_id", text("lower(name)"), unique=True),
        CheckConstraint(f"color IN {_COLORS}", name="ck_project_folders_color"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RESTRICT: an area cannot be deleted while it owns project folders.
    area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("areas.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(20), nullable=False, server_default="default")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectModel(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["area_id", "folder_id"],
            ["project_folders.area_id", "project_folders.id"],
            name="fk_projects_folder_same_area",
        ),
        Index("ix_projects_area_folder_position", "area_id", "folder_id", "position"),
        CheckConstraint(
            "status IN ('created', 'ideation', 'in_progress', 'on_hold', 'completed', 'cancelled')",
            name="ck_projects_status",
        ),
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_projects_priority"),
        CheckConstraint(f"color IN {_COLORS}", name="ck_projects_color"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_projects_end_after_start"
        ),
        CheckConstraint("is_archived = (archived_at IS NOT NULL)", name="ck_projects_archived_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    folder_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(20), nullable=False, server_default="default")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="created")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="medium")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    # Platform user id; resolved through the identity directory, never joined.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    position: Mapped[float] = mapped_column(Double, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectEpicModel(Base):
    __tablename__ = "project_epics"
    __table_args__ = (
        # Target of the composite FK that keeps a task in its epic's project.
        UniqueConstraint("project_id", "id", name="uq_project_epics_project_id_id"),
        Index("ix_project_epics_project_position", "project_id", "position"),
        CheckConstraint(f"color IN {_COLORS}", name="ck_project_epics_color"),
        CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
            name="ck_project_epics_end_after_start",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # CASCADE: deleting a project takes its epics with it.
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(20), nullable=False, server_default="default")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    position: Mapped[float] = mapped_column(Double, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectTaskModel(Base):
    __tablename__ = "project_tasks"
    __table_args__ = (
        # NO ACTION: an epic with tasks cannot be deleted, but a project delete
        # cascades to both tables and the check runs at the end of the statement.
        ForeignKeyConstraint(
            ["project_id", "epic_id"],
            ["project_epics.project_id", "project_epics.id"],
            name="fk_project_tasks_epic_same_project",
        ),
        Index("ix_project_tasks_project_status_position", "project_id", "status", "position"),
        Index("ix_project_tasks_epic_id", "epic_id"),
        CheckConstraint(
            "status IN ('backlog', 'in_progress', 'pending_validation', 'blocked', 'done')",
            name="ck_project_tasks_status",
        ),
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_project_tasks_priority"),
        CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
            name="ck_project_tasks_end_after_start",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    area_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Optional: with a NULL epic the composite FK is not checked (MATCH SIMPLE).
    epic_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="backlog")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="medium")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    position: Mapped[float] = mapped_column(Double, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
